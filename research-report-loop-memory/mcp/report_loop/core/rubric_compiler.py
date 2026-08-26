"""Compile immutable Base Rubrics with one frozen Memory Resolution Plan."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any


def _hash(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def compile_rubric(
    base_rubric: dict[str, Any],
    *,
    memory_snapshot: dict[str, Any],
    resolution_plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply an already validated plan. This function never interprets Memory."""

    compiled = copy.deepcopy(base_rubric)
    memory = {str(item.get("id")): item for item in memory_snapshot.get("items") or []}
    dimensions = {str(item.get("name")): item for item in compiled.get("dimensions") or []}
    checks = {
        str(check.get("id")): (dimension, check)
        for dimension in compiled.get("dimensions") or []
        for check in dimension.get("checks") or []
    }
    applied: list[str] = []
    warnings = list(memory_snapshot.get("warnings") or [])
    if resolution_plan.get("status") == "resolved":
        for decision in resolution_plan.get("decisions") or []:
            memory_id = str(decision.get("memoryId") or "")
            item = memory.get(memory_id)
            mode = decision.get("mode")
            if not item or mode == "ignore":
                continue
            dimension_name = str(decision.get("dimension") or "")
            dimension = dimensions.get(dimension_name)
            text = str(decision.get("judgeText") or "").strip()
            if not dimension or not text:
                warnings.append(f"resolution_compile_invalid:{memory_id}")
                continue
            metadata = {
                "memoryId": memory_id,
                "scope": item.get("scope"),
                "scopeValue": item.get("scopeValue"),
                "sourceL1Ids": list(item.get("sourceL1Ids") or []),
                "resolutionMode": mode,
            }
            if mode == "additional":
                check_id = f"M-{hashlib.sha256(memory_id.encode('utf-8')).hexdigest()[:10]}"
                dimension.setdefault("checks", []).append({
                    "id": check_id,
                    "label": "Memory Rubric",
                    "desc": text,
                    "effect": "未满足时按本维度正常降档",
                    "redline": False,
                    "memory": metadata,
                })
                checks[check_id] = (dimension, dimension["checks"][-1])
            elif mode == "interpret":
                target = str(decision.get("targetCheckId") or "")
                target_value = checks.get(target)
                if not target_value or target_value[0] is not dimension or target_value[1].get("redline"):
                    warnings.append(f"resolution_compile_locked_or_missing:{memory_id}:{target}")
                    continue
                check = target_value[1]
                check["desc"] = (
                    f"{str(check.get('desc') or '').rstrip()} "
                    f"本轮评判以以下场景解释为准（Base Check 保留为通用背景）：{text}"
                )
                check.setdefault("memoryInterpretations", []).append(metadata)
            else:
                warnings.append(f"resolution_compile_unknown_mode:{memory_id}:{mode}")
                continue
            applied.append(memory_id)

    material = {
        "baseVersion": base_rubric.get("version"),
        "memoryRevision": memory_snapshot.get("revision"),
        "resolutionPlan": resolution_plan,
    }
    metadata = {
        "status": memory_snapshot.get("status"),
        "revision": memory_snapshot.get("revision"),
        "rubricSetVersion": memory_snapshot.get("rubricSetVersion"),
        "baseRubricVersion": base_rubric.get("version"),
        "items": list(memory.values()),
        "appliedMemoryRubricIds": applied,
        "resolutionStatus": resolution_plan.get("status"),
        "resolutionPromptVersion": resolution_plan.get("promptVersion"),
        "resolutionPlanHash": _hash(resolution_plan),
        "resolverHash": _hash(material),
        "warnings": warnings,
    }
    compiled["baseVersion"] = base_rubric.get("version")
    compiled["memoryResolution"] = {
        "status": metadata["resolutionStatus"],
        "planHash": metadata["resolutionPlanHash"],
        "appliedMemoryRubricIds": applied,
    }
    return compiled, metadata
