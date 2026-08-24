"""Authoritative check-to-score conversion for Report Loop."""

from __future__ import annotations

from typing import Any, Dict


def overall(scores: Dict[str, float], rubric: Dict[str, Any]) -> float:
    weights = {item["name"]: item["weight"] for item in rubric["dimensions"]}
    return round(sum(scores[key] * weights[key] for key in scores if key in weights), 3)


def dim_from_checks(check_scores: Dict[str, float], rubric: Dict[str, Any]) -> Dict[str, float]:
    result = {}
    for dimension in rubric["dimensions"]:
        values = []
        redline_hit = False
        for check in dimension.get("checks") or []:
            value = check_scores.get(check["id"])
            if value is None:
                continue
            numeric = float(value)
            values.append(numeric)
            redline_hit = redline_hit or bool(check.get("redline") and numeric <= 0)
        if not values:
            continue
        score = 1 + 4 * (sum(values) / len(values))
        if redline_hit:
            score = min(score, 2.0)
        result[dimension["name"]] = round(max(1.0, min(5.0, score)), 3)
    return result


def score_check_judgment(check_scores: Dict[str, float], rubric: Dict[str, Any]) -> Dict[str, Any]:
    scores = dim_from_checks(check_scores, rubric)
    redline_checks = []
    hard_floor_failures = []
    for dimension in rubric.get("dimensions", []):
        for check in dimension.get("checks") or []:
            value = check_scores.get(check.get("id"))
            if check.get("redline") and value is not None and float(value) <= 0:
                redline_checks.append(str(check["id"]))
        floor = dimension.get("hard_floor")
        score = scores.get(dimension.get("name"))
        if floor is not None and score is not None and score < float(floor):
            hard_floor_failures.append(str(dimension["name"]))
    return {
        "scores": scores,
        "overall": overall(scores, rubric),
        "redline_checks": redline_checks,
        "hard_floor_failures": hard_floor_failures,
        "case_failed_gate": bool(hard_floor_failures),
    }


_CHECK_LABEL_VALUES = {
    "met": 1.0,
    "partial": 0.5,
    "miss": 0.0,
    1: 1.0,
    1.0: 1.0,
    0.5: 0.5,
    0: 0.0,
    0.0: 0.0,
}


def normalize_check_scores(checks: Dict[str, Any]) -> Dict[str, float]:
    normalized = {}
    for check_id, value in (checks or {}).items():
        if value not in _CHECK_LABEL_VALUES:
            raise ValueError("Invalid Judge check value: %s" % value)
        normalized[str(check_id)] = _CHECK_LABEL_VALUES[value]
    return normalized


def score_labeled_check_judgment(checks: Dict[str, Any], rubric: Dict[str, Any]) -> Dict[str, Any]:
    return score_check_judgment(normalize_check_scores(checks), rubric)
