"""Resolve independent Memory Rubrics against the immutable Base Rubric once per run."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable


PROMPT_VERSION = "memory-resolution-principles-v3-scope-resolution"
_MODES = {"additional", "interpret", "ignore"}


def _base_index(base_rubric: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "dimension": dimension.get("name"),
            "dimensionName": dimension.get("name_zh"),
            "checks": [
                {
                    "id": check.get("id"),
                    "label": check.get("label"),
                    "desc": check.get("desc"),
                    "redline": bool(check.get("redline")),
                }
                for check in dimension.get("checks") or []
            ],
        }
        for dimension in base_rubric.get("dimensions") or []
    ]


def build_resolution_prompt(
    base_rubric: dict[str, Any],
    memory_snapshot: dict[str, Any],
    *,
    task: str,
    audience: str,
    project: str,
    source_evidence: list[dict[str, Any]] | None = None,
    allow_source_review: bool = True,
) -> str:
    payload = {
        "task": task,
        "audience": audience,
        "project": project,
        "baseRubric": _base_index(base_rubric),
        "memoryRubrics": memory_snapshot.get("items") or [],
    }
    if source_evidence is not None:
        payload["sourceL1Evidence"] = source_evidence
    source_instruction = (
        "若某条 Memory Rubric 的原始意图或适用范围无法从当前内容判断，可在首轮仅返回 "
        '{"schemaVersion":1,"inspectSourceFor":["Memory Rubric ID"],"decisions":[]}；'
        "信息充分时不要溯源。"
        if allow_source_review
        else "已提供你请求的 sourceL1 证据；现在必须完成最终判断，不得再次请求溯源。"
    )
    return f"""你是 Rubric Resolution Judge。你只负责解释本轮应如何把独立 Memory Rubrics 应用到不可修改的 Base Rubrics；不要评审报告，也不要改写 Memory。

只遵守三条原则：
1. Base Rubrics 永远保留，不删除、不降权、不改写红线。
2. 结合当前任务理解 Memory：scope/scopeValue 是来源与适用范围线索，不是精确字符串路由规则；根据 task、audience、project 的语义判断是否适用，必要时溯源。若它提供新的可观察标准，用 additional；若它细化或替代某条非红线 Base Check 在本场景的适用方式，用 interpret；重复、无关或无法可靠判断时用 ignore。不要把相互矛盾的要求并列为 additional。
3. 每条 Memory 只落到一个现有六维维度；保持克制，不创建第七维，不改变维度权重。

{source_instruction}

返回严格 JSON：
{{"schemaVersion":1,"inspectSourceFor":[],"decisions":[{{"memoryId":"...","mode":"additional|interpret|ignore","dimension":"六维之一或null","targetCheckId":"interpret时必填，否则null","judgeText":"additional/interpret时的简洁可执行判据，ignore时为空","reason":"简短理由"}}]}}

输入：
{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}
"""


def _source_review_requests(raw: dict[str, Any], memory_snapshot: dict[str, Any]) -> list[str]:
    requested = raw.get("inspectSourceFor") or []
    if not isinstance(requested, list):
        raise ValueError("invalid_source_review_request")
    memory_ids = {str(item.get("id")) for item in memory_snapshot.get("items") or []}
    normalized = list(dict.fromkeys(str(value or "").strip() for value in requested))
    if any(not value or value not in memory_ids for value in normalized):
        raise ValueError("invalid_source_review_memory_id")
    return normalized


def _source_evidence(
    requested_memory_ids: list[str],
    memory_snapshot: dict[str, Any],
    load_sources: Callable[[list[str]], list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[str]]:
    by_memory_id = {
        str(item.get("id")): list(dict.fromkeys(str(value) for value in item.get("sourceL1Ids") or []))
        for item in memory_snapshot.get("items") or []
    }
    requested_source_ids = list(dict.fromkeys(
        source_id
        for memory_id in requested_memory_ids
        for source_id in by_memory_id.get(memory_id, [])
    ))
    loaded = load_sources(requested_source_ids)
    loaded_by_id = {str(item.get("id")): item for item in loaded}
    evidence = []
    for memory_id in requested_memory_ids:
        source_ids = by_memory_id.get(memory_id, [])
        evidence.append({
            "memoryId": memory_id,
            "sources": [loaded_by_id[value] for value in source_ids if value in loaded_by_id],
            "missingSourceL1Ids": [value for value in source_ids if value not in loaded_by_id],
        })
    return evidence, [value for value in requested_source_ids if value in loaded_by_id]


def validate_resolution_plan(
    raw: dict[str, Any],
    base_rubric: dict[str, Any],
    memory_snapshot: dict[str, Any],
) -> dict[str, Any]:
    dimensions = {str(item.get("name")): item for item in base_rubric.get("dimensions") or []}
    checks = {
        str(check.get("id")): (str(dimension.get("name")), bool(check.get("redline")))
        for dimension in base_rubric.get("dimensions") or []
        for check in dimension.get("checks") or []
    }
    memory_ids = {str(item.get("id")) for item in memory_snapshot.get("items") or []}
    decisions = raw.get("decisions")
    if raw.get("schemaVersion") != 1 or not isinstance(decisions, list):
        raise ValueError("invalid_resolution_plan_schema")
    seen: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for decision in decisions:
        if not isinstance(decision, dict):
            raise ValueError("invalid_resolution_decision")
        memory_id = str(decision.get("memoryId") or "").strip()
        mode = str(decision.get("mode") or "").strip()
        dimension = decision.get("dimension")
        target = decision.get("targetCheckId")
        text = str(decision.get("judgeText") or "").strip()
        if memory_id not in memory_ids or memory_id in seen or mode not in _MODES:
            raise ValueError(f"invalid_resolution_identity:{memory_id}")
        seen.add(memory_id)
        if mode == "ignore":
            dimension, target, text = None, None, ""
        else:
            dimension = str(dimension or "").strip()
            if dimension not in dimensions or not text:
                raise ValueError(f"invalid_resolution_target:{memory_id}")
            if mode == "additional":
                target = None
            else:
                target = str(target or "").strip()
                if target not in checks or checks[target][0] != dimension or checks[target][1]:
                    raise ValueError(f"invalid_interpret_target:{memory_id}:{target}")
        normalized.append({
            "memoryId": memory_id,
            "mode": mode,
            "dimension": dimension,
            "targetCheckId": target,
            "judgeText": text,
            "reason": str(decision.get("reason") or "").strip(),
        })
    if seen != memory_ids:
        raise ValueError("resolution_plan_incomplete")
    return {"schemaVersion": 1, "status": "resolved", "promptVersion": PROMPT_VERSION, "decisions": normalized}


def failed_resolution_plan(memory_snapshot: dict[str, Any], error: str) -> dict[str, Any]:
    return {
        "schemaVersion": 1,
        "status": "failed_base_only",
        "promptVersion": PROMPT_VERSION,
        "error": str(error),
        "decisions": [],
        "ignoredMemoryIds": [str(item.get("id")) for item in memory_snapshot.get("items") or []],
    }


def resolve_memory_rubrics(
    base_rubric: dict[str, Any],
    memory_snapshot: dict[str, Any],
    *,
    task: str,
    audience: str,
    project: str,
    call_model: Callable[[str], str],
    extract_json: Callable[[str], dict[str, Any]],
    load_sources: Callable[[list[str]], list[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    if not memory_snapshot.get("items"):
        return {"schemaVersion": 1, "status": "skipped_no_memory", "promptVersion": PROMPT_VERSION, "decisions": []}
    prompt = build_resolution_prompt(base_rubric, memory_snapshot, task=task, audience=audience, project=project)
    raw = extract_json(call_model(prompt))
    requested_memory_ids = _source_review_requests(raw, memory_snapshot)
    consulted_source_ids: list[str] = []
    source_prompt: str | None = None
    if requested_memory_ids:
        if load_sources is None:
            raise ValueError("source_review_unavailable")
        evidence, consulted_source_ids = _source_evidence(requested_memory_ids, memory_snapshot, load_sources)
        source_prompt = build_resolution_prompt(
            base_rubric,
            memory_snapshot,
            task=task,
            audience=audience,
            project=project,
            source_evidence=evidence,
            allow_source_review=False,
        )
        raw = extract_json(call_model(source_prompt))
        if _source_review_requests(raw, memory_snapshot):
            raise ValueError("repeated_source_review_request")
    plan = validate_resolution_plan(raw, base_rubric, memory_snapshot)
    plan["promptSha256"] = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if source_prompt is not None:
        plan["sourcePromptSha256"] = hashlib.sha256(source_prompt.encode("utf-8")).hexdigest()
        plan["consultedSourceL1Ids"] = consulted_source_ids
    return plan
