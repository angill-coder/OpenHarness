# -*- coding: utf-8 -*-
"""托管 rubrics 实验：单会话连续未改善后停止，必要时重开 v0。

默认口径：
- 当前会话计为第 1 个，最多运行 3 个会话；
- 每个会话跑到连续 3 个候选未刷新已采纳最佳 overall；
- 会话结束后，若历史最高 overall > 4.0，则不再重开；
- 否则用 LLM 从零起草新 v0，导入配置数据集并继续。
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from run_experiment_loop import APIError, ExperimentLoop, _safe_session_id


HERE = Path(__file__).resolve().parent
HARD_MAX_SESSIONS = 3


def _now_text() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _numbers(values: Iterable[Any]):
    for value in values:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            yield float(value)


def best_overall(state: Dict[str, Any]) -> Optional[float]:
    """从 curve、versions 与 optimizer_stop 中提取历史最高 overall。"""
    values = []
    for item in state.get("curve") or []:
        values.append((item.get("dev") or {}).get("overall"))
    for item in state.get("versions") or []:
        values.append((item.get("dev") or {}).get("overall"))
    stop = state.get("optimizer_stop") or {}
    values.extend([stop.get("best_overall"), stop.get("current_overall")])
    numeric = list(_numbers(values))
    return max(numeric) if numeric else None


class ManagedRubricsLoop:
    def __init__(
        self,
        *,
        base_url: str,
        initial_session: str,
        max_sessions: int,
        max_regressions: int,
        target: float,
        generation_parallel: int,
        judge_parallel: int,
        generation_model: str,
        llm_backend: str,
        llm_model: str,
        llm_reasoning_effort: str,
        poll_seconds: float,
        request_timeout: float,
        log_path: Path,
    ):
        self.base_url = base_url.rstrip("/")
        self.initial_session = _safe_session_id(initial_session)
        self.max_sessions = min(
            HARD_MAX_SESSIONS,
            max(1, int(max_sessions)),
        )
        self.max_regressions = max(1, int(max_regressions))
        self.target = float(target)
        self.generation_parallel = max(1, int(generation_parallel))
        self.judge_parallel = max(1, int(judge_parallel))
        self.generation_model = generation_model
        self.llm_backend = llm_backend
        self.llm_model = llm_model
        self.llm_reasoning_effort = llm_reasoning_effort
        self.poll_seconds = max(1.0, float(poll_seconds))
        self.request_timeout = max(30.0, float(request_timeout))
        self.log_path = log_path

    def emit(self, event: str, **payload):
        record = {
            "ts": time.time(),
            "time": _now_text(),
            "event": event,
            **payload,
        }
        line = json.dumps(record, ensure_ascii=False, sort_keys=True)
        print(line, flush=True)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")

    def _loop(self, session_id: str, log_path: Optional[Path] = None):
        return ExperimentLoop(
            base_url=self.base_url,
            session_id=session_id,
            poll_seconds=self.poll_seconds,
            request_timeout=self.request_timeout,
            max_generation_jobs_per_version=3,
            max_judge_rounds=3,
            max_optimizer_attempts=3,
            generation_parallel=self.generation_parallel,
            judge_parallel=self.judge_parallel,
            max_no_improvement_streak=self.max_regressions,
            generation_model=self.generation_model,
            llm_backend=self.llm_backend,
            llm_model=self.llm_model,
            llm_reasoning_effort=self.llm_reasoning_effort,
            log_path=log_path,
        )

    def _state(self, session_id: str):
        loop = self._loop(session_id)
        query = urllib.parse.urlencode({"id": session_id})
        return loop.api("/api/session?" + query)

    def _create_fresh_session(self, session_id: str, requirement: str):
        loop = self._loop(session_id)
        payload = {
            "session_id": session_id,
            "requirement": requirement,
            "product_id": "research_insight",
            "optimizer_mode": "llm_rewrite",
            "v0_strategy": "llm_scratch",
            "optimizer_stop": {},
            "llm_backend": self.llm_backend,
            "llm_model": self.llm_model,
            "llm_reasoning_effort": self.llm_reasoning_effort,
        }
        last_error = None
        for attempt in range(1, 4):
            self.emit(
                "fresh_v0_started",
                session_id=session_id,
                attempt=attempt,
                llm_backend=self.llm_backend,
                llm_model=self.llm_model,
                llm_reasoning_effort=self.llm_reasoning_effort,
            )
            try:
                state = loop.api("/api/session", "POST", payload)
                break
            except APIError as exc:
                last_error = exc
                if exc.status == 409:
                    state = self._state(session_id)
                    break
                if exc.status not in {408, 429, 500, 502, 503, 504}:
                    raise
                self.emit(
                    "fresh_v0_retry",
                    session_id=session_id,
                    attempt=attempt,
                    error=str(exc),
                )
                if attempt < 3:
                    time.sleep(min(5 * attempt, 15))
        else:
            raise last_error
        loop.api(
            "/api/data",
            "POST",
            {"id": session_id, "use_configured": True},
        )
        state = self._state(session_id)
        self.emit(
            "fresh_session_ready",
            session_id=session_id,
            current_version=state.get("current_version"),
            cases=len(state.get("data") or []),
        )
        return state

    def run(self) -> int:
        initial = self._state(self.initial_session)
        requirement = initial.get("requirement") or "面向总裁的研究汇报助手"
        session_ids = [self.initial_session]
        session_ids.extend(
            "%s-r%d" % (self.initial_session, index)
            for index in range(2, self.max_sessions + 1)
        )
        best_across_sessions = None
        self.emit(
            "managed_loop_started",
            initial_session=self.initial_session,
            max_sessions=self.max_sessions,
            max_regressions=self.max_regressions,
            target_exclusive=self.target,
            generation_parallel=self.generation_parallel,
            judge_parallel=self.judge_parallel,
            generation_model=self.generation_model,
            llm_backend=self.llm_backend,
            llm_model=self.llm_model,
            llm_reasoning_effort=self.llm_reasoning_effort,
        )

        for position, session_id in enumerate(session_ids, start=1):
            if position > 1:
                try:
                    state = self._state(session_id)
                    self.emit(
                        "fresh_session_reused",
                        session_id=session_id,
                        position=position,
                    )
                except APIError as exc:
                    if exc.status != 404:
                        raise
                    state = self._create_fresh_session(
                        session_id,
                        requirement,
                    )
            else:
                state = initial

            session_dir = HERE / "sessions" / session_id
            session_dir.mkdir(parents=True, exist_ok=True)
            self.emit(
                "session_loop_started",
                session_id=session_id,
                position=position,
                initial_best=best_overall(state),
            )
            result = self._loop(
                session_id,
                session_dir / "automation.jsonl",
            ).run()
            state = self._state(session_id)
            session_best = best_overall(state)
            if session_best is not None:
                best_across_sessions = (
                    session_best
                    if best_across_sessions is None
                    else max(best_across_sessions, session_best)
                )
            self.emit(
                "session_loop_finished",
                session_id=session_id,
                position=position,
                result=result,
                session_best=session_best,
                best_across_sessions=best_across_sessions,
                current_version=state.get("current_version"),
                best_version=state.get("best_version"),
                no_improvement_streak=(
                    state.get("optimizer_stop") or {}
                ).get("no_improvement_streak"),
            )
            if result != 0:
                self.emit(
                    "managed_loop_blocked",
                    session_id=session_id,
                    result=result,
                )
                return result
            if (
                best_across_sessions is not None
                and best_across_sessions > self.target
            ):
                self.emit(
                    "managed_loop_completed",
                    reason="历史最高分已超过目标",
                    sessions_run=position,
                    best_overall=best_across_sessions,
                )
                return 0
            if position < self.max_sessions:
                self.emit(
                    "next_session_required",
                    completed_session=session_id,
                    next_session=session_ids[position],
                    best_overall=best_across_sessions,
                    target_exclusive=self.target,
                )

        self.emit(
            "managed_loop_completed",
            reason="已达到最多 3 个会话，停止实验",
            sessions_run=self.max_sessions,
            best_overall=best_across_sessions,
        )
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--initial-session",
        default="rubrics-v2-0811",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get(
            "OPENHARNESS_BASE_URL",
            "http://127.0.0.1:18768",
        ),
    )
    parser.add_argument("--max-sessions", type=int, default=3)
    parser.add_argument("--max-regressions", type=int, default=3)
    parser.add_argument("--target", type=float, default=4.0)
    parser.add_argument("--generation-parallel", type=int, default=20)
    parser.add_argument("--judge-parallel", type=int, default=20)
    parser.add_argument(
        "--generation-model",
        default="deepseek-v4-pro-ioa",
    )
    parser.add_argument("--llm-backend", default="codex")
    parser.add_argument("--llm-model", default="gpt-5.6-sol")
    parser.add_argument("--llm-reasoning-effort", default="medium")
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--request-timeout", type=float, default=2400)
    args = parser.parse_args(argv)

    sid = _safe_session_id(args.initial_session)
    session_dir = HERE / "sessions" / sid
    session_dir.mkdir(parents=True, exist_ok=True)
    lock_path = session_dir / "managed_experiment.lock"
    log_path = session_dir / "managed_experiment.jsonl"
    with lock_path.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(
                lock.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            print("已有托管实验进程在运行。", file=sys.stderr)
            return 3
        runner = ManagedRubricsLoop(
            base_url=args.base_url,
            initial_session=sid,
            max_sessions=args.max_sessions,
            max_regressions=args.max_regressions,
            target=args.target,
            generation_parallel=args.generation_parallel,
            judge_parallel=args.judge_parallel,
            generation_model=args.generation_model,
            llm_backend=args.llm_backend,
            llm_model=args.llm_model,
            llm_reasoning_effort=args.llm_reasoning_effort,
            poll_seconds=args.poll_seconds,
            request_timeout=args.request_timeout,
            log_path=log_path,
        )
        try:
            return runner.run()
        except Exception as exc:
            runner.emit("managed_loop_failed", error=str(exc))
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
