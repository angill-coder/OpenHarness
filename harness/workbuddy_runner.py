# -*- coding: utf-8 -*-
"""OpenHarness 调用 WorkBuddy 的唯一 façade。

本模块拥有任务预检、配置归一化、报告验收和条件重试。调用方不应直接
依赖 ``workbuddy_batch`` 的内部模块。
"""

from __future__ import annotations

import json
import re
import uuid
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

from external_run_models import (
    ExternalAttemptResult,
    ExternalBatchResult,
    ExternalCaseResult,
    ExternalRunRequest,
    ReportOutputContract,
)
from report_artifact import validate_report_artifact
from workbuddy_batch.adapter import discover_command
from workbuddy_batch.dataset import load_cases
from workbuddy_batch.io import sha256_text, write_json
from workbuddy_batch.models import BatchConfig, CaseSpec, Interaction
from workbuddy_batch.runner import BatchRunner


class ExternalRunConfigurationError(ValueError):
    """启动外部任务前即可确定、无需自动重试的配置错误。"""


_FORBIDDEN_GENERATION_KEYS = {
    "ground_truth",
    "ground_truth_findings",
    "expected_insights",
    "supported_claims",
    "key_claim_ids",
    "noise_source_ids",
    "human_label",
    "human_labels",
    "human_scores",
    "judge_checks",
    "judge_reasoning",
    "report_judgments",
}


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_id(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in "-_" else "-"
        for character in value
    )
    return cleaned.strip("-") or "case"


def _inside(path: Path, roots: Iterable[Path]) -> bool:
    resolved = path.expanduser().resolve()
    for root in roots:
        allowed = root.expanduser().resolve()
        if resolved == allowed:
            return True
        try:
            resolved.relative_to(allowed)
            return True
        except ValueError:
            continue
    return False


def _find_forbidden_generation_key(value, prefix: str = "data") -> Optional[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key)
            path = f"{prefix}.{name}"
            if name in _FORBIDDEN_GENERATION_KEYS:
                return path
            nested = _find_forbidden_generation_key(item, path)
            if nested:
                return nested
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            nested = _find_forbidden_generation_key(
                item,
                f"{prefix}[{index}]",
            )
            if nested:
                return nested
    return None


def _skill_name_from_path(path: Path) -> str:
    skill_file = path / "SKILL.md" if path.is_dir() else path
    if not skill_file.is_file():
        raise ExternalRunConfigurationError(
            f"Skill 路径缺少 SKILL.md: {path}"
        )
    header = skill_file.read_text(
        encoding="utf-8", errors="replace"
    )[:4096]
    match = re.search(r"(?m)^name:\s*['\"]?([^'\"\n]+)", header)
    return (
        match.group(1).strip() if match else skill_file.parent.name
    ).replace("/", "-")


def compile_execution_directive(contract: ReportOutputContract) -> str:
    """把 OpenHarness 交付约束编译成不可由用户任意覆盖的 system prompt。"""

    return "\n".join(
        [
            "OpenHarness automated execution constraints:",
            "1. 必须显式加载并使用本次 workspace 中指定的 WorkBuddy Skill，"
            "不得替换为其他 Skill。",
            "2. 生成任务只能使用本 case workspace 中提供的材料；不得尝试读取"
            " OpenHarness ground truth、Rubric、Judge 结果或人工评分。",
            f"3. 最终报告必须写入 {contract.required_glob}。",
            "4. 报告文件完整写入且非空之前，不得宣布任务完成。",
            "5. 最终回复只需简要说明完成状态和报告路径；报告正文以文件为准。",
        ]
    )


def _delivery_suffix(contract: ReportOutputContract) -> str:
    return (
        "\n\n[OpenHarness 最终交付指令]\n"
        "这是本 case 最后一轮预设用户输入。不要继续追问，请从现在开始完成"
        f"全部分析，并将最终报告写入 {contract.required_glob}。"
        "确认文件完整落盘后再结束。"
    )


def _normalize_cases(
    cases: list[CaseSpec],
    request: ExternalRunRequest,
    requested_skill_name: str,
) -> tuple[list[CaseSpec], Dict[str, Dict[str, str]]]:
    roots = tuple(
        item.expanduser().resolve()
        for item in (
            request.allowed_material_roots
            or (request.case_file.expanduser().resolve().parent,)
        )
    )
    normalized: list[CaseSpec] = []
    identities: Dict[str, Dict[str, str]] = {}
    seen_openharness_ids: set[str] = set()
    suffix = _delivery_suffix(request.output_contract)

    for case in cases:
        metadata = dict(case.metadata)
        mapping = request.case_map.get(case.case_id, {})
        is_unified = (
            metadata.get("dataset_schema_version")
            == "openharness-wb/v1"
        )
        openharness_case_id = str(
            case.case_id
            if is_unified
            else (
                metadata.get("openharness_case_id")
                or mapping.get("openharness_case_id")
                or case.case_id
            )
        )
        split = str(metadata.get("split") or mapping.get("split") or "dev")
        if openharness_case_id in seen_openharness_ids:
            raise ExternalRunConfigurationError(
                f"多个 WB case 映射到同一 OpenHarness case: "
                f"{openharness_case_id}"
            )
        seen_openharness_ids.add(openharness_case_id)

        leaked_key = _find_forbidden_generation_key(case.data)
        if leaked_key:
            raise ExternalRunConfigurationError(
                f"case {case.case_id} 包含禁止发送给生成模型的字段: "
                f"{leaked_key}"
            )
        for item in case.input_files:
            source = item.source.expanduser().resolve()
            if not source.exists():
                raise ExternalRunConfigurationError(
                    f"case {case.case_id} 输入材料不存在: {source}"
                )
            if not _inside(source, roots):
                raise ExternalRunConfigurationError(
                    f"case {case.case_id} 输入材料不在允许目录: {source}"
                )
            target = Path(item.target) if item.target else None
            if target and (target.is_absolute() or ".." in target.parts):
                raise ExternalRunConfigurationError(
                    f"case {case.case_id} input_files.target 非法: "
                    f"{item.target}"
                )
        if case.plugin_dirs:
            raise ExternalRunConfigurationError(
                f"case {case.case_id} 不允许自行指定 plugin_dirs"
            )
        if case.skill_paths:
            raise ExternalRunConfigurationError(
                f"case {case.case_id} 不允许自行指定 skill_paths"
            )
        conflicting_skills = [
            item for item in case.skills if item != requested_skill_name
        ]
        if conflicting_skills:
            raise ExternalRunConfigurationError(
                f"case {case.case_id} 的 Skill {conflicting_skills} 与 Runner "
                f"Skill {requested_skill_name} 冲突"
            )
        if request.model and case.model and case.model != request.model:
            raise ExternalRunConfigurationError(
                f"case {case.case_id} model={case.model} 与 Runner "
                f"model={request.model} 冲突"
            )
        if request.effort and case.effort and case.effort != request.effort:
            raise ExternalRunConfigurationError(
                f"case {case.case_id} effort={case.effort} 与 Runner "
                f"effort={request.effort} 冲突"
            )
        conflicting_globs = [
            item
            for item in case.artifact_globs
            if item != request.output_contract.required_glob
        ]
        if conflicting_globs:
            raise ExternalRunConfigurationError(
                f"case {case.case_id} artifact_globs={conflicting_globs} "
                "与 Runner 输出契约冲突"
            )

        source_turns = case.to_dict()["turns"]
        if case.interactions:
            interactions = list(case.interactions)
            last = interactions[-1]
            interactions[-1] = replace(last, input=last.input + suffix)
            effective_prompt = case.prompt
            effective_interactions = tuple(interactions)
        else:
            effective_prompt = case.prompt + suffix
            effective_interactions = ()

        metadata.update(
            {
                "openharness_case_id": openharness_case_id,
                "split": split,
                "skill_version": request.skill_version,
                "source_turns": source_turns,
                "execution_directive_sha256": sha256_text(
                    compile_execution_directive(request.output_contract)
                ),
            }
        )
        normalized_case = replace(
            case,
            prompt=effective_prompt,
            interactions=effective_interactions,
            model=None if request.model else case.model,
            effort=None if request.effort else case.effort,
            skills=(),
            skill_paths=(),
            plugin_dirs=(),
            artifact_globs=(),
            metadata=metadata,
        )
        normalized.append(normalized_case)
        identities[case.case_id] = {
            "openharness_case_id": openharness_case_id,
            "split": split,
        }
    return normalized, identities


def _read_batch_summaries(run_dir: Path) -> Dict[str, dict]:
    path = run_dir / "results.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    summaries = payload.get("summaries", [])
    if not isinstance(summaries, list):
        return {}
    return {
        str(item["case_id"]): item
        for item in summaries
        if isinstance(item, dict) and item.get("case_id")
    }


def _read_wb_session_id(case_dir: Path) -> Optional[str]:
    path = case_dir / "case.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("session_id")
    return str(value) if value else None


def _usage(summary: dict) -> dict:
    return {
        "rounds": [
            {
                "round_index": item.get("round_index"),
                "usage": item.get("usage", {}),
            }
            for item in summary.get("rounds", [])
            if isinstance(item, dict)
        ]
    }


def _build_batch_config(
    request: ExternalRunRequest,
    generation_dir: Path,
    command: tuple[str, ...],
) -> BatchConfig:
    return BatchConfig(
        command=command,
        output_root=generation_dir,
        workbuddy_home=request.workbuddy_home,
        product_config=request.product_config,
        model=request.model,
        effort=request.effort,
        # 外层 attempt scheduler 控制全局 case 并发；单次 WB 批次只含一个 case。
        parallel=1,
        repetition=1,
        timeout_seconds=request.timeout_seconds,
        stall_timeout_seconds=request.stall_timeout_seconds,
        allowed_tools=request.allowed_tools,
        disallowed_tools=request.disallowed_tools,
        capture_native_session=True,
        require_skill=True,
        skills=(request.skill_name,) if request.skill_name else (),
        skill_paths=(request.skill_path.resolve(),) if request.skill_path else (),
        append_system_prompt=compile_execution_directive(
            request.output_contract
        ),
        artifact_globs=(request.output_contract.required_glob,),
        environment=dict(request.environment),
    )


def _batch_status(cases: list[ExternalCaseResult]) -> str:
    if any(item.status in {"queued", "running", "retrying"} for item in cases):
        return "running"
    generated = sum(item.status == "generated" for item in cases)
    if generated == len(cases):
        return "completed"
    if generated:
        return "partial"
    if any(item.status == "cancelled" for item in cases):
        return "cancelled"
    return "failed"


def _persist_result(
    generation_dir: Path,
    result: ExternalBatchResult,
) -> None:
    write_json(
        generation_dir / "generation_result.json",
        result.to_dict(include_report_text=True),
    )


def _notify_progress(
    callback: Optional[Callable[[ExternalBatchResult], None]],
    result: ExternalBatchResult,
) -> None:
    if callback is None:
        return
    try:
        callback(result)
    except Exception:
        # 进度上报属于旁路能力，不能反向打断真实报告生成。
        pass


def _case_attempt_run_id(wb_case_id: str, attempt: int) -> str:
    return "case-%s-%s-attempt-%02d" % (
        _safe_id(wb_case_id)[:48],
        sha256_text(wb_case_id)[:8],
        attempt,
    )


def _execute_case_attempt(
    case: CaseSpec,
    identity: Dict[str, str],
    attempt: int,
    request: ExternalRunRequest,
    batch_config: BatchConfig,
) -> ExternalAttemptResult:
    """执行单个 case 的一次尝试，并独立完成报告验收。"""

    wb_run_id = _case_attempt_run_id(case.case_id, attempt)
    expected_run_dir = batch_config.output_root / wb_run_id
    try:
        run_dir = BatchRunner(batch_config).run(
            [case],
            run_id=wb_run_id,
        )
        summaries = _read_batch_summaries(run_dir)
        case_dir = run_dir / "cases" / _safe_id(case.case_id)
        summary = summaries.get(
            case.case_id,
            {
                "case_id": case.case_id,
                "status": "harness_error",
                "error": "WB 批次没有返回该 case 的机器结果",
                "rounds": [],
            },
        )
        validation = validate_report_artifact(
            case_dir,
            request.output_contract,
        )
        wb_status = str(summary.get("status") or "harness_error")
        warning = None
        if validation.valid and wb_status != "success":
            warning = (
                f"WB status={wb_status}，但报告已通过 Artifact Validator"
            )
        error = None
        if not validation.valid:
            error = " | ".join(
                str(item)
                for item in (summary.get("error"), validation.error)
                if item
            ) or "未产出有效报告"
        return ExternalAttemptResult(
            attempt=attempt,
            max_attempts=request.max_attempts,
            wb_case_id=case.case_id,
            openharness_case_id=identity["openharness_case_id"],
            status=(
                "generated" if validation.valid else validation.status
            ),
            wb_status=wb_status,
            wb_run_id=wb_run_id,
            wb_session_id=_read_wb_session_id(case_dir),
            run_dir=str(run_dir),
            trace_path=str(case_dir / "trace"),
            manifest_path=str(
                case_dir / "artifacts" / "manifest.json"
            ),
            duration_ms=summary.get("duration_ms"),
            configured_model=summary.get("configured_model"),
            observed_models=tuple(
                str(item)
                for item in summary.get("observed_models", [])
            ),
            usage=_usage(summary),
            error=error,
            warning=warning,
            report=validation.report,
        )
    except Exception as exc:
        case_dir = expected_run_dir / "cases" / _safe_id(case.case_id)
        return ExternalAttemptResult(
            attempt=attempt,
            max_attempts=request.max_attempts,
            wb_case_id=case.case_id,
            openharness_case_id=identity["openharness_case_id"],
            status="harness_error",
            wb_status="harness_error",
            wb_run_id=wb_run_id,
            wb_session_id=None,
            run_dir=str(expected_run_dir),
            trace_path=str(case_dir / "trace"),
            manifest_path=str(
                case_dir / "artifacts" / "manifest.json"
            ),
            duration_ms=None,
            configured_model=request.model,
            observed_models=(),
            usage={},
            error="%s: %s" % (type(exc).__name__, exc),
        )


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
    """执行真实报告生成；无有效报告时按 case 最多额外重试 N 次。"""

    case_file = request.case_file.expanduser().resolve()
    if not case_file.is_file():
        raise ExternalRunConfigurationError(
            f"dataset 不存在或不是文件: {case_file}"
        )
    if request.auto_judge:
        raise ExternalRunConfigurationError(
            "Phase 0 尚未接入自动 Judge，请使用 auto_judge=False"
        )
    if request.skill_path:
        skill_path = request.skill_path.expanduser().resolve()
        requested_skill_name = _skill_name_from_path(skill_path)
        request = replace(request, skill_path=skill_path)
    else:
        requested_skill_name = str(request.skill_name)

    command = request.command or discover_command()
    try:
        raw_cases = load_cases(case_file)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise ExternalRunConfigurationError(
            f"dataset 解析失败: {exc}"
        ) from exc
    cases, identities = _normalize_cases(
        raw_cases,
        replace(request, case_file=case_file),
        requested_skill_name,
    )
    if request.openharness_case_ids:
        requested_ids = set(request.openharness_case_ids)
        available_ids = {
            item["openharness_case_id"]
            for item in identities.values()
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

    generation_id = (
        f"gen-{datetime.now().strftime('%Y%m%dT%H%M%S')}-"
        f"{uuid.uuid4().hex[:8]}"
    )
    generation_dir = request.output_root.expanduser().resolve() / generation_id
    generation_dir.mkdir(parents=True, exist_ok=False)
    started_at = _iso_now()
    write_json(
        generation_dir / "request.json",
        {
            **request.to_dict(),
            "generation_id": generation_id,
            "effective_command": list(command),
            "effective_skill_name": requested_skill_name,
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
    batch_config = _build_batch_config(
        request,
        generation_dir,
        tuple(command),
    )
    initial = ExternalBatchResult(
        generation_id=generation_id,
        session_id=request.session_id,
        skill_version=request.skill_version,
        status="running",
        output_dir=str(generation_dir),
        created_at=started_at,
        finished_at=started_at,
        cases=list(case_results.values()),
    )
    _persist_result(generation_dir, initial)
    _notify_progress(progress_callback, initial)

    # 只保留最多 parallel 个在途 attempt。某 case 失败后，其下一次尝试
    # 立即放到 ready 队首，无需等待其他 case 的本轮执行结束。
    ready = deque((wb_case_id, 1) for wb_case_id in case_by_id)
    in_flight = {}
    with ThreadPoolExecutor(max_workers=request.parallel) as executor:
        while ready or in_flight:
            cancelled = bool(should_cancel and should_cancel())
            if cancelled:
                while ready:
                    wb_case_id, _ = ready.popleft()
                    case_results[wb_case_id].status = "cancelled"

            while (
                ready
                and len(in_flight) < request.parallel
                and not cancelled
            ):
                wb_case_id, attempt = ready.popleft()
                case_results[wb_case_id].status = "running"
                future = executor.submit(
                    _execute_case_attempt,
                    case_by_id[wb_case_id],
                    identities[wb_case_id],
                    attempt,
                    request,
                    batch_config,
                )
                in_flight[future] = (wb_case_id, attempt)

            if not in_flight:
                break
            completed, _ = wait(
                tuple(in_flight),
                return_when=FIRST_COMPLETED,
            )
            for future in completed:
                wb_case_id, attempt = in_flight.pop(future)
                attempt_result = future.result()
                case_result = case_results[wb_case_id]
                case_result.attempts.append(attempt_result)
                if attempt_result.has_valid_report:
                    case_result.status = "generated"
                    case_result.report = attempt_result.report
                elif should_cancel and should_cancel():
                    case_result.status = "cancelled"
                elif attempt < request.max_attempts:
                    case_result.status = "retrying"
                    ready.appendleft((wb_case_id, attempt + 1))
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

    result = _snapshot_result(
        generation_id,
        request,
        generation_dir,
        started_at,
        case_results,
    )
    _persist_result(generation_dir, result)
    _notify_progress(progress_callback, result)
    return result
