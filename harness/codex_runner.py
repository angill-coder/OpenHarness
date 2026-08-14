# -*- coding: utf-8 -*-
"""OpenHarness 通过 Codex CLI 生成真实报告的 façade。

对上保持 ``ExternalRunRequest`` / ``ExternalBatchResult`` 契约不变，对下为
每个 case 建独立 workspace，复制冻结 Skill 与材料，并用一次无状态 Codex
``exec`` 重放该 case 的全部预设用户轮次。最终报告仍由独立 Artifact
Validator 验收；无有效报告时按 case 重试。
"""

from __future__ import annotations

import json
import hashlib
import os
import queue
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import uuid
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional, TextIO

from external_run_models import (
    ExternalAttemptResult,
    ExternalBatchResult,
    ExternalCaseResult,
    ExternalRunRequest,
)
from report_artifact import validate_report_artifact
from workbuddy_batch.adapter import structured_data_first_system_prompt
from workbuddy_batch.artifacts import (
    collect_artifacts,
    copy_inputs,
    snapshot_workspace,
    stage_skills,
)
from workbuddy_batch.dataset import load_cases
from workbuddy_batch.io import append_jsonl, write_json
from workbuddy_batch.models import CaseSpec
from workbuddy_runner import (
    ExternalRunConfigurationError,
    _batch_status,
    _normalize_cases,
    _notify_progress,
    _persist_result,
    _skill_name_from_path,
)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in value
    )
    return cleaned.strip("-") or "case"


def discover_command(explicit: str | None = None) -> tuple[str, ...]:
    """发现 Codex CLI；显式路径优先，其次读取统一环境变量。"""

    configured = (
        explicit
        or os.environ.get("OPENHARNESS_CODEX_CLI_PATH")
        or ""
    ).strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise FileNotFoundError("Codex CLI 不存在: %s" % path)
        return (str(path),)
    executable = shutil.which("codex")
    if not executable:
        raise FileNotFoundError(
            "找不到 Codex CLI。请安装 codex 或设置 "
            "OPENHARNESS_CODEX_CLI_PATH"
        )
    return (executable,)


def compile_execution_directive(
    case: CaseSpec,
    skill_name: str,
    request: ExternalRunRequest,
) -> str:
    contract = request.output_contract
    sections = [
        "OpenHarness automated Codex Runner constraints:",
        "1. 必须先完整读取并应用 `.codebuddy/skills/%s/SKILL.md`，以及该 "
        "Skill 直接引用的规则文件；不得替换为其他 Skill。" % skill_name,
        "2. 只能读取当前 case workspace 中的 Skill、structured data 与 "
        "materials；不得向上探索运行目录，也不得读取 OpenHarness 的人工报告、"
        "Rubric、Judge 结果或人工评分。",
        "3. 下方按顺序给出的是同一用户会话的预设轮次；所有已知回答都已经"
        "提供。不要继续追问，直接完成分析与交付。",
        "4. 最终报告必须完整写入 `%s`；文件非空且写入完成前不得宣布任务完成。"
        % contract.required_glob,
        "5. 最终回复只需简要说明完成状态和报告路径；报告正文以文件为准。",
    ]
    evidence_contract = structured_data_first_system_prompt(case)
    if evidence_contract:
        sections.extend(["", evidence_contract])
    return "\n".join(sections)


def compile_replay_prompt(
    case: CaseSpec,
    skill_name: str,
    request: ExternalRunRequest,
) -> str:
    """把预设多轮用户输入合为一次无状态 Codex 执行。"""

    sections = [compile_execution_directive(case, skill_name, request)]
    for index, interaction in enumerate(case.user_inputs, start=1):
        label = interaction.label or "turn_%d" % index
        sections.extend(
            [
                "",
                "[预设用户轮次 %d · %s]" % (index, label),
                interaction.input,
            ]
        )
    sections.extend(
        [
            "",
            "[执行要求]",
            "现在把以上轮次视为已经完成的同一段对话。立即使用指定 Skill 与"
            "当前 workspace 材料生成最终报告，并按路径契约落盘。",
        ]
    )
    return "\n".join(sections)


def build_command(
    command: tuple[str, ...],
    workspace: Path,
    output_path: Path,
    request: ExternalRunRequest,
    prompt: str,
) -> list[str]:
    args = [
        *command,
        "--sandbox",
        "workspace-write",
        "--ask-for-approval",
        "never",
    ]
    if request.model:
        args.extend(["--model", request.model])
    if request.effort:
        args.extend(
            [
                "--config",
                'model_reasoning_effort="%s"' % request.effort,
            ]
        )
    args.extend(
        [
            "--cd",
            str(workspace),
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-last-message",
            str(output_path),
            "--json",
            prompt,
        ]
    )
    return args


def _stop_process(
    process: subprocess.Popen[str],
    process_group_id: int | None,
) -> None:
    if os.name == "nt":
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        return
    try:
        os.killpg(process_group_id or process.pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process_group_id or process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _run_process(
    *,
    args: list[str],
    workspace: Path,
    trace_dir: Path,
    output_path: Path,
    request: ExternalRunRequest,
) -> dict:
    started = time.monotonic()
    process = subprocess.Popen(
        args,
        cwd=workspace,
        env={**os.environ, **request.environment},
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

    readers = [
        threading.Thread(
            target=read_stream,
            args=("stdout", process.stdout),
            daemon=True,
        ),
        threading.Thread(
            target=read_stream,
            args=("stderr", process.stderr),
            daemon=True,
        ),
    ]
    for reader in readers:
        reader.start()

    open_streams = {"stdout", "stderr"}
    last_activity = time.monotonic()
    timeout_kind = None
    thread_id = None
    usage = {}
    final_messages: list[str] = []
    operations = []
    stderr_tail: deque[str] = deque(maxlen=40)
    process_group_id = process.pid if os.name != "nt" else None

    while open_streams or process.poll() is None:
        now = time.monotonic()
        if timeout_kind is None and now - started >= request.timeout_seconds:
            timeout_kind = "case_timeout"
            _stop_process(process, process_group_id)
        elif (
            timeout_kind is None
            and now - last_activity >= request.stall_timeout_seconds
        ):
            timeout_kind = "stall_timeout"
            _stop_process(process, process_group_id)
        try:
            stream_name, line = output_queue.get(timeout=0.1)
        except queue.Empty:
            if timeout_kind and process.poll() is not None:
                break
            continue
        if line is None:
            open_streams.discard(stream_name)
            continue
        last_activity = time.monotonic()
        elapsed_ms = int((last_activity - started) * 1000)
        if stream_name == "stderr":
            stderr_tail.append(line)
            append_jsonl(
                trace_dir / "2_events.jsonl",
                {
                    "stream": "stderr",
                    "observed_at": _iso_now(),
                    "elapsed_ms": elapsed_ms,
                    "line": line,
                },
            )
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            event = {"type": "unparsed_stdout", "line": line}
        append_jsonl(
            trace_dir / "2_events.jsonl",
            {
                "stream": "stdout",
                "observed_at": _iso_now(),
                "elapsed_ms": elapsed_ms,
                "event": event,
            },
        )
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        if event.get("type") == "turn.completed" and isinstance(
            event.get("usage"), dict
        ):
            usage = event["usage"]
        item = event.get("item")
        if event.get("type") == "item.completed" and isinstance(item, dict):
            if item.get("type") == "agent_message" and item.get("text"):
                final_messages.append(str(item["text"]))
            elif item.get("type") in {"command_execution", "file_change"}:
                operations.append(
                    {
                        "name": str(item.get("type")),
                        "status": str(item.get("status") or "completed"),
                        "round_index": 0,
                        "duration_ms": None,
                        "input": item.get("command") or item.get("changes"),
                        "result": item.get("aggregated_output"),
                    }
                )

    return_code = process.wait()
    for reader in readers:
        reader.join(timeout=1)
    if process.stdout is not None and not readers[0].is_alive():
        process.stdout.close()
    if process.stderr is not None and not readers[1].is_alive():
        process.stderr.close()
    duration_ms = int((time.monotonic() - started) * 1000)
    final_output = ""
    if output_path.is_file():
        final_output = output_path.read_text(
            encoding="utf-8", errors="replace"
        ).strip()
    if not final_output and final_messages:
        final_output = final_messages[-1]
    if timeout_kind == "case_timeout":
        status = "timeout"
        error = "Codex case 总执行时间超过 %g 秒" % request.timeout_seconds
    elif timeout_kind == "stall_timeout":
        status = "stalled"
        error = "Codex 连续 %g 秒无输出" % request.stall_timeout_seconds
    elif return_code != 0:
        status = "cli_error"
        detail = "\n".join(stderr_tail)[-1200:]
        error = "Codex CLI 退出码 %d%s" % (
            return_code,
            (": " + detail) if detail else "",
        )
    else:
        status = "success"
        error = None
    write_json(trace_dir / "1_operations.json", operations)
    return {
        "status": status,
        "error": error,
        "return_code": return_code,
        "duration_ms": duration_ms,
        "final_output": final_output,
        "thread_id": str(thread_id) if thread_id else None,
        "usage": usage,
        "operations": operations,
    }


def _execute_case_attempt(
    case: CaseSpec,
    identity: Dict[str, str],
    attempt: int,
    request: ExternalRunRequest,
    generation_dir: Path,
    command: tuple[str, ...],
    skill_name: str,
) -> ExternalAttemptResult:
    run_id = "case-%s-a%02d" % (
        hashlib.sha256(case.case_id.encode("utf-8")).hexdigest()[:16],
        attempt,
    )
    run_dir = generation_dir / run_id
    case_dir = run_dir / "cases" / _safe_id(case.case_id)
    trace_dir = case_dir / "trace"
    # workspace 必须位于仓库之外，避免 Codex 沿父目录发现 OpenHarness 自身的
    # AGENTS.md/HANDOFF.md，把平台开发规则污染进被测报告生成上下文。
    workspace = Path(
        tempfile.mkdtemp(prefix="openharness-codex-runner-")
    )
    started = time.monotonic()
    try:
        copy_inputs(case.input_files, workspace)
        stage_skills((request.skill_path,), workspace)
        before = snapshot_workspace(workspace)
        prompt = compile_replay_prompt(case, skill_name, request)
        round_dir = trace_dir / "rounds" / "00_replay"
        round_dir.mkdir(parents=True, exist_ok=True)
        output_path = round_dir / "last-message.txt"
        args = build_command(
            command,
            workspace,
            output_path,
            request,
            prompt,
        )
        write_json(
            case_dir / "case.json",
            {
                "session_id": None,
                "provider": "codex",
                "workspace_isolated": True,
                "started_at": _iso_now(),
                "case": case.to_dict(),
            },
        )
        write_json(
            round_dir / "request.json",
            {
                "round_index": 0,
                "label": "combined_replay",
                "source_turn_count": len(case.user_inputs),
                "prompt_sha256": hashlib.sha256(
                    prompt.encode("utf-8")
                ).hexdigest(),
                "command": [
                    *args[:-1],
                    "<prompt omitted>",
                ],
            },
        )
        result = _run_process(
            args=args,
            workspace=workspace,
            trace_dir=trace_dir,
            output_path=output_path,
            request=request,
        )
        write_json(
            round_dir / "result.json",
            {
                "round_index": 0,
                "label": "combined_replay",
                **{
                    key: value
                    for key, value in result.items()
                    if key != "operations"
                },
                "observed_models": [request.model] if request.model else [],
            },
        )
        manifest = collect_artifacts(
            workspace,
            before,
            case_dir / "artifacts",
            (request.output_contract.required_glob,),
        )
        write_json(case_dir / "artifacts" / "manifest.json", manifest)
        validation = validate_report_artifact(
            case_dir,
            request.output_contract,
        )
        warning = None
        if validation.valid and result["status"] != "success":
            warning = (
                "Codex status=%s，但报告已通过 Artifact Validator"
                % result["status"]
            )
        error = None
        if not validation.valid:
            error = " | ".join(
                str(item)
                for item in (result.get("error"), validation.error)
                if item
            ) or "未产出有效报告"
        conversation = [
            "# Codex Runner conversation",
            "",
            "- provider: `codex`",
            "- model: `%s`" % (request.model or "default"),
            "- reasoning_effort: `%s`" % (request.effort or "default"),
            "",
        ]
        for index, interaction in enumerate(case.user_inputs, start=1):
            conversation.extend(
                [
                    "## 用户轮次 %d · %s" % (
                        index,
                        interaction.label or "turn",
                    ),
                    "",
                    interaction.input,
                    "",
                ]
            )
        conversation.extend(
            ["## Codex 最终回复", "", result.get("final_output") or ""]
        )
        (case_dir / "conversation.md").write_text(
            "\n".join(conversation) + "\n",
            encoding="utf-8",
        )
        return ExternalAttemptResult(
            attempt=attempt,
            max_attempts=request.max_attempts,
            wb_case_id=case.case_id,
            openharness_case_id=identity["openharness_case_id"],
            status="generated" if validation.valid else validation.status,
            wb_status=result["status"],
            wb_run_id=run_id,
            wb_session_id=result.get("thread_id"),
            run_dir=str(run_dir),
            trace_path=str(trace_dir),
            manifest_path=str(case_dir / "artifacts" / "manifest.json"),
            duration_ms=result.get("duration_ms"),
            configured_model=request.model,
            observed_models=(request.model,) if request.model else (),
            usage=result.get("usage") or {},
            error=error,
            warning=warning,
            report=validation.report,
        )
    except Exception as exc:
        return ExternalAttemptResult(
            attempt=attempt,
            max_attempts=request.max_attempts,
            wb_case_id=case.case_id,
            openharness_case_id=identity["openharness_case_id"],
            status="harness_error",
            wb_status="harness_error",
            wb_run_id=run_id,
            wb_session_id=None,
            run_dir=str(run_dir),
            trace_path=str(trace_dir),
            manifest_path=str(case_dir / "artifacts" / "manifest.json"),
            duration_ms=int((time.monotonic() - started) * 1000),
            configured_model=request.model,
            observed_models=(),
            usage={},
            error="%s: %s" % (type(exc).__name__, exc),
        )
    finally:
        try:
            if workspace.exists():
                shutil.rmtree(workspace)
        except OSError:
            pass


def _snapshot_result(
    generation_id: str,
    request: ExternalRunRequest,
    generation_dir: Path,
    started_at: str,
    case_results: Dict[str, ExternalCaseResult],
) -> ExternalBatchResult:
    return ExternalBatchResult(
        generation_id=generation_id,
        session_id=request.session_id,
        skill_version=request.skill_version,
        status=_batch_status(list(case_results.values())),
        output_dir=str(generation_dir),
        created_at=started_at,
        finished_at=_iso_now(),
        cases=list(case_results.values()),
    )


def run_external_cases(
    request: ExternalRunRequest,
    progress_callback: Optional[
        Callable[[ExternalBatchResult], None]
    ] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> ExternalBatchResult:
    case_file = request.case_file.expanduser().resolve()
    if not case_file.is_file():
        raise ExternalRunConfigurationError(
            "dataset 不存在或不是文件: %s" % case_file
        )
    if request.auto_judge:
        raise ExternalRunConfigurationError(
            "报告 Runner 不接受 auto_judge=True"
        )
    if not request.skill_path:
        raise ExternalRunConfigurationError(
            "Codex Runner 必须使用冻结的 skill_path"
        )
    skill_path = request.skill_path.expanduser().resolve()
    skill_name = _skill_name_from_path(skill_path)
    request = replace(request, case_file=case_file, skill_path=skill_path)
    command = request.command or discover_command()
    try:
        raw_cases = load_cases(case_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ExternalRunConfigurationError(
            "dataset 解析失败: %s" % exc
        ) from exc
    directive = compile_execution_directive(
        raw_cases[0] if raw_cases else CaseSpec("empty", ""),
        skill_name,
        request,
    )
    cases, identities = _normalize_cases(
        raw_cases,
        request,
        skill_name,
        execution_directive=directive,
        runner_name="Codex",
    )
    if request.openharness_case_ids:
        requested_ids = set(request.openharness_case_ids)
        available_ids = {
            item["openharness_case_id"] for item in identities.values()
        }
        missing_ids = sorted(requested_ids - available_ids)
        if missing_ids:
            raise ExternalRunConfigurationError(
                "dataset 缺少 OpenHarness case 映射: "
                + ", ".join(missing_ids)
            )
        cases = [
            case
            for case in cases
            if identities[case.case_id]["openharness_case_id"]
            in requested_ids
        ]
    if not cases:
        raise ExternalRunConfigurationError("没有可执行的 case")

    generation_id = "gen-codex-%s-%s" % (
        datetime.now().strftime("%Y%m%dT%H%M%S"),
        uuid.uuid4().hex[:8],
    )
    generation_dir = request.output_root.expanduser().resolve() / generation_id
    generation_dir.mkdir(parents=True, exist_ok=False)
    started_at = _iso_now()
    write_json(
        generation_dir / "request.json",
        {
            **request.to_dict(),
            "provider": "codex",
            "generation_id": generation_id,
            "effective_command": list(command),
            "effective_skill_name": skill_name,
            "created_at": started_at,
        },
    )

    case_results = {
        case.case_id: ExternalCaseResult(
            wb_case_id=case.case_id,
            openharness_case_id=identities[case.case_id][
                "openharness_case_id"
            ],
            split=identities[case.case_id]["split"],
        )
        for case in cases
    }
    case_by_id = {case.case_id: case for case in cases}
    initial = _snapshot_result(
        generation_id,
        request,
        generation_dir,
        started_at,
        case_results,
    )
    _persist_result(generation_dir, initial)
    _notify_progress(progress_callback, initial)

    ready = deque((case_id, 1) for case_id in case_by_id)
    in_flight = {}
    with ThreadPoolExecutor(max_workers=request.parallel) as executor:
        while ready or in_flight:
            cancelled = bool(should_cancel and should_cancel())
            if cancelled:
                while ready:
                    case_id, _ = ready.popleft()
                    case_results[case_id].status = "cancelled"
            launched = False
            while (
                ready
                and len(in_flight) < request.parallel
                and not cancelled
            ):
                case_id, attempt = ready.popleft()
                case_results[case_id].status = "running"
                future = executor.submit(
                    _execute_case_attempt,
                    case_by_id[case_id],
                    identities[case_id],
                    attempt,
                    request,
                    generation_dir,
                    tuple(command),
                    skill_name,
                )
                in_flight[future] = (case_id, attempt)
                launched = True
            if launched:
                snapshot = _snapshot_result(
                    generation_id,
                    request,
                    generation_dir,
                    started_at,
                    case_results,
                )
                _persist_result(generation_dir, snapshot)
                _notify_progress(progress_callback, snapshot)
            if not in_flight:
                break
            completed, _ = wait(
                tuple(in_flight),
                return_when=FIRST_COMPLETED,
            )
            for future in completed:
                case_id, attempt = in_flight.pop(future)
                attempt_result = future.result()
                case_result = case_results[case_id]
                case_result.attempts.append(attempt_result)
                if attempt_result.has_valid_report:
                    case_result.status = "generated"
                    case_result.report = attempt_result.report
                elif should_cancel and should_cancel():
                    case_result.status = "cancelled"
                elif attempt < request.max_attempts:
                    case_result.status = "retrying"
                    ready.appendleft((case_id, attempt + 1))
                else:
                    case_result.status = "retry_exhausted"
                snapshot = _snapshot_result(
                    generation_id,
                    request,
                    generation_dir,
                    started_at,
                    case_results,
                )
                _persist_result(generation_dir, snapshot)
                _notify_progress(progress_callback, snapshot)

    final = _snapshot_result(
        generation_id,
        request,
        generation_dir,
        started_at,
        case_results,
    )
    _persist_result(generation_dir, final)
    _notify_progress(progress_callback, final)
    return final
