from __future__ import annotations

import json
import os
import queue
import shutil
import signal
import subprocess
import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from .adapter import build_environment, build_round_command
from .artifacts import collect_artifacts, copy_inputs, snapshot_workspace, stage_skills
from .events import EventCollector, assistant_text
from .io import append_jsonl, sha256_text, write_json
from .markdown_report import render_case_markdown, render_run_markdown, write_markdown
from .models import BatchConfig, CaseSpec


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in value
    )
    return cleaned.strip("-") or "case"


class BatchRunner:
    def __init__(self, config: BatchConfig) -> None:
        self.config = config
        self._launch_lock = threading.Lock()
        self._progress_lock = threading.Lock()
        self._last_launch = 0.0
        self._started_cases = 0
        self._total_cases = 0

    def run(self, cases: list[CaseSpec], run_id: str | None = None) -> Path:
        cases = self._expand_repetitions(cases)
        self._validate_skills(cases)
        run_id = run_id or (
            f"{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        )
        run_dir = self.config.output_root / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        self._started_cases = 0
        self._total_cases = len(cases)
        self._progress(
            f"[批次启动] run={run_id} cases={len(cases)} "
            f"parallel={self.config.parallel} repetition={self.config.repetition} "
            f"case_timeout={self.config.timeout_seconds:g}s "
            f"stall_timeout={self.config.stall_timeout_seconds:g}s"
        )
        summaries: list[dict[str, Any]] = []
        with ThreadPoolExecutor(max_workers=self.config.parallel) as executor:
            future_map = {
                executor.submit(self._run_case, case, run_dir, run_id): case
                for case in cases
            }
            for future in as_completed(future_map):
                case = future_map[future]
                try:
                    summary = future.result()
                except Exception as exc:  # keep other cases running
                    summary = {
                        "case_id": case.case_id,
                        "status": "harness_error",
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    }
                    failed_case_dir = run_dir / "cases" / _safe(case.case_id)
                    failed_case_dir.mkdir(parents=True, exist_ok=True)
                summaries.append(summary)
                duration = summary.get("duration_ms")
                elapsed = f"{duration / 1000:.1f}s" if isinstance(duration, int) else "—"
                self._progress(
                    f"[已完成 {len(summaries)}/{len(cases)}] case={case.case_id} "
                    f"status={summary['status']} elapsed={elapsed}"
                )
        summaries.sort(key=lambda item: item["case_id"])
        status_counts = {
            status: sum(item.get("status") == status for item in summaries)
            for status in sorted({str(item.get("status")) for item in summaries})
        }
        write_json(
            run_dir / "results.json",
            {
                "run_id": run_id,
                "config": self.config.to_dict(),
                "status_counts": status_counts,
                "summaries": summaries,
            },
        )
        report_paths = {
            case.case_id: f"cases/{_safe(case.case_id)}/conversation.md"
            for case in cases
            if (run_dir / "cases" / _safe(case.case_id) / "conversation.md").exists()
        }
        write_markdown(
            run_dir / "results.md",
            render_run_markdown(
                run_id=run_id,
                summaries=summaries,
                report_paths=report_paths,
            ),
        )
        counts = ", ".join(
            f"{name}={count}" for name, count in status_counts.items()
        )
        self._progress(f"[批次完成] run={run_id} {counts}")
        return run_dir

    def _progress(self, message: str) -> None:
        with self._progress_lock:
            print(message, flush=True)

    def _case_started(self, case: CaseSpec) -> None:
        with self._progress_lock:
            self._started_cases += 1
            repetition = ""
            if case.metadata.get("repetition_total", 1) > 1:
                repetition = (
                    f" source={case.metadata.get('source_case_id')}"
                    f" repetition={case.metadata.get('repetition_index')}/"
                    f"{case.metadata.get('repetition_total')}"
                )
            print(
                f"[已启动 {self._started_cases}/{self._total_cases}] "
                f"case={case.case_id}{repetition}",
                flush=True,
            )

    def _expand_repetitions(self, cases: list[CaseSpec]) -> list[CaseSpec]:
        if self.config.repetition == 1:
            return cases
        width = max(3, len(str(self.config.repetition)))
        expanded: list[CaseSpec] = []
        for case in cases:
            for repetition_index in range(1, self.config.repetition + 1):
                expanded.append(
                    replace(
                        case,
                        case_id=(
                            f"{case.case_id}__rep_"
                            f"{repetition_index:0{width}d}"
                        ),
                        metadata={
                            **case.metadata,
                            "source_case_id": case.case_id,
                            "repetition_index": repetition_index,
                            "repetition_total": self.config.repetition,
                        },
                    )
                )
        return expanded

    def _validate_skills(self, cases: list[CaseSpec]) -> None:
        if not self.config.require_skill:
            return
        missing = [
            case.case_id
            for case in cases
            if not (
                self.config.skills
                or self.config.skill_paths
                or case.skills
                or case.skill_paths
            )
        ]
        if missing:
            preview = ", ".join(missing[:10])
            suffix = " ..." if len(missing) > 10 else ""
            raise ValueError(
                "以下案例没有指定 Skill: "
                f"{preview}{suffix}。请使用 --skill/--skill-path，或仅在 CLI/模型"
                "连通测试时显式添加 --allow-no-skill。"
            )

    def _run_case(self, case: CaseSpec, run_dir: Path, run_id: str) -> dict[str, Any]:
        self._case_started(case)
        started_at = _iso_now()
        case_started = time.monotonic()
        case_dir = run_dir / "cases" / _safe(case.case_id)
        trace_dir = case_dir / "trace"
        workspace = trace_dir / "workspace"
        workspace.mkdir(parents=True, exist_ok=False)
        copy_inputs(case.input_files, workspace)
        combined_skill_paths = (*self.config.skill_paths, *case.skill_paths)
        staged_names = stage_skills(combined_skill_paths, workspace)
        skills = tuple(dict.fromkeys((*self.config.skills, *case.skills, *staged_names)))
        plugin_dirs = tuple(dict.fromkeys((*self.config.plugin_dirs, *case.plugin_dirs)))
        for path in plugin_dirs:
            if not path.exists():
                raise FileNotFoundError(f"插件目录不存在: {path}")
        before = snapshot_workspace(workspace)
        session_id = f"wbb-{_safe(run_id)}-{_safe(case.case_id)}-{uuid.uuid4().hex[:6]}"[:120]
        write_json(
            case_dir / "case.json",
            {
                "session_id": session_id,
                "started_at": started_at,
                "case": case.to_dict(),
                "effective": {
                    "model": case.model or self.config.model,
                    "effort": case.effort or self.config.effort,
                    "skills": skills,
                    "plugin_dirs": [str(path) for path in plugin_dirs],
                    "permission_mode": "bypassPermissions",
                    "sandbox": None,
                    "timeout": {
                        "seconds": self.config.timeout_seconds,
                        "scope": "case",
                    },
                    "stall_timeout_seconds": self.config.stall_timeout_seconds,
                },
            },
        )
        collector = EventCollector(session_id)
        rounds: list[dict[str, Any]] = []
        status = "success"
        error: str | None = None
        artifact_globs = tuple(
            dict.fromkeys((*self.config.artifact_globs, *case.artifact_globs))
        )
        previous_round_snapshot = before
        for round_index, interaction in enumerate(case.user_inputs):
            result = self._run_round(
                case=case,
                workspace=workspace,
                case_dir=case_dir,
                case_started=case_started,
                session_id=session_id,
                round_index=round_index,
                prompt=interaction.input,
                label=interaction.label,
                skills=skills,
                plugin_dirs=plugin_dirs,
                collector=collector,
            )
            round_dir = (
                trace_dir
                / "rounds"
                / f"{round_index:02d}_{_safe(interaction.label)}"
            )
            round_manifest = collect_artifacts(
                workspace,
                previous_round_snapshot,
                round_dir / "artifacts",
                artifact_globs,
            )
            write_json(round_dir / "artifacts" / "manifest.json", round_manifest)
            result["artifact_count"] = sum(
                item.get("status") != "deleted" for item in round_manifest
            )
            result["artifact_manifest"] = str(
                (round_dir / "artifacts" / "manifest.json").relative_to(case_dir)
            )
            write_json(round_dir / "result.json", result)
            previous_round_snapshot = snapshot_workspace(workspace)
            rounds.append(result)
            if result["status"] != "success":
                status = result["status"]
                error = result.get("error")
                break
        manifest = collect_artifacts(
            workspace, before, case_dir / "artifacts", artifact_globs
        )
        write_json(case_dir / "artifacts" / "manifest.json", manifest)
        operations = collector.finalize_operations(
            int((time.monotonic() - case_started) * 1000),
            interrupted_by=(
                "timeout"
                if status == "timeout"
                else "stall_timeout" if status == "stalled" else None
            ),
        )
        write_json(trace_dir / "1_operations.json", operations)
        native_sessions = self._capture_native_session(session_id, case_dir)
        duration_ms = int((time.monotonic() - case_started) * 1000)
        summary = {
            "case_id": case.case_id,
            "source_case_id": case.metadata.get("source_case_id", case.case_id),
            "repetition_index": case.metadata.get("repetition_index", 1),
            "repetition_total": case.metadata.get("repetition_total", 1),
            "session_id": session_id,
            "configured_model": case.model or self.config.model,
            "observed_models": sorted(
                {
                    model
                    for item in rounds
                    for model in item.get("observed_models", [])
                }
            ),
            "status": status,
            "error": error,
            "started_at": started_at,
            "finished_at": _iso_now(),
            "duration_ms": duration_ms,
            "timeout_seconds": self.config.timeout_seconds,
            "stall_timeout_seconds": self.config.stall_timeout_seconds,
            "rounds_planned": len(case.user_inputs),
            "rounds_completed": sum(item["status"] == "success" for item in rounds),
            "rounds": rounds,
            "final_output": (
                rounds[-1].get("final_output", "") if rounds else ""
            ),
            "artifact_count": sum(item.get("status") != "deleted" for item in manifest),
            "operation_count": len(operations),
            "native_session_files": native_sessions,
        }
        write_markdown(
            case_dir / "conversation.md",
            render_case_markdown(
                case=case,
                summary=summary,
                assistant_messages=collector.assistant_messages,
                operations=operations,
                model=case.model or self.config.model,
                skills=skills,
            ),
        )
        return summary

    def _wait_for_launch_slot(self) -> None:
        if not self.config.launch_interval_seconds:
            return
        with self._launch_lock:
            now = time.monotonic()
            wait_for = self.config.launch_interval_seconds - (now - self._last_launch)
            if wait_for > 0:
                time.sleep(wait_for)
            self._last_launch = time.monotonic()

    def _run_round(
        self,
        *,
        case: CaseSpec,
        workspace: Path,
        case_dir: Path,
        case_started: float,
        session_id: str,
        round_index: int,
        prompt: str,
        label: str,
        skills: tuple[str, ...],
        plugin_dirs: tuple[Path, ...],
        collector: EventCollector,
    ) -> dict[str, Any]:
        round_dir = (
            case_dir / "trace" / "rounds" / f"{round_index:02d}_{_safe(label)}"
        )
        round_dir.mkdir(parents=True, exist_ok=True)
        command = build_round_command(
            self.config,
            case,
            session_id,
            round_index,
            prompt,
            skills,
        )
        metadata_command = [
            *command[:-1],
            f"<prompt sha256={sha256_text(prompt)}>",
        ]
        write_json(
            round_dir / "request.json",
            {
                "round_index": round_index,
                "label": label,
                "prompt": prompt,
                "command": metadata_command,
                "started_at": _iso_now(),
                "case_timeout_seconds": self.config.timeout_seconds,
                "stall_timeout_seconds": self.config.stall_timeout_seconds,
                "case_elapsed_before_round_ms": int(
                    (time.monotonic() - case_started) * 1000
                ),
            },
        )
        self._wait_for_launch_slot()
        total_rounds = len(case.user_inputs)
        self._progress(
            f"[轮次开始] case={case.case_id} round={round_index + 1}/{total_rounds} "
            f"label={label}"
        )
        # ``EventCollector`` spans the whole case, but a result event is scoped to
        # one CLI process. Reset it so a missing result in a resumed round cannot
        # be mistaken for the preceding round's successful result.
        collector.result = None
        round_started = time.monotonic()
        remaining_seconds = self.config.timeout_seconds - (
            round_started - case_started
        )
        if remaining_seconds <= 0:
            payload = self._timeout_before_round_payload(
                case=case,
                case_dir=case_dir,
                case_started=case_started,
                round_index=round_index,
                label=label,
                total_rounds=total_rounds,
            )
            write_json(round_dir / "result.json", payload)
            return payload
        process = subprocess.Popen(
            command,
            cwd=workspace,
            env=build_environment(self.config, plugin_dirs),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            start_new_session=(os.name != "nt"),
        )
        output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()

        def read_stream(name: str, stream: TextIO | None) -> None:
            if stream is None:
                output_queue.put((name, None))
                return
            try:
                for line in stream:
                    output_queue.put((name, line.rstrip("\r\n")))
            except (OSError, ValueError):
                pass
            finally:
                output_queue.put((name, None))

        threads = [
            threading.Thread(target=read_stream, args=("stdout", process.stdout), daemon=True),
            threading.Thread(target=read_stream, args=("stderr", process.stderr), daemon=True),
        ]
        for thread in threads:
            thread.start()
        open_streams = {"stdout", "stderr"}
        timed_out = False
        timeout_kind: str | None = None
        drain_deadline: float | None = None
        last_activity = time.monotonic()
        process_group_id = process.pid if os.name != "nt" else None
        parse_errors = 0
        with (round_dir / "stdout.jsonl").open("w", encoding="utf-8") as raw_stdout, (
            round_dir / "stderr.log"
        ).open("w", encoding="utf-8") as raw_stderr:
            while open_streams or process.poll() is None:
                case_elapsed_seconds = time.monotonic() - case_started
                if (
                    not timed_out
                    and case_elapsed_seconds >= self.config.timeout_seconds
                ):
                    timed_out = True
                    timeout_kind = "case_timeout"
                    timeout_event = {
                        "stream": "harness",
                        "round_index": round_index,
                        "observed_at": _iso_now(),
                        "case_elapsed_ms": int(case_elapsed_seconds * 1000),
                        "round_elapsed_ms": int(
                            (time.monotonic() - round_started) * 1000
                        ),
                        "event": {
                            "type": "harness",
                            "subtype": "case_timeout",
                            "timeout_seconds": self.config.timeout_seconds,
                            "message": "case 总执行时间达到上限，终止 WorkBuddy 进程组",
                        },
                    }
                    append_jsonl(
                        case_dir / "trace" / "2_events.jsonl", timeout_event
                    )
                    self._progress(
                        f"[超时截断] case={case.case_id} "
                        f"round={round_index + 1}/{total_rounds} "
                        f"limit={self.config.timeout_seconds:g}s"
                    )
                    self._stop_process(process, process_group_id)
                    # A misbehaving descendant may outlive the CLI and keep an
                    # inherited stdout/stderr pipe open. Do not let trace drain
                    # defeat the case timeout indefinitely.
                    drain_deadline = time.monotonic() + 2
                if (
                    not timed_out
                    and time.monotonic() - last_activity
                    >= self.config.stall_timeout_seconds
                ):
                    timed_out = True
                    timeout_kind = "stall_timeout"
                    stall_event = {
                        "stream": "harness",
                        "round_index": round_index,
                        "observed_at": _iso_now(),
                        "case_elapsed_ms": int(case_elapsed_seconds * 1000),
                        "round_elapsed_ms": int(
                            (time.monotonic() - round_started) * 1000
                        ),
                        "event": {
                            "type": "harness",
                            "subtype": "stall_timeout",
                            "timeout_seconds": self.config.stall_timeout_seconds,
                            "message": (
                                "WorkBuddy 连续无 stdout/stderr，终止进程组"
                            ),
                        },
                    }
                    append_jsonl(
                        case_dir / "trace" / "2_events.jsonl", stall_event
                    )
                    self._progress(
                        f"[无响应截断] case={case.case_id} "
                        f"round={round_index + 1}/{total_rounds} "
                        f"limit={self.config.stall_timeout_seconds:g}s"
                    )
                    self._stop_process(process, process_group_id)
                    drain_deadline = time.monotonic() + 2
                if (
                    timed_out
                    and drain_deadline is not None
                    and time.monotonic() >= drain_deadline
                    and output_queue.empty()
                ):
                    break
                try:
                    stream_name, line = output_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                if line is None:
                    open_streams.discard(stream_name)
                    continue
                last_activity = time.monotonic()
                observed_at = _iso_now()
                case_elapsed_ms = int((time.monotonic() - case_started) * 1000)
                round_elapsed_ms = int((time.monotonic() - round_started) * 1000)
                if stream_name == "stderr":
                    raw_stderr.write(line + "\n")
                    raw_stderr.flush()
                    append_jsonl(
                        case_dir / "trace" / "2_events.jsonl",
                        {
                            "stream": "stderr",
                            "round_index": round_index,
                            "observed_at": observed_at,
                            "case_elapsed_ms": case_elapsed_ms,
                            "round_elapsed_ms": round_elapsed_ms,
                            "line": line,
                        },
                    )
                    continue
                raw_stdout.write(line + "\n")
                raw_stdout.flush()
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    parse_errors += 1
                    event = {"type": "unparsed_stdout", "line": line}
                envelope = {
                    "stream": "stdout",
                    "round_index": round_index,
                    "observed_at": observed_at,
                    "case_elapsed_ms": case_elapsed_ms,
                    "round_elapsed_ms": round_elapsed_ms,
                    "event": event,
                }
                append_jsonl(case_dir / "trace" / "2_events.jsonl", envelope)
                collector.consume(event, case_elapsed_ms, round_index)
        return_code = process.wait()
        for thread in threads:
            thread.join(timeout=1)
        if process.stdout is not None and not threads[0].is_alive():
            process.stdout.close()
        if process.stderr is not None and not threads[1].is_alive():
            process.stderr.close()
        duration_ms = int((time.monotonic() - round_started) * 1000)
        result_event = collector.result
        if timeout_kind == "case_timeout":
            status = "timeout"
            error = (
                f"case 总执行时间超过 {self.config.timeout_seconds:g} 秒，"
                f"截断于第 {round_index + 1} 轮"
            )
        elif timeout_kind == "stall_timeout":
            status = "stalled"
            error = (
                f"WorkBuddy 连续 {self.config.stall_timeout_seconds:g} 秒无输出，"
                f"截断于第 {round_index + 1} 轮"
            )
        elif return_code != 0:
            status = "cli_error"
            error = f"WorkBuddy CLI 退出码 {return_code}"
        elif not result_event:
            status = "protocol_error"
            error = "CLI 未输出 result 事件"
        elif result_event.get("is_error") or result_event.get("subtype") != "success":
            status = "agent_error"
            error = str(result_event.get("result") or result_event.get("subtype"))
        else:
            status = "success"
            error = None
        final_output = (
            str(result_event.get("result") or "")
            if result_event
            else assistant_text(collector.assistant_messages, round_index)
        )
        payload = {
            "round_index": round_index,
            "label": label,
            "status": status,
            "error": error,
            "return_code": return_code,
            "duration_ms": duration_ms,
            "parse_errors": parse_errors,
            "final_output": final_output,
            "observed_models": collector.observed_models(round_index),
            "usage": (
                result_event.get("usage")
                if result_event and isinstance(result_event.get("usage"), dict)
                else {}
            ),
            "result_event": result_event,
        }
        write_json(round_dir / "result.json", payload)
        self._progress(
            f"[轮次完成] case={case.case_id} round={round_index + 1}/{total_rounds} "
            f"status={status} elapsed={duration_ms / 1000:.1f}s"
        )
        return payload

    def _timeout_before_round_payload(
        self,
        *,
        case: CaseSpec,
        case_dir: Path,
        case_started: float,
        round_index: int,
        label: str,
        total_rounds: int,
    ) -> dict[str, Any]:
        case_elapsed_ms = int((time.monotonic() - case_started) * 1000)
        error = (
            f"case 总执行时间超过 {self.config.timeout_seconds:g} 秒，"
            f"未启动第 {round_index + 1} 轮"
        )
        append_jsonl(
            case_dir / "trace" / "2_events.jsonl",
            {
                "stream": "harness",
                "round_index": round_index,
                "observed_at": _iso_now(),
                "case_elapsed_ms": case_elapsed_ms,
                "round_elapsed_ms": 0,
                "event": {
                    "type": "harness",
                    "subtype": "case_timeout",
                    "timeout_seconds": self.config.timeout_seconds,
                    "message": error,
                },
            },
        )
        payload = {
            "round_index": round_index,
            "label": label,
            "status": "timeout",
            "error": error,
            "return_code": None,
            "duration_ms": 0,
            "parse_errors": 0,
            "final_output": "",
            "observed_models": [],
            "usage": {},
            "result_event": None,
        }
        self._progress(
            f"[超时截断] case={case.case_id} "
            f"round={round_index + 1}/{total_rounds} "
            f"limit={self.config.timeout_seconds:g}s"
        )
        self._progress(
            f"[轮次完成] case={case.case_id} "
            f"round={round_index + 1}/{total_rounds} status=timeout elapsed=0.0s"
        )
        return payload

    @staticmethod
    def _stop_process(
        process: subprocess.Popen[str], process_group_id: int | None = None
    ) -> None:
        """Stop the CLI and lingering descendants that may still hold trace pipes."""

        if os.name == "nt":
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
            return

        pgid = process_group_id or process.pid
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass

        grace_deadline = time.monotonic() + 5
        while time.monotonic() < grace_deadline:
            # Reap the CLI promptly. Otherwise a terminated parent can remain a
            # zombie and make the process group appear alive for the full grace.
            process.poll()
            try:
                os.killpg(pgid, 0)
            except (ProcessLookupError, PermissionError):
                break
            time.sleep(0.05)
        else:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass

        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    def _capture_native_session(self, session_id: str, case_dir: Path) -> list[str]:
        home = self.config.workbuddy_home
        if not self.config.capture_native_session or not home or not home.exists():
            return []
        matches: list[Path] = []
        for root_name in ("projects", "sessions"):
            root = home / root_name
            if root.exists():
                matches.extend(root.rglob(f"{session_id}*.jsonl"))
        copied: list[str] = []
        destination = case_dir / "trace" / "native_session"
        for index, source in enumerate(sorted(set(matches))):
            target = destination / f"{index:02d}_{source.name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(source, target)
            except OSError:
                continue
            copied.append(str(target.relative_to(case_dir)))
        return copied
