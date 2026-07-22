# -*- coding: utf-8 -*-
"""
calibration.py — Judge 校准 / meta-eval (对应 Rubric 文档 §6)

拿人工标注比对 judge 分, 算分维度一致率 + 整体一致率。
门槛: 整体一致率 >= 0.85 才允许开优化(否则在优化一个坏裁判)。

一致定义: 分维度 judge 分与人工分相差 <= 1 视为一致(±1 容差, 与 §6 一致)。
"""
from typing import Any, Dict, List


AGREE_TOL = 1
GATE = 0.85


def agreement(records, rubric: Dict[str, Any]) -> Dict[str, Any]:
    dims = [d["name"] for d in rubric["dimensions"]]
    labeled = [r for r in records if r.human_label]
    per_dim = {d: {"agree": 0, "total": 0} for d in dims}
    total_agree = total = 0
    disagreements = []

    for r in labeled:
        for d in dims:
            if d in r.scores and d in r.human_label:
                per_dim[d]["total"] += 1
                total += 1
                if abs(r.scores[d] - r.human_label[d]) <= AGREE_TOL:
                    per_dim[d]["agree"] += 1
                    total_agree += 1
                else:
                    disagreements.append((r.case_id, d, r.scores[d], r.human_label[d]))

    dim_rate = {d: (per_dim[d]["agree"] / per_dim[d]["total"] if per_dim[d]["total"] else 0.0)
                for d in dims}
    overall_rate = total_agree / total if total else 0.0
    # 找出一致率最低的维度(通常是锚点最模糊的那个 -> 该回去补锚点/反例)
    worst = min(dim_rate.items(), key=lambda kv: kv[1]) if dim_rate else (None, 1.0)
    return {
        "n_labeled": len(labeled),
        "overall": round(overall_rate, 3),
        "per_dim": {d: round(v, 3) for d, v in dim_rate.items()},
        "passes_gate": overall_rate >= GATE,
        "gate": GATE,
        "worst_dim": worst[0],
        "worst_rate": round(worst[1], 3),
        "disagreements": disagreements[:5],
    }
