"""Resolve a versioned Rubric Set into one frozen Judge rubric."""

from __future__ import annotations

import copy
import hashlib
import json
from collections import OrderedDict
from typing import Any

from .memory_rubric_provider import MemoryRubricProvider


_PERSONAL_WEIGHT = 0.10
_SCOPE_PRIORITY = {"core": 0, "audience": 1, "project": 2}


def _personal_dimension() -> dict[str, Any]:
    return {
        "name": "personal",
        "name_zh": "个性化要求",
        "scale": [1, 5],
        "weight": _PERSONAL_WEIGHT,
        "hard_floor": None,
        "is_reverse": False,
        "criteria": "评价无法归入基础六维、但可从报告中直接观察的长期个性化写作要求。",
        "anchors": {
            "5": "全部适用的个性化要求均清晰满足。",
            "4": "基本满足，仅有一处轻微偏差。",
            "3": "部分满足，但存在影响用户使用体验的明显偏差。",
            "2": "多数要求未满足。",
            "1": "基本未体现适用的个性化要求。",
        },
        "positive_example": "报告稳定遵守用户已经确认的长期个性化标准。",
        "negative_example": "报告忽略已生效的个性化标准。",
        "checks": [],
    }


def _join_description(base: str, requirements: OrderedDict[str, str]) -> str:
    if not requirements:
        return base
    additions = "；".join(value.rstrip("。；") for value in requirements.values())
    return f"{base.rstrip('。；')}；个性化补充：{additions}。"


def _join_effect(base: str, additions: list[str]) -> str:
    values = [base.rstrip("。；"), *[value.rstrip("。；") for value in additions]]
    unique = list(dict.fromkeys(value for value in values if value))
    return "；".join(unique) + ("。" if unique else "")


def compile_rubric(
    base_rubric: dict[str, Any],
    *,
    provider: MemoryRubricProvider | None,
    audience: str = "",
    project: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Apply Base -> Core -> Audience -> Project overlays without an LLM call."""

    compiled = copy.deepcopy(base_rubric)
    base_version = str(compiled.get("version") or "base")
    snapshot = (
        provider.load(audience=audience, project=project)
        if provider is not None
        else {
            "status": "disabled",
            "revision": None,
            "rubricSetVersion": None,
            "documents": [],
            "items": [],
            "warnings": [],
        }
    )
    warnings = list(snapshot.get("warnings") or [])
    dimensions = {
        str(item.get("name") or ""): item
        for item in compiled.get("dimensions") or []
    }
    slots: dict[str, dict[str, Any]] = {}
    check_ids: set[str] = set()
    for dimension in compiled.get("dimensions") or []:
        dimension_name = str(dimension.get("name") or "")
        for check in dimension.get("checks") or []:
            check_id = str(check.get("id") or "").strip()
            criterion_key = str(
                check.get("criterionKey")
                or f"{dimension_name}.{check_id.lower()}"
            ).strip()
            if not check_id or criterion_key in slots:
                raise ValueError(f"invalid base rubric criterion: {criterion_key}")
            check["criterionKey"] = criterion_key
            check_ids.add(check_id)
            slots[criterion_key] = {
                "check": check,
                "dimension": dimension,
                "baseDescription": str(check.get("desc") or ""),
                "baseEffect": str(check.get("effect") or ""),
                "requirements": OrderedDict(),
                "effectAdditions": [],
                "overlays": [],
                "origin": "base",
            }

    included: list[dict[str, Any]] = []
    items = sorted(
        snapshot.get("items") or [],
        key=lambda item: (
            _SCOPE_PRIORITY.get(str(item.get("scope") or "core"), 0),
            str(item.get("criterionKey") or item.get("id") or ""),
            str(item.get("id") or ""),
        ),
    )
    for item in items:
        item_id = str(item.get("id") or "").strip()
        criterion_key = str(item.get("criterionKey") or item_id).strip()
        dimension_name = str(item.get("dimension") or "").strip()
        operation = str(item.get("operation") or "add").strip()
        if not item_id or not criterion_key:
            warnings.append("memory_rubric_missing_identity")
            continue
        if item.get("redline") is not False or item.get("status") != "active":
            warnings.append(f"memory_rubric_not_active_non_redline:{item_id}")
            continue
        if operation not in {"add", "extend", "override", "disable"}:
            warnings.append(f"memory_rubric_unknown_operation:{item_id}:{operation}")
            continue

        slot = slots.get(criterion_key)
        if operation == "add" and slot is not None:
            if slot["origin"] == "memory" and slot["check"].get("id") == item_id:
                operation = "override"  # schema-v1 compatibility across scopes
            else:
                warnings.append(f"memory_rubric_add_existing_criterion:{item_id}:{criterion_key}")
                continue

        if operation == "add":
            if dimension_name not in dimensions:
                if dimension_name != "personal":
                    warnings.append(f"memory_rubric_unknown_dimension:{item_id}:{dimension_name}")
                    continue
                dimensions["personal"] = _personal_dimension()
                compiled.setdefault("dimensions", []).append(dimensions["personal"])
            if item_id in check_ids:
                warnings.append(f"memory_rubric_id_conflict:{item_id}")
                continue
            check = {
                "id": item_id,
                "criterionKey": criterion_key,
                "label": str(item.get("label") or item_id),
                "desc": str(item.get("desc") or ""),
                "effect": str(item.get("effect") or ""),
                "redline": False,
            }
            if isinstance(item.get("optimizer"), dict):
                check["optimizer"] = copy.deepcopy(item["optimizer"])
            dimensions[dimension_name].setdefault("checks", []).append(check)
            check_ids.add(item_id)
            slot = {
                "check": check,
                "dimension": dimensions[dimension_name],
                "baseDescription": check["desc"],
                "baseEffect": check["effect"],
                "requirements": OrderedDict(),
                "effectAdditions": [],
                "overlays": [],
                "origin": "memory",
            }
            slots[criterion_key] = slot
        elif slot is None:
            warnings.append(f"memory_rubric_target_not_found:{item_id}:{criterion_key}")
            continue

        assert slot is not None
        check = slot["check"]
        if str(slot["dimension"].get("name") or "") != dimension_name:
            warnings.append(f"memory_rubric_dimension_mismatch:{item_id}:{criterion_key}")
            continue
        if operation in {"override", "disable"} and check.get("redline"):
            warnings.append(f"memory_rubric_locked_redline:{item_id}:{criterion_key}")
            continue

        overlay_meta = {
            "id": item_id,
            "operation": operation,
            "scope": item.get("scope"),
            "scopeValue": item.get("scopeValue"),
            "sourceL1Ids": list(item.get("sourceL1Ids") or []),
            "sourcePath": item.get("sourcePath"),
        }
        if operation == "extend":
            requirements = item.get("requirements") or []
            if not requirements:
                warnings.append(f"memory_rubric_extend_without_requirements:{item_id}")
                continue
            for requirement in requirements:
                key = str(requirement.get("key") or "").strip()
                text = str(requirement.get("text") or "").strip()
                if key and text:
                    slot["requirements"][key] = text
            effect = str(item.get("effect") or "").strip()
            if effect:
                slot["effectAdditions"].append(effect)
            check["desc"] = _join_description(slot["baseDescription"], slot["requirements"])
            check["effect"] = _join_effect(slot["baseEffect"], slot["effectAdditions"])
        elif operation == "override":
            check.update({
                "label": str(item.get("label") or check.get("label") or item_id),
                "desc": str(item.get("desc") or ""),
                "effect": str(item.get("effect") or ""),
                "redline": False,
            })
            slot["baseDescription"] = check["desc"]
            slot["baseEffect"] = check["effect"]
            slot["requirements"] = OrderedDict()
            slot["effectAdditions"] = []
            if isinstance(item.get("optimizer"), dict):
                check["optimizer"] = copy.deepcopy(item["optimizer"])
        elif operation == "disable":
            slot["dimension"]["checks"] = [
                value for value in slot["dimension"].get("checks") or []
                if value is not check
            ]
            del slots[criterion_key]
        slot["overlays"].append(overlay_meta)
        if operation != "disable":
            check["memory"] = {
                "criterionKey": criterion_key,
                "overlays": copy.deepcopy(slot["overlays"]),
            }
        included.append({
            "id": item_id,
            "criterionKey": criterion_key,
            "operation": operation,
            "dimension": dimension_name,
            "scope": item.get("scope"),
            "scopeValue": item.get("scopeValue"),
            "sourcePath": item.get("sourcePath"),
        })

    personal = dimensions.get("personal")
    personal_active = bool(personal and personal.get("checks"))
    if personal and not personal_active:
        compiled["dimensions"] = [
            dimension for dimension in compiled.get("dimensions") or []
            if dimension.get("name") != "personal"
        ]
    if personal_active:
        base_dimensions = [
            dimension for dimension in compiled.get("dimensions") or []
            if dimension.get("name") != "personal"
        ]
        total = sum(float(dimension.get("weight") or 0) for dimension in base_dimensions)
        if total <= 0:
            raise ValueError("base rubric weights must be positive")
        for dimension in base_dimensions:
            dimension["weight"] = round(
                float(dimension.get("weight") or 0) / total * (1 - _PERSONAL_WEIGHT),
                6,
            )
        personal["weight"] = _PERSONAL_WEIGHT

    resolver_material = {
        "revision": snapshot.get("revision"),
        "rubricSetVersion": snapshot.get("rubricSetVersion"),
        "audience": audience.strip(),
        "project": project.strip(),
        "items": included,
    }
    resolver_hash = hashlib.sha256(
        json.dumps(resolver_material, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    rubric_set_version = snapshot.get("rubricSetVersion")
    if rubric_set_version:
        compiled["baseVersion"] = base_version
        compiled["version"] = rubric_set_version
    metadata = {
        "status": snapshot.get("status"),
        "revision": snapshot.get("revision"),
        "rubricSetVersion": rubric_set_version,
        "baseRubricVersion": base_version,
        "resolverHash": resolver_hash,
        "personalActive": personal_active,
        "selectedScopes": {
            "core": True,
            "audience": audience.strip() or None,
            "project": project.strip() or None,
        },
        "documents": list(snapshot.get("documents") or []),
        "items": included,
        "warnings": warnings,
    }
    compiled["_rubricSet"] = metadata
    return compiled, metadata
