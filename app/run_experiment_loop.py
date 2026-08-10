# -*- coding: utf-8 -*-
"""通过 OpenHarness HTTP API 自动跑完整的 LLM Skill 优化实验。

状态机（可中断、可恢复）：
  报告未齐 -> 附着当前生成任务 / 启动生成 / 仅重试失败 case
  报告齐但 Judge 未齐 -> 批量 Judge（服务端自动跳过已成功 case）
  Judge 齐 -> advance 生成 LLM 改写候选
  候选 -> 回到报告生成；Judge 完成后服务端自动 Gate 采纳/回滚
  optimizer_stop 命中 -> 正常退出

脚本不直接读写 Session state；所有业务变更均走现有 API。运行日志追加到
app/sessions/<sid>/automation.jsonl。文件锁防止同一会话同时跑两个自动驾驶。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


HERE = Path(__file__).resolve().parent
TERMINAL_GENERATION = {
    "completed",
    "partial",
    "failed",
    "cancelled",
    "interrupted",
}
RETRYABLE_HTTP = {408, 409, 429, 500, 502, 503, 504}


class LoopError(RuntimeError):
    """自动实验无法继续。"""


class TransientLoopError(LoopError):
    """网络/服务短暂不可用，可安全重试。"""


class APIError(LoopError):
    def __init__(self, status: int, message: str, payload=None):
        super().__init__("HTTP %s: %s" % (status, message))
        self.status = status
        self.payload = payload


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _safe_session_id(value: str) -> str:
    value = str(value or "").strip()
    if not value or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in value):
        raise ValueError("session id 只能包含字母、数字、-、_")
    return value


def _current_generation_jobs(generation: Dict[str, Any], version: str):
    return [
        item
        for item in (generation.get("jobs") or [])
        if item.get("skill_version") == version
    ]


def plan_next_action(
    state: Dict[str, Any],
    generation: Dict[str, Any],
    max_generation_jobs_per_version: int,
) -> Dict[str, Any]:
    """纯函数：根据服务端快照决定下一动作，便于测试与审计。"""
    stop = state.get("optimizer_stop") or {}
    if stop.get("stopped"):
        return {
            "action": "stop",
            "reason": stop.get("reason") or "已命中停止条件",
        }

    active = generation.get("job")
    if active and active.get("active"):
        return {
            "action": "wait_generation",
            "job_id": active["job_id"],
            "version": active.get("skill_version"),
        }

    progress = state.get("judge_progress") or {}
    missing_reports = list(progress.get("missing_report_case_ids") or [])
    version = state.get("current_version")
    jobs = _current_generation_jobs(generation, version)
    jobs.sort(key=lambda item: item.get("created_at") or 0, reverse=True)
    if missing_reports:
        if not jobs:
            return {
                "action": "start_generation",
                "version": version,
                "case_ids": missing_reports,
            }
        latest = jobs[0]
        if len(jobs) >= max_generation_jobs_per_version:
            return {
                "action": "blocked",
                "reason": (
                    "%s 已运行 %d 个生成任务，仍缺 %d 个 case，达到自动重试上限"
                    % (version, len(jobs), len(missing_reports))
                ),
                "missing_case_ids": missing_reports,
            }
        failed = list(latest.get("failed_case_ids") or [])
        if latest.get("terminal") and failed:
            return {
                "action": "retry_generation",
                "version": version,
                "job_id": latest["job_id"],
                "case_ids": failed,
                "attempt": len(jobs) + 1,
            }
        return {
            "action": "start_generation",
            "version": version,
            "case_ids": missing_reports,
        }

    if not progress.get("complete"):
        return {
            "action": "run_judge",
            "version": version,
            "pending_case_ids": list(
                progress.get("pending_judge_case_ids") or []
            ),
        }

    advance = (state.get("actions") or {}).get("advance") or {}
    if advance.get("enabled"):
        return {
            "action": "advance",
            "version": version,
        }
    return {
        "action": "blocked",
        "reason": advance.get("reason") or "当前状态不能推进",
    }


class ExperimentLoop:
    def __init__(
        self,
        base_url: str,
        session_id: str,
        poll_seconds: float = 10.0,
        request_timeout: float = 2400.0,
        max_generation_jobs_per_version: int = 3,
        max_judge_rounds: int = 3,
        max_optimizer_attempts: int = 3,
        generation_parallel: Optional[int] = None,
        judge_parallel: Optional[int] = None,
        max_settled_candidates: Optional[int] = None,
        log_path: Optional[Path] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.session_id = _safe_session_id(session_id)
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.request_timeout = max(30.0, float(request_timeout))
        self.max_generation_jobs_per_version = max(
            1,
            int(max_generation_jobs_per_version),
        )
        self.max_judge_rounds = max(1, int(max_judge_rounds))
        self.max_optimizer_attempts = max(
            1,
            int(max_optimizer_attempts),
        )
        self.generation_parallel = generation_parallel
        self.judge_parallel = judge_parallel
        self.max_settled_candidates = (
            None
            if max_settled_candidates is None
            else max(1, int(max_settled_candidates))
        )
        self.log_path = log_path
        self._last_generation_marker = None
        self._judge_rounds: Dict[str, int] = {}
        self._optimizer_attempts: Dict[str, int] = {}

    def emit(self, event: str, **payload):
        record = {
            "ts": time.time(),
            "time": _now_text(),
            "event": event,
            **payload,
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        print(line, flush=True)
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            with self.log_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    def api(
        self,
        path: str,
        method: str = "GET",
        payload: Optional[Dict[str, Any]] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        data = None
        headers = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=timeout or self.request_timeout,
            ) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = {"error": raw}
            raise APIError(
                exc.code,
                parsed.get("error") or raw,
                parsed,
            ) from exc
        except (
            urllib.error.URLError,
            TimeoutError,
            socket.timeout,
            OSError,
        ) as exc:
            raise TransientLoopError(
                "连接 OpenHarness 失败: %s" % exc
            ) from exc
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise LoopError("OpenHarness 返回了无效 JSON") from exc

    def session_state(self):
        query = urllib.parse.urlencode({"id": self.session_id})
        return self.api("/api/session?" + query)

    def generation_state(self, job_id=None):
        query = urllib.parse.urlencode(
            {"id": job_id}
            if job_id
            else {"session_id": self.session_id}
        )
        return self.api("/api/generation?" + query)

    def _wait_generation(self, job_id: str):
        last_heartbeat = 0.0
        while True:
            job = self.generation_state(job_id)
            marker = (
                job.get("status"),
                job.get("imported_count"),
                job.get("generated_count"),
                tuple(
                    sorted(
                        (
                            item.get("status"),
                            item.get("attempts"),
                        )
                        for item in (job.get("cases") or [])
                    )
                ),
            )
            now = time.time()
            if (
                marker != self._last_generation_marker
                or now - last_heartbeat >= 60
            ):
                self.emit(
                    "generation_progress",
                    job_id=job_id,
                    version=job.get("skill_version"),
                    status=job.get("status"),
                    imported=job.get("imported_count"),
                    total=job.get("case_count"),
                    failed=len(job.get("failed_case_ids") or []),
                    error=job.get("error"),
                )
                self._last_generation_marker = marker
                last_heartbeat = now
            if job.get("status") in TERMINAL_GENERATION:
                return job
            time.sleep(self.poll_seconds)

    def _start_generation(self, action):
        payload = {
            "id": self.session_id,
            "case_ids": action.get("case_ids"),
            "idempotency_key": "auto-start-%s-%s" % (
                self.session_id,
                action.get("version"),
            ),
        }
        if self.generation_parallel is not None:
            payload["parallel"] = self.generation_parallel
        result = self.api(
            "/api/generation/start",
            "POST",
            payload,
            timeout=60,
        )
        job = result["job"]
        self.emit(
            "generation_started",
            job_id=job["job_id"],
            version=job.get("skill_version"),
            cases=job.get("case_count"),
            reused=result.get("reused"),
        )

    def _retry_generation(self, action):
        payload = {
            "job_id": action["job_id"],
            "idempotency_key": "auto-retry-%s-%d"
            % (action["job_id"], action["attempt"]),
        }
        if self.generation_parallel is not None:
            payload["parallel"] = self.generation_parallel
        result = self.api(
            "/api/generation/retry",
            "POST",
            payload,
            timeout=60,
        )
        job = result["job"]
        self.emit(
            "generation_retry_started",
            parent_job_id=action["job_id"],
            job_id=job["job_id"],
            version=job.get("skill_version"),
            cases=job.get("case_count"),
            reused=result.get("reused"),
        )

    def _run_judge(self, action):
        version = action["version"]
        round_no = self._judge_rounds.get(version, 0) + 1
        if round_no > self.max_judge_rounds:
            raise LoopError(
                "%s Judge 连续 %d 轮仍未完成，达到自动重试上限"
                % (version, self.max_judge_rounds)
            )
        self._judge_rounds[version] = round_no
        payload = {
            "id": self.session_id,
            "version": version,
        }
        if self.judge_parallel is not None:
            payload["parallel"] = self.judge_parallel
        self.emit(
            "judge_started",
            version=version,
            round=round_no,
            pending=len(action.get("pending_case_ids") or []),
        )
        result = self.api(
            "/api/run_judge_batch",
            "POST",
            payload,
        )
        summary = result.get("summary") or {}
        self.emit(
            "judge_finished",
            version=version,
            round=round_no,
            status=summary.get("status"),
            judged=summary.get("judged_cases"),
            failed=summary.get("failed_cases"),
            remaining=summary.get("remaining_cases"),
            candidate_settled=summary.get("candidate_settled"),
        )

    def _advance(self, action):
        version = action["version"]
        attempt = self._optimizer_attempts.get(version, 0) + 1
        if attempt > self.max_optimizer_attempts:
            raise LoopError(
                "%s LLM 改写连续 %d 次未产出候选，达到自动重试上限"
                % (version, self.max_optimizer_attempts)
            )
        self._optimizer_attempts[version] = attempt
        self.emit(
            "optimizer_started",
            version=version,
            attempt=attempt,
        )
        result = self.api(
            "/api/advance",
            "POST",
            {"id": self.session_id},
        )
        advance = result.get("advance_result") or {}
        self.emit(
            "optimizer_finished",
            version=version,
            attempt=attempt,
            status=advance.get("status"),
            code=advance.get("code"),
            candidate=advance.get("version"),
            message=advance.get("message"),
        )
        status = advance.get("status")
        if status in {"proposed", "converged"}:
            # 这里的上限只约束“连续未产出候选”的 API/LLM 重试，
            # 不能把后续被 Judge 拒绝的正常候选也累计进去。候选一旦
            # 成功产出，下一轮从同一 best version 再改写时应重新计数。
            self._optimizer_attempts[version] = 0
            return
        if (
            status == "blocked"
            and advance.get("code") == "llm_rewrite_no_change"
        ):
            time.sleep(min(2 ** (attempt - 1), 8))
            return
        raise LoopError(
            "Optimizer 无法继续: %s"
            % (advance.get("message") or advance)
        )

    def run(self, once: bool = False):
        config = self.api("/api/generation/config")
        if not config.get("ready"):
            raise LoopError(
                "报告生成服务未就绪: %s" % config.get("error")
            )
        if self.generation_parallel is None:
            self.generation_parallel = int(config.get("parallel") or 1)
        if self.judge_parallel is None:
            self.judge_parallel = int(
                config.get("judge_parallel") or 1
            )
        self.emit(
            "automation_started",
            session_id=self.session_id,
            generation_parallel=self.generation_parallel,
            judge_parallel=self.judge_parallel,
            max_generation_jobs_per_version=(
                self.max_generation_jobs_per_version
            ),
            max_judge_rounds=self.max_judge_rounds,
            max_optimizer_attempts=self.max_optimizer_attempts,
        )

        transient_errors = 0
        while True:
            try:
                state = self.session_state()
                settled_candidates = [
                    item
                    for item in (state.get("versions") or [])
                    if item.get("version") != "v0"
                    and item.get("candidate_state")
                    in {"adopted", "rejected"}
                ]
                if (
                    self.max_settled_candidates is not None
                    and len(settled_candidates)
                    >= self.max_settled_candidates
                ):
                    self.emit(
                        "automation_completed",
                        reason="已完成指定数量的候选判定",
                        settled_candidates=len(settled_candidates),
                        current_version=state.get("current_version"),
                        best_version=state.get("best_version"),
                    )
                    return 0
                generation = self.generation_state()
                action = plan_next_action(
                    state,
                    generation,
                    self.max_generation_jobs_per_version,
                )
                self.emit(
                    "next_action",
                    current_version=state.get("current_version"),
                    best_version=state.get("best_version"),
                    overall=(
                        state.get("optimizer_stop") or {}
                    ).get("current_overall"),
                    no_improvement_streak=(
                        state.get("optimizer_stop") or {}
                    ).get("no_improvement_streak"),
                    **action,
                )
                if once:
                    return 0
                kind = action["action"]
                if kind == "stop":
                    self.emit(
                        "automation_completed",
                        current_version=state.get("current_version"),
                        best_version=state.get("best_version"),
                        optimizer_stop=state.get("optimizer_stop"),
                    )
                    return 0
                if kind == "blocked":
                    raise LoopError(action["reason"])
                if kind == "wait_generation":
                    self._wait_generation(action["job_id"])
                elif kind == "start_generation":
                    self._start_generation(action)
                elif kind == "retry_generation":
                    self._retry_generation(action)
                elif kind == "run_judge":
                    self._run_judge(action)
                elif kind == "advance":
                    self._advance(action)
                else:
                    raise LoopError("未知动作: %s" % kind)
                transient_errors = 0
            except (APIError, LoopError) as exc:
                retryable = (
                    isinstance(exc, TransientLoopError)
                    or (
                        isinstance(exc, APIError)
                        and exc.status in RETRYABLE_HTTP
                    )
                )
                transient_errors += 1
                if retryable and transient_errors <= 5:
                    delay = min(2 ** (transient_errors - 1), 20)
                    self.emit(
                        "transient_error",
                        error=str(exc),
                        retry_in_seconds=delay,
                        consecutive_errors=transient_errors,
                    )
                    time.sleep(delay)
                    continue
                self.emit(
                    "automation_blocked",
                    error=str(exc),
                    consecutive_errors=transient_errors,
                )
                return 2


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--session", required=True)
    parser.add_argument(
        "--base-url",
        default=os.environ.get(
            "OPENHARNESS_BASE_URL",
            "http://127.0.0.1:8080",
        ),
    )
    parser.add_argument("--poll-seconds", type=float, default=10)
    parser.add_argument("--request-timeout", type=float, default=2400)
    parser.add_argument(
        "--max-generation-jobs-per-version",
        type=int,
        default=3,
    )
    parser.add_argument("--max-judge-rounds", type=int, default=3)
    parser.add_argument(
        "--max-optimizer-attempts",
        type=int,
        default=3,
    )
    parser.add_argument("--generation-parallel", type=int)
    parser.add_argument("--judge-parallel", type=int)
    parser.add_argument(
        "--max-settled-candidates",
        type=int,
        help="指定数量的非 v0 候选完成 Gate 判定后退出。",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="只输出下一动作，不执行变更。",
    )
    args = parser.parse_args(argv)
    sid = _safe_session_id(args.session)
    session_dir = HERE / "sessions" / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    lock_path = session_dir / "automation.lock"
    log_path = session_dir / "automation.jsonl"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            print(
                "会话 %s 已有自动实验进程在运行。" % sid,
                file=sys.stderr,
            )
            return 3
        loop = ExperimentLoop(
            base_url=args.base_url,
            session_id=sid,
            poll_seconds=args.poll_seconds,
            request_timeout=args.request_timeout,
            max_generation_jobs_per_version=(
                args.max_generation_jobs_per_version
            ),
            max_judge_rounds=args.max_judge_rounds,
            max_optimizer_attempts=args.max_optimizer_attempts,
            generation_parallel=args.generation_parallel,
            judge_parallel=args.judge_parallel,
            max_settled_candidates=args.max_settled_candidates,
            log_path=log_path,
        )
        return loop.run(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
