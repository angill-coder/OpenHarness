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
    return {
        "accepted": improved and regressed is None and not red_line_new,
        "improved": improved,
        "regressed_dimension": regressed,
        "red_line_new": red_line_new,
    }
