# -*- coding: utf-8 -*-
"""候选迭代的轻量、可关联、可回放日志。

每个被生成/评测的 Skill 版本在 sessions/<sid>/iterations/<version>/ 下维护
五个 JSON 文件。文件只保存 hash、摘要和现有大文件的相对引用，不复制报告、
Prompt 或模型完整响应。
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import persistence as persist
import optimizer_pipeline


SCHEMA_VERSION = "openharness-iteration/v1"
FILES = (
    "manifest.json",
    "optimizer_summary.json",
    "dialogue_contract.json",
    "gate_decision.json",
    "resource_usage.json",
)

_LOCK = threading.RLock()
_FIELD_PATTERNS = {
    "background": re.compile(r"背景|受众|给谁看|汇报对象|决策|场合", re.I),
    "hypothesis": re.compile(r"hypo(?:thesis)?|假设|预判|待验证", re.I),
    "priority_materials": re.compile(r"重点素材|重点材料|材料重点|高质量材料", re.I),
    "length_budget": re.compile(r"篇幅|字数|页数|长度|多少页|多少字", re.I),
}


def _now() -> float:
    return round(time.time(), 3)


def _safe_segment(value: Any) -> str:
    text = str(value or "").strip()
    if not text or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in text):
        raise ValueError("iteration trace 路径段非法: %r" % text)
    return text


def _json_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _atomic_write(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(
        ".%s.%s.%s.tmp"
        % (path.name, os.getpid(), threading.get_ident())
    )
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def _read(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return dict(default)
    return payload if isinstance(payload, dict) else dict(default)


def _iteration_dir(session_id: str, version: str) -> Path:
    return (
        Path(persist._BASE)
        / _safe_segment(session_id)
        / "iterations"
        / _safe_segment(version)
    )


def _version_entry(session, version: str) -> Optional[Dict[str, Any]]:
    for entry in session.versions:
        if entry.get("version") == version:
            return entry
    return None


def _skill_dict(entry: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not entry:
        return {}
    skill = entry.get("skill")
    return skill.to_dict() if hasattr(skill, "to_dict") else dict(skill or {})


def _instruction_text(entry: Optional[Dict[str, Any]]) -> str:
    skill = _skill_dict(entry)
    return str((skill.get("instructions") or {}).get("prose") or "")


def _defaults(session, version: str) -> Dict[str, Dict[str, Any]]:
    entry = _version_entry(session, version)
    skill = _skill_dict(entry)
    parent_version = (entry or {}).get("parent") or skill.get("parent_version")
    iteration_id = "iter-%s-%s-%s" % (
        _safe_segment(session.id),
        _safe_segment(version),
        _json_hash(skill)[:12],
    )
    now = _now()
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "iteration_id": iteration_id,
        "session_id": session.id,
        "candidate_version": version,
        "parent_version": parent_version,
        "status": "initialized",
        "created_at": now,
        "updated_at": now,
        "inputs": {
            "requirement_sha256": _text_hash(
                str(getattr(session, "requirement", "") or "")
            ),
            "rubric_sha256": _json_hash(getattr(session, "rubric", {})),
            "dataset_sha256": _json_hash(getattr(session, "cases", [])),
            "candidate_skill_sha256": _json_hash(skill),
        },
        "correlation": {
            "optimizer_run_id": None,
            "generation_job_ids": [],
            "generation_ids": [],
            "judge_run_ids": [],
        },
        "files": {name[:-5]: name for name in FILES},
        "existing_artifacts": {
            "events": "../../events.jsonl",
            "outputs": "../../outputs.jsonl",
            "check_judgments": "../../check_judgments.jsonl",
            "generation_jobs": "../../generation_jobs/",
        },
    }
    if parent_version:
        parent = _version_entry(session, parent_version)
        manifest["inputs"]["parent_skill_sha256"] = _json_hash(
            _skill_dict(parent)
        )
    return {
        "manifest.json": manifest,
        "optimizer_summary.json": {
            "schema_version": SCHEMA_VERSION,
            "iteration_id": iteration_id,
            "status": "pending" if parent_version else "not_applicable",
            "updated_at": now,
        },
        "dialogue_contract.json": {
            "schema_version": SCHEMA_VERSION,
            "iteration_id": iteration_id,
            "status": "pending",
            "expected_fields": [
                "background",
                "hypothesis",
                "priority_materials",
            ],
            "cases": {},
            "summary": {},
            "updated_at": now,
        },
        "gate_decision.json": {
            "schema_version": SCHEMA_VERSION,
            "iteration_id": iteration_id,
            "status": "pending" if parent_version else "not_applicable",
            "decision": None,
            "updated_at": now,
        },
        "resource_usage.json": {
            "schema_version": SCHEMA_VERSION,
            "iteration_id": iteration_id,
            "generation": {"jobs": {}, "totals": {}},
            "judge": {"runs": {}, "totals": {}},
            "optimizer": {"model_calls": 0},
            "updated_at": now,
        },
    }


def ensure_iteration(session, version: str) -> Dict[str, Any]:
    """创建五文件骨架并返回 manifest；已有内容不会被覆盖。"""
    with _LOCK:
        root = _iteration_dir(session.id, version)
        manifest_path = root / "manifest.json"
        # 生成任务会在每个 case 状态变化时更新资源日志。正常热路径只读已有
        # manifest，避免为每次进度刷新重复序列化/哈希整份 dataset 与报告。
        if all((root / name).exists() for name in FILES):
            manifest = _read(manifest_path, {})
            if manifest.get("iteration_id"):
                return manifest
        defaults = _defaults(session, version)
        for name, payload in defaults.items():
            path = root / name
            if not path.exists():
                _atomic_write(path, payload)
        manifest = _read(manifest_path, defaults["manifest.json"])
        if not manifest.get("iteration_id"):
            manifest = defaults["manifest.json"]
            _atomic_write(manifest_path, manifest)
        return manifest


def iteration_id(session, version: str) -> str:
    return str(ensure_iteration(session, version)["iteration_id"])


def _update(
    session,
    version: str,
    filename: str,
    updater,
) -> Dict[str, Any]:
    with _LOCK:
        manifest = ensure_iteration(session, version)
        path = _iteration_dir(session.id, version) / filename
        payload = _read(
            path,
            {
                "schema_version": SCHEMA_VERSION,
                "iteration_id": manifest["iteration_id"],
            },
        )
        updater(payload)
        payload["updated_at"] = _now()
        _atomic_write(path, payload)
        return payload


def _update_manifest(session, version: str, updater) -> Dict[str, Any]:
    return _update(session, version, "manifest.json", updater)


def record_optimizer_proposal(
    session,
    version: str,
    context: Dict[str, Any],
    proposal: Dict[str, Any],
) -> Dict[str, Any]:
    entry = _version_entry(session, version)
    parent = _version_entry(session, (entry or {}).get("parent"))
    before = _instruction_text(parent)
    after = _instruction_text(entry)
    diff = list(
        difflib.ndiff(before.splitlines(), after.splitlines())
    )
    open_failures = list((context or {}).get("open_failures") or [])
    evidence_sent = sum(
        len(item.get("evidence") or []) for item in open_failures
    )
    failure_hits = sum(
        int(item.get("hit_count") or 0) for item in open_failures
    )
    failure_inventory = list(
        (context or {}).get("failure_inventory") or []
    )
    diagnosis_candidates = list(
        (context or {}).get("diagnosis_candidates") or []
    )
    diagnosis_evidence = list(
        (context or {}).get("diagnosis_evidence") or []
    )
    evidence_catalog = list(
        (context or {}).get("evidence_catalog") or []
    )
    evidence_by_check: Dict[str, int] = {}
    for item in diagnosis_evidence:
        check_id = str(item.get("check_id") or "")
        if check_id:
            evidence_by_check[check_id] = evidence_by_check.get(check_id, 0) + 1
    trace = dict(proposal.get("_optimizer_trace") or {})
    run_id = "opt-%s-%s" % (version, _json_hash(trace or proposal)[:12])

    def update(payload):
        payload.update(
            {
                "status": "captured",
                "optimizer_run_id": run_id,
                "parent_version": (entry or {}).get("parent"),
                "candidate_version": version,
                "change_summary": proposal.get("change_summary", ""),
                "targets_failures": list(
                    proposal.get("targets_failures") or []
                ),
                "target_failure_count": len(
                    proposal.get("targets_failures") or []
                ),
                "affected_dims": list(proposal.get("affected_dims") or []),
                "diagnosis": dict(proposal.get("diagnosis") or {}),
                "selected_target": dict(
                    proposal.get("selected_target") or {}
                ),
                "root_cause": dict(proposal.get("root_cause") or {}),
                "experiment": dict(proposal.get("experiment") or {}),
                "patch": dict(proposal.get("patch") or {}),
                "redline_preservation": dict(
                    proposal.get("redline_preservation") or {}
                ),
                "patch_budget": dict(proposal.get("budget") or {}),
                "instruction_metrics": {
                    "parent_chars": len(before),
                    "candidate_chars": len(after),
                    "net_chars": len(after) - len(before),
                    "parent_lines": len(before.splitlines()),
                    "candidate_lines": len(after.splitlines()),
                    "lines_added": sum(
                        item.startswith("+ ") for item in diff
                    ),
                    "lines_removed": sum(
                        item.startswith("- ") for item in diff
                    ),
                    "parent_sha256": _text_hash(before),
                    "candidate_sha256": _text_hash(after),
                },
                "failure_context": {
                    "open_failure_count": len(open_failures),
                    "failure_hit_count_total": failure_hits,
                    "evidence_samples_sent": evidence_sent,
                    "evidence_cap_per_failure": 4,
                    "experiment_evidence_available": len(
                        (context or {}).get("experiment_evidence") or []
                    ),
                    "failure_inventory_count": len(failure_inventory),
                    "diagnosis_candidate_count": len(
                        diagnosis_candidates
                    ),
                    "diagnosis_evidence_sent": len(diagnosis_evidence),
                    "diagnosis_evidence_by_check": evidence_by_check,
                    "patch_evidence_sent": len(
                        (proposal.get("experiment") or {}).get("examples")
                        or []
                    ),
                    "evidence_catalog_available": len(evidence_catalog),
                },
                "model_trace": trace,
            }
        )

    summary = _update(
        session,
        version,
        "optimizer_summary.json",
        update,
    )

    def manifest_update(payload):
        payload["status"] = "optimizer_proposed"
        payload["correlation"]["optimizer_run_id"] = run_id

    _update_manifest(session, version, manifest_update)

    def resource_update(payload):
        model_calls = int(trace.get("model_calls") or 0)
        payload["optimizer"] = {
            "model_calls": model_calls,
            "diagnosis_prompt_chars": trace.get(
                "diagnosis_prompt_chars"
            ),
            "diagnosis_response_chars": trace.get(
                "diagnosis_response_chars"
            ),
            "diagnosis_duration_ms": trace.get(
                "diagnosis_duration_ms"
            ),
            "diagnosis_max_tokens": trace.get(
                "diagnosis_max_tokens"
            ),
            "patch_prompt_chars": trace.get("patch_prompt_chars"),
            "patch_response_chars": trace.get("patch_response_chars"),
            "patch_duration_ms": trace.get("patch_duration_ms"),
            "patch_max_tokens": trace.get("patch_max_tokens"),
            "rewrite_prompt_chars": trace.get("rewrite_prompt_chars"),
            "rewrite_response_chars": trace.get("rewrite_response_chars"),
            "rewrite_duration_ms": trace.get("rewrite_duration_ms"),
            "rewrite_max_tokens": trace.get("rewrite_max_tokens"),
            "wall_time_ms": sum(
                int(trace.get(key) or 0)
                for key in (
                    "diagnosis_duration_ms",
                    "patch_duration_ms",
                )
            ),
        }

    _update(session, version, "resource_usage.json", resource_update)
    return summary


def record_optimizer_failure(
    session,
    version: str,
    context: Dict[str, Any],
    diagnostic: Dict[str, Any],
) -> Dict[str, Any]:
    """记录未产出候选的下一轮 Optimizer 尝试，不覆盖当前版本原有轨迹。"""
    ensure_iteration(session, version)
    safe_diagnostic = dict(diagnostic or {})
    recorded_at = _now()
    run_id = "opt-failed-%s-%d-%s" % (
        version,
        int(recorded_at * 1000),
        _json_hash(safe_diagnostic)[:8],
    )
    attempt = {
        "optimizer_run_id": run_id,
        "status": "failed",
        "stage": safe_diagnostic.get("stage"),
        "model_calls": int(safe_diagnostic.get("model_calls") or 0),
        "error_code": safe_diagnostic.get("error_code"),
        "reason": safe_diagnostic.get("reason"),
        "diagnosis_prompt_chars": safe_diagnostic.get(
            "diagnosis_prompt_chars"
        ),
        "diagnosis_prompt_sha256": safe_diagnostic.get(
            "diagnosis_prompt_sha256"
        ),
        "diagnosis_duration_ms": safe_diagnostic.get(
            "diagnosis_duration_ms"
        ),
        "patch_prompt_chars": safe_diagnostic.get(
            "patch_prompt_chars"
        ),
        "patch_prompt_sha256": safe_diagnostic.get(
            "patch_prompt_sha256"
        ),
        "patch_duration_ms": safe_diagnostic.get("patch_duration_ms"),
        "rewrite_prompt_chars": safe_diagnostic.get(
            "rewrite_prompt_chars"
        ),
        "rewrite_prompt_sha256": safe_diagnostic.get(
            "rewrite_prompt_sha256"
        ),
        "rewrite_duration_ms": safe_diagnostic.get(
            "rewrite_duration_ms"
        ),
        "open_failure_count": len(
            (context or {}).get("open_failures") or []
        ),
        "experiment_evidence_available": len(
            (context or {}).get("experiment_evidence") or []
        ),
        "failure_inventory_count": len(
            (context or {}).get("failure_inventory") or []
        ),
        "diagnosis_candidate_count": len(
            (context or {}).get("diagnosis_candidates") or []
        ),
        "diagnosis_evidence_available": len(
            (context or {}).get("diagnosis_evidence") or []
        ),
        "diagnostic": {
            key: value
            for key, value in safe_diagnostic.items()
            if key not in {
                "llm_diagnostics",
                "rewrite_prompt_sha256",
            }
        },
        "llm_diagnostics": safe_diagnostic.get("llm_diagnostics") or {},
        "recorded_at": recorded_at,
    }

    def summary_update(payload):
        attempts = payload.setdefault("next_proposal_attempts", [])
        if not any(
            item.get("optimizer_run_id") == run_id for item in attempts
        ):
            attempts.append(attempt)
        payload["latest_next_proposal_status"] = "failed"

    summary = _update(
        session,
        version,
        "optimizer_summary.json",
        summary_update,
    )

    def resource_update(payload):
        optimizer = payload.setdefault("optimizer", {"model_calls": 0})
        attempts = optimizer.setdefault("failed_next_proposal_attempts", [])
        if not any(
            item.get("optimizer_run_id") == run_id for item in attempts
        ):
            attempts.append(attempt)
        optimizer["failed_next_proposal_model_calls"] = sum(
            int(item.get("model_calls") or 0)
            or len(
                (item.get("llm_diagnostics") or {}).get("attempts") or []
            )
            for item in attempts
        )
        optimizer["failed_next_proposal_wall_time_ms"] = sum(
            (
                int(item.get("diagnosis_duration_ms") or 0)
                + int(item.get("patch_duration_ms") or 0)
            )
            or int(item.get("rewrite_duration_ms") or 0)
            for item in attempts
        )

    _update(session, version, "resource_usage.json", resource_update)

    def manifest_update(payload):
        ids = payload["correlation"].setdefault(
            "failed_optimizer_run_ids",
            [],
        )
        if run_id not in ids:
            ids.append(run_id)

    _update_manifest(session, version, manifest_update)
    return summary


def record_generation_job(session, job_payload: Dict[str, Any]) -> Dict[str, Any]:
    version = str(job_payload["skill_version"])
    job_id = str(job_payload["job_id"])
    cases = list(job_payload.get("cases") or [])
    started = job_payload.get("started_at")
    finished = job_payload.get("finished_at")
    duration_ms = (
        round((float(finished) - float(started)) * 1000)
        if started is not None and finished is not None
        else None
    )
    job_summary = {
        "job_id": job_id,
        "generation_id": job_payload.get("generation_id"),
        "parent_job_id": job_payload.get("parent_job_id"),
        "status": job_payload.get("status"),
        "model": job_payload.get("model"),
        "case_count": int(job_payload.get("case_count") or len(cases)),
        "generated_count": int(job_payload.get("generated_count") or 0),
        "attempts": sum(int(item.get("attempts") or 0) for item in cases),
        "imported_count": int(job_payload.get("imported_count") or 0),
        "report_bytes": sum(
            int(item.get("report_size") or 0) for item in cases
        ),
        "failed_case_ids": list(job_payload.get("failed_case_ids") or []),
        "duration_ms": duration_ms,
        "parallel": job_payload.get("parallel"),
        "max_report_retries": job_payload.get("max_report_retries"),
        "timeout_seconds": job_payload.get("timeout_seconds"),
        "stall_timeout_seconds": job_payload.get("stall_timeout_seconds"),
        "dataset_sha256": job_payload.get("dataset_sha256"),
        "skill_artifact_hash": job_payload.get("skill_artifact_hash"),
        "execution_skill_hash": job_payload.get("execution_skill_hash"),
        "job_ref": "../../generation_jobs/%s.json" % job_id,
    }

    def update(payload):
        jobs = payload["generation"].setdefault("jobs", {})
        jobs[job_id] = job_summary
        values = list(jobs.values())
        payload["generation"]["totals"] = {
            "jobs": len(values),
            "case_slots": sum(item["case_count"] for item in values),
            "case_attempts": sum(item["attempts"] for item in values),
            "imported_reports": sum(
                item["imported_count"] for item in values
            ),
            "report_bytes": sum(item["report_bytes"] for item in values),
            "failed_case_slots": sum(
                len(item["failed_case_ids"]) for item in values
            ),
            "wall_time_ms": sum(
                item["duration_ms"] or 0 for item in values
            ),
        }

    resource = _update(
        session,
        version,
        "resource_usage.json",
        update,
    )

    def manifest_update(payload):
        ids = payload["correlation"].setdefault("generation_job_ids", [])
        if job_id not in ids:
            ids.append(job_id)
        generation_id = job_payload.get("generation_id")
        generations = payload["correlation"].setdefault(
            "generation_ids", []
        )
        if generation_id and generation_id not in generations:
            generations.append(generation_id)
        payload["status"] = (
            "reports_generated"
            if job_payload.get("terminal")
            else "generating"
        )

    _update_manifest(session, version, manifest_update)
    return resource


def _matched_fields(text: str) -> list[str]:
    return [
        field
        for field, pattern in _FIELD_PATTERNS.items()
        if pattern.search(text or "")
    ]


def _report_metrics(report: str) -> Dict[str, int]:
    lines = (report or "").splitlines()
    return {
        "chars": len(report or ""),
        "lines": len(lines),
        "headings": sum(
            bool(re.match(r"^#{1,6}\s+", line)) for line in lines
        ),
        "bullet_lines": sum(
            bool(re.match(r"^\s*[-*+]\s+", line)) for line in lines
        ),
        "table_rows": sum(
            line.count("|") >= 2 for line in lines
        ),
    }


def record_dialogue_contract(
    session,
    version: str,
    reports: Dict[str, str],
    traces: Optional[Dict[str, Dict[str, Any]]] = None,
    source: str = "generation",
) -> Dict[str, Any]:
    traces = traces or {}
    cases_by_id = {
        str(item.get("case_id")): item for item in session.cases
    }

    def update(payload):
        cases = payload.setdefault("cases", {})
        for case_id, report in reports.items():
            source_case = cases_by_id.get(str(case_id), {})
            turns = list(source_case.get("turns") or [])
            trace_rounds = list(
                (traces.get(case_id) or {}).get("rounds", [])
            )
            # 已成功导入的报告来自最后一轮输出；只把此前轮次视为追问，
            # 防止报告正文里的“背景/假设”等词被误判成 assistant 提问。
            question_rounds = trace_rounds[:-1]
            assistant_outputs = "\n".join(
                str(item.get("output") or "")
                for item in question_rounds
            )
            user_answers = "\n".join(
                str(
                    item.get("prompt")
                    or item.get("input")
                    or item.get("text")
                    or ""
                )
                for item in turns[1:]
                if isinstance(item, dict)
            )
            all_user_inputs = "\n".join(
                [
                    str(source_case.get("input") or ""),
                    *[
                        str(
                            item.get("prompt")
                            or item.get("input")
                            or item.get("text")
                            or ""
                        )
                        for item in turns
                        if isinstance(item, dict)
                    ],
                ]
            )
            asked = _matched_fields(assistant_outputs)
            answered = _matched_fields(user_answers)
            provided = _matched_fields(all_user_inputs)
            unanswered = sorted(set(asked) - set(answered))
            missing_expected = sorted(
                set(payload.get("expected_fields") or []) - set(provided)
            )
            contract_gaps = bool(unanswered or missing_expected)
            cases[str(case_id)] = {
                "source": source,
                "turn_count": len(turns),
                "asked_fields": asked,
                "answered_fields": answered,
                "provided_fields": provided,
                "unanswered_asked_fields": unanswered,
                "missing_expected_fields": missing_expected,
                "final_delivery_observed": bool(report),
                "final_delivery_forced": bool(report) and contract_gaps,
                "conversation_contract_passed": not contract_gaps,
                "report_metrics": _report_metrics(report),
                "report_sha256": _text_hash(report),
                "generation_trace_available": bool(traces.get(case_id)),
            }
        values = list(cases.values())
        failed = [
            case_id
            for case_id, item in cases.items()
            if not item.get("conversation_contract_passed")
        ]
        payload["status"] = "captured"
        payload["summary"] = {
            "case_count": len(values),
            "passed_cases": len(values) - len(failed),
            "failed_cases": len(failed),
            "failed_case_ids": sorted(failed),
            "forced_finalization_cases": sum(
                bool(item.get("final_delivery_forced")) for item in values
            ),
        }

    return _update(
        session,
        version,
        "dialogue_contract.json",
        update,
    )


def record_judge_run(
    session,
    version: str,
    judge_run_id: str,
    results: Iterable[Dict[str, Any]],
    config: Dict[str, Any],
) -> Dict[str, Any]:
    results = list(results or [])
    calls = [
        call
        for result in results
        for call in ((result.get("judge_trace") or {}).get("calls") or [])
        if isinstance(call, dict)
    ]
    run_summary = {
        "judge_run_id": judge_run_id,
        "status": (
            "completed"
            if results and all(
                item.get("status") in {"judged", "skipped_existing"}
                for item in results
            )
            else "partial"
        ),
        "strategy": config.get("strategy"),
        "backend": config.get("backend"),
        "model": config.get("model"),
        "reasoning_effort": config.get("reasoning_effort"),
        "parallel": config.get("parallel"),
        "max_retries": config.get("max_retries"),
        "case_results": len(results),
        "judged_cases": sum(
            item.get("status") == "judged" for item in results
        ),
        "failed_cases": sum(
            item.get("status") not in {"judged", "skipped_existing"}
            for item in results
        ),
        "model_calls": len(calls),
        "retry_calls": sum(
            int(call.get("retry") or 0) > 0
            or int(call.get("attempt") or 1) > 1
            for call in calls
        ),
        "prompt_chars": sum(int(call.get("promptChars") or 0) for call in calls),
        "response_chars": sum(
            len(str(call.get("response") or "")) for call in calls
        ),
        "duration_ms": sum(
            int(call.get("durationMs") or 0) for call in calls
        ),
    }

    def update(payload):
        runs = payload["judge"].setdefault("runs", {})
        runs[judge_run_id] = run_summary
        values = list(runs.values())
        payload["judge"]["totals"] = {
            "runs": len(values),
            "case_results": sum(item["case_results"] for item in values),
            "judged_cases": sum(item["judged_cases"] for item in values),
            "failed_cases": sum(item["failed_cases"] for item in values),
            "model_calls": sum(item["model_calls"] for item in values),
            "retry_calls": sum(item["retry_calls"] for item in values),
            "prompt_chars": sum(item["prompt_chars"] for item in values),
            "response_chars": sum(item["response_chars"] for item in values),
            "wall_time_ms": sum(item["duration_ms"] for item in values),
        }

    resource = _update(
        session,
        version,
        "resource_usage.json",
        update,
    )

    def manifest_update(payload):
        ids = payload["correlation"].setdefault("judge_run_ids", [])
        if judge_run_id not in ids:
            ids.append(judge_run_id)
        payload["status"] = "judged"

    _update_manifest(session, version, manifest_update)
    return resource


def _failed_case_ids(entry: Dict[str, Any]) -> set[str]:
    return {
        str(record.case_id)
        for record in (entry.get("_recs") or [])
        if getattr(record, "case_failed_gate", False)
    }


def _champion(session) -> Optional[Dict[str, Any]]:
    return optimizer_pipeline.champion_entry(session)


def champion_version(session) -> Optional[str]:
    entry = _champion(session)
    return str(entry.get("version")) if entry else None


def record_gate_decision(
    session,
    candidate: Dict[str, Any],
    champion: Dict[str, Any],
    decision: str,
    reasons: Dict[str, Any],
    target_dims: Iterable[str],
    tolerance: float,
    current_before: str,
    champion_before: Optional[str],
    source_parent: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    version = str(candidate["version"])
    champion_scores = dict(champion.get("dev") or {})
    candidate_scores = dict(candidate.get("dev") or {})
    dims = list(getattr(session, "dims", []))
    score_delta = {
        dim: round(
            candidate_scores.get(dim, 0.0)
            - champion_scores.get(dim, 0.0),
            3,
        )
        for dim in (*dims, "overall")
    }
    regressed = [
        dim for dim in dims if score_delta.get(dim, 0.0) < -float(tolerance)
    ]
    champion_failed = _failed_case_ids(champion)
    candidate_failed = _failed_case_ids(candidate)
    current_after = session._current().get("version")
    champion_after_entry = _champion(session)
    champion_after = (
        champion_after_entry.get("version")
        if champion_after_entry
        else None
    )
    failed_case_increase = len(candidate_failed) > len(champion_failed)
    invariants = [
        {
            "name": "no_dimension_regression_including_targets",
            "passed": not regressed,
            "detail": {"regressed_dims": regressed, "tolerance": tolerance},
        },
        {
            "name": "minimum_effective_overall_improvement",
            "passed": bool(reasons.get("effective_overall_improvement")),
            "detail": {
                "overall_delta": score_delta.get("overall"),
                "minimum": reasons.get("min_overall_improvement"),
            },
        },
        {
            "name": "pareto_hard_failures_not_worse",
            "passed": bool(reasons.get("hard_failures_not_worse")),
            "detail": {
                "champion": [
                    reasons.get("champion_redline_failures"),
                    reasons.get("champion_hard_floor_failures"),
                ],
                "candidate": [
                    reasons.get("candidate_redline_failures"),
                    reasons.get("candidate_hard_floor_failures"),
                ],
            },
        },
        {
            "name": "redline_failure_key_churn_is_diagnostic_only",
            "passed": bool(
                reasons.get("hard_failures_not_worse")
            ),
            "detail": {
                "keys_comparable": reasons.get(
                    "redline_failure_keys_comparable"
                ),
                "verified": reasons.get(
                    "redline_failure_keys_verified"
                ),
                "new_failure_keys": reasons.get(
                    "new_redline_failure_keys",
                    [],
                ),
                "resolved_failure_keys": reasons.get(
                    "resolved_redline_failure_keys",
                    [],
                ),
                "new_keys_are_gate_veto": False,
            },
        },
        {
            "name": "target_check_not_materially_worse",
            "passed": bool(reasons.get("target_check_stable")),
            "detail": {
                "available": reasons.get("target_check_available"),
                "delta": reasons.get("target_check_delta"),
                "drop_tolerance": reasons.get(
                    "target_check_drop_tolerance"
                ),
            },
        },
        {
            "name": "failed_case_count_not_increased",
            "passed": not failed_case_increase,
            "detail": {
                "champion": len(champion_failed),
                "candidate": len(candidate_failed),
                "new_failed_case_ids": sorted(
                    candidate_failed - champion_failed
                ),
            },
        },
        {
            "name": "holdout_guard_for_selected_path",
            "passed": bool(
                (
                    reasons.get("hard_failures_improved")
                    and (
                        not reasons.get("holdout_available")
                        or reasons.get("holdout_guard_passed")
                    )
                )
                or (
                    reasons.get("hard_failures_equal")
                    and reasons.get("holdout_passed")
                )
            ),
            "detail": {
                "available": reasons.get("holdout_available"),
                "guard_passed": reasons.get("holdout_guard_passed"),
                "hard_failures_not_worse": reasons.get(
                    "holdout_hard_not_worse"
                ),
                "overall_delta": reasons.get("holdout_overall_delta"),
                "regressed_dims": reasons.get("holdout_regressed_dims", []),
            },
        },
    ]

    def update(payload):
        payload.update(
            {
                "status": "settled",
                "decision_rule_version": "gate/v4-net-hard-improvement",
                "decision": decision,
                "candidate_version": version,
                "parent_version": (
                    (source_parent or {}).get("version")
                    or candidate.get("parent")
                ),
                "comparison_baseline_version": champion.get("version"),
                "comparison_baseline": "historical_champion",
                "target_dims": list(target_dims or []),
                "tolerance": tolerance,
                "scores": {
                    "champion": champion_scores,
                    "candidate": candidate_scores,
                    "delta": score_delta,
                },
                "failed_cases": {
                    "champion_count": len(champion_failed),
                    "candidate_count": len(candidate_failed),
                    "new_failed_case_ids": sorted(
                        candidate_failed - champion_failed
                    ),
                    "resolved_case_ids": sorted(
                        champion_failed - candidate_failed
                    ),
                },
                "invariants": invariants,
                "current_pointer": {
                    "before": current_before,
                    "after": current_after,
                },
                "champion": {
                    "before": champion_before,
                    "after": champion_after,
                },
                "gate_reasons": reasons,
                "lexicographic_objective": {
                    "hard_constraint": "pareto_hard_failures_not_worse",
                    "order": [
                        "redline_failures",
                        "hard_floor_failures",
                        "overall",
                        "target_checks",
                    ],
                    "adoption_path": reasons.get("adoption_path"),
                    "target_check": reasons.get("target_check", {}),
                },
            }
        )

    gate = _update(
        session,
        version,
        "gate_decision.json",
        update,
    )

    def manifest_update(payload):
        payload["status"] = "settled"
        payload["decision"] = decision
        payload["champion_before"] = champion_before
        payload["champion_after"] = champion_after

    _update_manifest(session, version, manifest_update)
    return gate
