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

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from mcp.report_loop.core.judge_provider import (
    JudgeProviderError,
    locked_report_judge_settings,
)
from mcp.report_loop.core.host_model_resolver import (
    HostModelResolutionError,
    resolve_host_model_id,
)
from mcp.report_loop.core.persistent_rewriter import PersistentRewriter, RewriterError
from mcp.report_loop.core.memory_rubric_provider import MemoryRubricProvider
from mcp.report_loop.core.runtime import ReportLoopError, ReportLoopRuntime


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
        if not material.is_absolute() or not material.is_file():
            raise JobError(f"priority material does not exist: {material}")
        display = _required_text(item.get("displayName"), f"priorityMaterials[{index}].displayName")
        key = os.path.normcase(str(material))
        if key in seen:
            raise JobError(f"duplicate priority material: {material}")
        seen.add(key)
        item["path"] = str(material)
        item["displayName"] = display
    normalized["intakeContext"] = intake
    v1 = Path(_required_text(job.get("v1ArtifactPath"), "v1ArtifactPath")).expanduser().resolve()
    if not v1.is_file():
        raise JobError(f"V1 report does not exist: {v1}")
    normalized["v1ArtifactPath"] = str(v1)
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
        normalized["judgeProvider"] = locked_report_judge_settings(
            job.get("judgeProvider")
        ).provider
    except JudgeProviderError as exc:
        raise JobError("judgeProvider must be workbuddy or codex") from exc
    normalized["outputPath"] = str(
        Path(_required_text(job.get("outputPath"), "outputPath")).expanduser().resolve()
    )
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


def _deliver(result: dict[str, Any], output_value: str) -> dict[str, Any]:
    source = Path(result["bestArtifactPath"]).resolve()
    output = Path(output_value).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    if source != output:
        temporary = output.with_name(output.name + ".report-loop.tmp")
        shutil.copyfile(source, temporary)
        os.replace(temporary, output)
    return {**result, "finalArtifactPath": str(output)}


def run(
    job: dict[str, Any],
    *,
    runtime_factory: Callable[..., ReportLoopRuntime] = ReportLoopRuntime,
    rewriter_factory: Callable[..., PersistentRewriter] = PersistentRewriter,
) -> dict[str, Any]:
    settings = locked_report_judge_settings(job.get("judgeProvider"))
    model = job["hostModel"]
    memory_dir = Path(
        os.environ.get(
            "RESEARCH_REPORT_MEMORY_V2_0821_DIR",
            "~/.research-report-memory-v2-0821",
        )
    ).expanduser()
    runtime = runtime_factory(
        judge_provider=settings.provider,
        judge_model=settings.model,
        judge_effort=settings.effort,
        judge_fallback_model=model["modelId"],
        judge_fallback_effort=model.get("effort"),
        memory_provider=MemoryRubricProvider(memory_dir),
    )
    model = job["hostModel"]
    started = runtime.start(
        originalUserQuery=job["originalUserQuery"],
        intakeContext=job["intakeContext"],
        writerModel=model,
        audience=str(job.get("audience") or ""),
        project=str(job.get("project") or ""),
        artifactPath=job["v1ArtifactPath"],
        structuredDataPath=job.get("structuredDataPath"),
    )
    run_id = started["runId"]
    deadline = runtime.deadline_at(run_id)
    rewriter = None
    result: dict[str, Any] | None = None
    try:
        try:
            result = runtime.submit(
                runId=run_id,
                artifactPath=job["v1ArtifactPath"],
                timeoutSeconds=deadline - time.time(),
            )
        except ReportLoopError as exc:
            return {
                "status": "error",
                "runId": run_id,
                "stopCode": "judge_unavailable",
                "reason": str(exc),
                "judgeModel": settings.model,
                "judgeEffort": settings.effort,
                "judgeProvider": settings.provider,
                "judgeFallbackProvider": "workbuddy",
                "judgeFallbackModel": model["modelId"],
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
    delivered = _deliver(result, job["outputPath"])
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
