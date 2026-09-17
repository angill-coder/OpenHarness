"""Candidate-adoption gate without the legacy Skill Loop runtime."""

from __future__ import annotations


def _dim_regressed(new_scores, old_scores, dims_to_watch, tolerance, all_dims):
    for dimension in all_dims:
        if dimension in dims_to_watch:
            continue
        if old_scores.get(dimension, 0) - new_scores.get(dimension, 0) > tolerance:
            return dimension
    return None


def evaluate_candidate_gate(
    candidate_scores,
    current_scores,
    target_dims,
    all_dims,
    drop_tolerance=0.15,
):
    watched = list(target_dims or all_dims)
    comparable = [
        dimension
        for dimension in watched
        if dimension in candidate_scores and dimension in current_scores
    ] or ["overall"]
    improved = any(
        candidate_scores.get(dimension, 0) - current_scores.get(dimension, 0) > 0.001
        for dimension in comparable
    )
    regressed = _dim_regressed(
        candidate_scores, current_scores, watched, drop_tolerance, all_dims
    )
    red_line_new = (
        candidate_scores.get("red_line_fails", 0)
        > current_scores.get("red_line_fails", 0)
    )
    overall_missing = (
        "overall" not in candidate_scores or "overall" not in current_scores
    )
    candidate_overall = (
        float(candidate_scores["overall"]) if not overall_missing else None
    )
    current_overall = (
        float(current_scores["overall"]) if not overall_missing else None
    )
    overall_delta = (
        round(candidate_overall - current_overall, 3)
        if candidate_overall is not None and current_overall is not None
        else None
    )
    overall_regressed = overall_delta is not None and overall_delta < 0
    overall_non_decreasing = not overall_missing and not overall_regressed
    return {
        "accepted": (
            improved
            and regressed is None
            and not red_line_new
            and overall_non_decreasing
        ),
        "improved": improved,
        "regressed_dimension": regressed,
        "red_line_new": red_line_new,
        "overall_missing": overall_missing,
        "overall_regressed": overall_regressed,
        "overall_non_decreasing": overall_non_decreasing,
        "overall_delta": overall_delta,
    }
