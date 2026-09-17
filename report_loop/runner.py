#!/usr/bin/env python3
"""Python-owned Report Loop orchestrator."""
from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Callable

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from report_loop.core.judge_provider import (
    JudgeProviderError,
    judge_settings_are_pinned,
    locked_report_judge_settings,
)
from report_loop.core.host_model_resolver import (
    HostModelResolutionError,
    resolve_host_model_id,
)
from report_loop.core.persistent_rewriter import PersistentRewriter, RewriterError
from report_loop.core.memory_rubric_provider import (
    MemoryRubricProvider,
    resolve_memory_root,
)
from report_loop.core.runtime import ReportLoopError, ReportLoopRuntime
from report_loop.core.workspace import (
    WorkspaceError,
    assert_within,
    delivery_path,
    derive_material_root,
    ensure_writable,
    find_existing_report_ids,
    hide_internal_dir,
    internal_root,
    new_report_id,
    next_loop_dir,
    next_version,
    parse_report_id,
    resolve_report_dir,
    sanitize_topic,
    version_ledger_path,
)


class JobError(ValueError):
    """Invalid App-to-runner job."""


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise JobError(f"{name} is required")
    return text


def load_job(path: Path) -> dict[str, Any]:
    path = path.expanduser().resolve()
    job = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(job, dict) or job.get("schemaVersion") != 2:
        raise JobError("schemaVersion must be 2")
    normalized = copy.deepcopy(job)
    normalized["originalUserQuery"] = _required_text(
        job.get("originalUserQuery"), "originalUserQuery"
    )
    intake = job.get("intakeContext")
    if not isinstance(intake, dict):
        raise JobError("intakeContext must be an object")
    evidence = intake.get("userInputEvidence")
    if not isinstance(evidence, dict):
        raise JobError("intakeContext.userInputEvidence must be an object")
    for key in ("reportBackground", "materialHypothesis", "priorityMaterials"):
        _required_text(
            evidence.get(key),
            f"intakeContext.userInputEvidence.{key}",
        )
    for key in ("reportBackground", "materialHypothesis"):
        item = intake.get(key)
        if not isinstance(item, dict):
            raise JobError(f"intakeContext.{key} must be an object")
        _required_text(item.get("value"), f"intakeContext.{key}.value")
    materials = intake.get("priorityMaterials")
    if not isinstance(materials, list) or not materials:
        raise JobError("intakeContext.priorityMaterials must not be empty")
    seen: set[str] = set()
    for index, item in enumerate(materials):
        if not isinstance(item, dict):
            raise JobError(f"priorityMaterials[{index}] must be an object")
        material = Path(_required_text(item.get("path"), f"priorityMaterials[{index}].path")).expanduser().resolve()
        if not material.is_absolute() or not material.exists():
            raise JobError(f"priority material path does not exist: {material}")
        display = _required_text(item.get("displayName"), f"priorityMaterials[{index}].displayName")
        key = os.path.normcase(str(material))
        if key in seen:
            raise JobError(f"duplicate priority material: {material}")
        seen.add(key)
        item["path"] = str(material)
        item["displayName"] = display
    normalized["intakeContext"] = intake
    draft = Path(_required_text(job.get("draftArtifactPath"), "draftArtifactPath")).expanduser().resolve()
    if not draft.is_file():
        raise JobError(f"draft report does not exist: {draft}")
    normalized["draftArtifactPath"] = str(draft)
    host_model = job.get("hostModel") or {}
    if not isinstance(host_model, dict):
        raise JobError("hostModel must be an object when provided")
    try:
        host_model_id = resolve_host_model_id(path)
    except HostModelResolutionError as exc:
        raise JobError(str(exc)) from exc
    normalized["hostModel"] = {
        "modelId": host_model_id,
        **({"effort": str(host_model["effort"]).strip()} if str(host_model.get("effort") or "").strip() else {}),
    }
    try:
        judge_override_model = str(job.get("judgeModel") or "").strip() or None
        judge_override_effort = str(job.get("judgeEffort") or "").strip() or None
        normalized["judgeProvider"] = locked_report_judge_settings(
            job.get("judgeProvider"),
            model=judge_override_model,
            effort=judge_override_effort,
        ).provider
    except JudgeProviderError as exc:
        raise JobError(str(exc)) from exc
    if judge_override_model:
        normalized["judgeModel"] = judge_override_model
    else:
        normalized.pop("judgeModel", None)
    if judge_override_effort:
        normalized["judgeEffort"] = judge_override_effort
    else:
        normalized.pop("judgeEffort", None)
    normalized["outputPath"] = str(
        Path(_required_text(job.get("outputPath"), "outputPath")).expanduser().resolve()
    ) if str(job.get("outputPath") or "").strip() else None

    # 工作区锚定：产物位置由这里推导与校验。
    # 「用户指定位置优先」——显式 outputPath 即用户指定的交付位置，其所在目录
    # 就是本次报告目录；未指定时才回落到 素材目录/报告/。
    material_paths = [item["path"] for item in materials]
    try:
        material_root = derive_material_root(material_paths)
        requested_dir = job.get("reportDir")
        if not requested_dir and normalized["outputPath"]:
            requested_dir = str(Path(normalized["outputPath"]).parent)
        report_dir = resolve_report_dir(
            material_root=material_root,
            requested=requested_dir,
        )
        report_dir = ensure_writable(report_dir)
        topic = sanitize_topic(
            job.get("reportTopic") or normalized["originalUserQuery"][:40]
        )
        report_id = str(job.get("reportId") or "").strip()
        if report_id:
            parse_report_id(report_id)
        else:
            existing = find_existing_report_ids(report_dir, topic)
            # 沿用已有报告标识，使反馈与新 Loop 都归到同一份报告下
            report_id = existing[-1] if existing else new_report_id(topic)
        if normalized["outputPath"]:
            assert_within(
                Path(normalized["outputPath"]), report_dir, label="outputPath"
            )
    except WorkspaceError as exc:
        raise JobError(str(exc)) from exc

    normalized["materialRoot"] = str(material_root)
    normalized["reportDir"] = str(report_dir)
    normalized["reportTopic"] = topic
    normalized["reportId"] = report_id
    for key in ("dataVersion", "dataSha256", "roundRequest"):
        value = str(job.get(key) or "").strip()
        if value:
            normalized[key] = value
        else:
            normalized.pop(key, None)
    if job.get("structuredDataPath"):
        structured = Path(str(job["structuredDataPath"])).expanduser().resolve()
        if not structured.is_file():
            raise JobError(f"structuredDataPath does not exist: {structured}")
        normalized["structuredDataPath"] = str(structured)
    if job.get("cancelFilePath"):
        normalized["cancelFilePath"] = str(Path(str(job["cancelFilePath"])).expanduser().resolve())
    return normalized


def _cancelled(job: dict[str, Any]) -> bool:
    value = job.get("cancelFilePath")
    return bool(value and Path(value).is_file())


def _publish(result: dict[str, Any], job: dict[str, Any]) -> dict[str, Any]:
    """把本轮最佳候选发布为 报告/<报告主题>-vN.md，并登记版本说明。

    内部候选与评测明细只留在隐藏目录；交付版本号与直接改写共用一套序号，
    旧版保留（见 skills/research-report-loop/references/workspace-and-delivery.md）。
    """
    source = Path(result["bestArtifactPath"]).resolve()
    explicit = job.get("outputPath")
    report_dir_value = job.get("reportDir")
    if not report_dir_value:
        # 未经 load_job 归一化的 Job：按显式 outputPath 交付，不登记版本说明。
        if not explicit:
            raise JobError("缺少 reportDir 与 outputPath，无法确定交付位置")
        output = Path(explicit).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        if source != output:
            temporary = output.with_name(output.name + ".report-loop.tmp")
            shutil.copyfile(source, temporary)
            os.replace(temporary, output)
        return {
            **result,
            "finalArtifactPath": str(output),
            "rewriteRounds": max(0, int(result.get("judgedVersions") or 1) - 1),
        }

    report_dir = Path(report_dir_value).resolve()
    topic = job["reportTopic"]

    if explicit:
        output = Path(explicit).resolve()
    else:
        output = delivery_path(report_dir, topic, next_version(report_dir, topic))

    output.parent.mkdir(parents=True, exist_ok=True)
    if source != output:
        temporary = output.with_name(output.name + ".report-loop.tmp")
        shutil.copyfile(source, temporary)
        os.replace(temporary, output)

    hide_internal_dir(internal_root(report_dir))
    ledger = _update_version_ledger(result, job, output)

    return {
        **result,
        "finalArtifactPath": str(output),
        "reportDir": str(report_dir),
        "reportId": job["reportId"],
        "reportTopic": topic,
        "loopDir": job.get("loopDir"),
        "versionLedgerPath": str(ledger),
        "deliveredVersion": output.stem.rsplit("-v", 1)[-1] if "-v" in output.stem else None,
        "rewriteRounds": max(0, int(result.get("judgedVersions") or 1) - 1),
    }


def _update_version_ledger(
    result: dict[str, Any], job: dict[str, Any], output: Path
) -> Path:
    """追加一行版本记录。未评测不沿用旧分数，未知信息不补猜。"""
    report_dir = Path(job["reportDir"]).resolve()
    path = version_ledger_path(report_dir)
    header = [
        "# 版本说明",
        "",
        f"当前版本：{output.name}",
        "",
        "| 版本 | 时间 | 数据版本 | 用户修改要求 | 修改摘要 | 评测结果 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    rows: list[str] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("|") and not stripped.startswith("| ---") \
                    and not stripped.startswith("| 版本 "):
                rows.append(stripped)

    def cell(value: Any) -> str:
        text = str(value if value not in (None, "") else "未记录")
        return text.replace("|", "\\|").replace("\n", " ")[:200]

    data_version = job.get("dataVersion")
    data_sha = job.get("dataSha256")
    data_cell = (
        f"{data_version} / `{str(data_sha)[:12]}`"
        if data_version and data_sha
        else cell(data_version)
    )

    score = result.get("bestScore")
    if score is None:
        evaluation = "未完成评测"
    else:
        evaluation = (
            f"{score} 分；{result.get('judgedVersions')} 个候选，"
            f"最佳 {result.get('bestVersion')}，停止原因 {result.get('stopCode')}"
        )
        if result.get("scoresComparableToHistory") is False:
            evaluation += "；与历史评分不可比"

    rows.append(
        "| " + " | ".join([
            output.name,
            _timestamp(),
            data_cell,
            cell(job.get("roundRequest")),
            cell(result.get("stopReason")),
            cell(evaluation),
        ]) + " |"
    )
    body = "\n".join(header + rows) + "\n"
    temporary = path.with_name(path.name + ".report-loop.tmp")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, path)
    return path


def _timestamp() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="seconds")


def run(
    job: dict[str, Any],
    *,
    runtime_factory: Callable[..., ReportLoopRuntime] = ReportLoopRuntime,
    rewriter_factory: Callable[..., PersistentRewriter] = PersistentRewriter,
) -> dict[str, Any]:
    settings = locked_report_judge_settings(
        job.get("judgeProvider"),
        model=str(job.get("judgeModel") or "").strip() or None,
        effort=str(job.get("judgeEffort") or "").strip() or None,
    )
    model = job["hostModel"]
    # 记忆根目录：Job 指定 > REPORT_AGENT_MEMORY_DIR > 主目录下 ReportAgentMemory/
    memory_root = resolve_memory_root(job.get("memoryRoot"))
    runtime = runtime_factory(
        judge_provider=settings.provider,
        judge_model=settings.model,
        judge_effort=settings.effort,
        judge_fallback_model=model["modelId"],
        judge_fallback_effort=model.get("effort"),
        memory_provider=MemoryRubricProvider(memory_root),
    )
    model = job["hostModel"]
    # 本轮 Loop 目录：报告/.report-agent/<报告标识>/loop-NNN-时间戳/
    # 同一报告重启 Loop 时序号递增，旧 Loop 目录完整保留。
    # 直接构造 Job（未经 load_job 归一化）时退回插件数据目录布局。
    loop_dir = None
    if job.get("reportDir") and job.get("reportId"):
        loop_dir = next_loop_dir(Path(job["reportDir"]), job["reportId"])
        job["loopDir"] = str(loop_dir)
    started = runtime.start(
        originalUserQuery=job["originalUserQuery"],
        intakeContext=job["intakeContext"],
        writerModel=model,
        audience=str(job.get("audience") or ""),
        project=str(job.get("project") or ""),
        artifactPath=job["draftArtifactPath"],
        structuredDataPath=job.get("structuredDataPath"),
        loopDir=str(loop_dir) if loop_dir else None,
        reportDir=job.get("reportDir"),
        reportTopic=job.get("reportTopic"),
        reportId=job.get("reportId"),
        dataVersion=job.get("dataVersion"),
        dataSha256=job.get("dataSha256"),
        roundRequest=job.get("roundRequest"),
    )
    run_id = started["runId"]
    deadline = runtime.deadline_at(run_id)
    rewriter = None
    result: dict[str, Any] | None = None
    try:
        try:
            result = runtime.submit(
                runId=run_id,
                artifactPath=job["draftArtifactPath"],
                timeoutSeconds=deadline - time.time(),
            )
        except ReportLoopError as exc:
            result = {
                "status": "completed",
                "runId": run_id,
                "nextAction": "deliver",
                "stopCode": "judge_unavailable",
                "stopReason": str(exc),
                "bestVersion": "v1",
                "bestScore": None,
                "bestArtifactPath": job["draftArtifactPath"],
                "judgedVersions": 0,
                "judgeModel": settings.model,
                "judgeEffort": settings.effort,
                "judgeProvider": settings.provider,
                "judgeFallbackProvider": "workbuddy",
                "judgeFallbackModel": model["modelId"],
                "judgeFallbackUsed": False,
                "judgeModelPinned": judge_settings_are_pinned(settings),
                "scoresComparableToHistory": False,
            }
        while result["nextAction"] == "revise":
            if _cancelled(job):
                result = runtime.finish(runId=run_id, reason="user_cancelled")
                break
            if time.time() >= deadline:
                result = runtime.finish(runId=run_id, reason="time_budget_exhausted")
                break
            if rewriter is None:
                rewriter = rewriter_factory(
                    model=model["modelId"],
                    effort=model.get("effort"),
                    deadline_at=deadline,
                )
                rewriter.start()
            best_markdown = Path(result["bestArtifactPath"]).read_text(encoding="utf-8")
            payload = {
                "baseVersion": result["bestVersion"],
                "decision": result["decision"],
                "revisionBrief": result["revisionBrief"],
                "bestReportMarkdown": best_markdown,
            }
            if result["version"] == "v1":
                payload.update(
                    {
                        "originalUserQuery": job["originalUserQuery"],
                        "intakeContext": job["intakeContext"],
                        "reportV1Markdown": best_markdown,
                    }
                )
            candidate = rewriter.rewrite(payload)
            candidate_path = runtime.run_directory(run_id) / "candidates" / (
                f"v{result['judgedVersions'] + 1}.md"
            )
            candidate_path.parent.mkdir(parents=True, exist_ok=True)
            candidate_path.write_text(candidate + "\n", encoding="utf-8")
            try:
                result = runtime.submit(
                    runId=run_id,
                    artifactPath=str(candidate_path),
                    timeoutSeconds=deadline - time.time(),
                )
            except ReportLoopError as exc:
                reason = (
                    "time_budget_exhausted"
                    if time.time() >= deadline
                    else "judge_unavailable"
                )
                result = runtime.finish(runId=run_id, reason=reason)
                result["fault"] = str(exc)
                break
        if result.get("status") != "completed":
            result = runtime.finish(runId=run_id)
    except RewriterError as exc:
        result = runtime.finish(runId=run_id, reason="rewrite_unavailable")
        result["fault"] = str(exc)
    except KeyboardInterrupt:
        if result is None:
            raise
        result = runtime.finish(runId=run_id, reason="user_cancelled")
    finally:
        if rewriter is not None:
            rewriter.close()
    delivered = _publish(result, job)
    delivered["judgePrimaryModel"] = settings.model
    delivered["judgePrimaryProvider"] = settings.provider
    delivered["judgeModel"] = result.get("judgeModel", settings.model)
    delivered["judgeEffort"] = result.get("judgeEffort", settings.effort)
    delivered["judgeProvider"] = result.get("judgeProvider", settings.provider)
    delivered["judgeFallbackProvider"] = "workbuddy"
    delivered["judgeFallbackModel"] = model["modelId"]
    delivered["judgeFallbackUsed"] = bool(result.get("judgeFallbackUsed"))
    return delivered


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", required=True)
    args = parser.parse_args()
    try:
        job = load_job(Path(args.job).resolve())
        payload = run(job)
        exit_code = 0 if payload.get("status") == "completed" else 1
    except Exception as exc:
        payload = {"status": "error", "reason": str(exc)}
        exit_code = 1
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
