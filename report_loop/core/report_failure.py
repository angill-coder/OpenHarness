"""Convert Judge check results into the Report Loop repair input."""

from __future__ import annotations

from typing import Any, Dict


def failure_report_from_checks(
    checks: Dict[str, Any],
    reasoning: Dict[str, Any],
    rubric: Dict[str, Any],
) -> Dict[str, Any]:
    by_check = {}
    dimensions = []
    for dimension in rubric.get("dimensions", []):
        failed = []
        for check in dimension.get("checks", []):
            check_id = str(check["id"])
            value = checks.get(check_id)
            if value in ("met", 1, 1.0):
                continue
            item = {
                "check_id": check_id,
                "label": check.get("label", check_id),
                "status": value,
                "reason": str((reasoning or {}).get(check_id) or ""),
                "redline": bool(check.get("redline")),
                "repairability": check.get("repairability"),
                "requires_human": bool(check.get("requires_human")),
                "needs_material": bool(check.get("needs_material")),
            }
            failed.append(item)
            by_check[check_id] = item
        if failed:
            dimensions.append({
                "dimension": dimension.get("name"),
                "dimension_zh": dimension.get("name_zh", dimension.get("name")),
                "failures": failed,
            })
    summary = [
        "%s %s：%s" % (
            item["check_id"],
            item["label"],
            item["reason"] or item["status"],
        )
        for item in by_check.values()
    ]
    return {
        "dimensions": dimensions,
        "checks": by_check,
        "summary": summary,
        "count": len(by_check),
    }
