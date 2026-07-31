# -*- coding: utf-8 -*-
"""
runner.py — 批量执行 skill 于数据集, 产出 EvalRecord (对应架构文档 RUNNER + JUDGE)

Runner 负责: 取 case -> backend.run -> judge.score_report -> 组装 EvalRecord。
把执行和判分串在一起产出一批可分析的记录。
"""
from typing import Any, Dict, List
from schemas import EvalRecord
import judge as judge_mod


def run_external_cases(request, progress_callback=None, should_cancel=None):
    """统一的真实外部执行入口。

    WorkBuddy 的具体实现通过延迟导入隔离，确保未安装/不可用时不影响原有
    Mock 路径和 OpenHarness 启动。
    """
    from workbuddy_runner import run_external_cases as _run_external_cases

    return _run_external_cases(
        request,
        progress_callback=progress_callback,
        should_cancel=should_cancel,
    )


def run_split(skill, cases: List[Dict[str, Any]], rubric: Dict[str, Any],
              backend, run_id: str) -> List[EvalRecord]:
    records = []
    for case in cases:
        report, trace = backend.run(skill, case)
        scores, reasoning, flagged, failed = judge_mod.score_report(report, case, rubric)
        rec = EvalRecord(
            run_id=run_id, skill_version=skill.version, dataset_split=case["split"],
            case_id=case["case_id"], input=case["input"], trace=trace, output=report,
            scores=scores, judge_reasoning=reasoning, flagged=flagged, case_failed_gate=failed,
        )
        records.append(rec)
    return records


def mean_scores(records: List[EvalRecord], rubric: Dict[str, Any]) -> Dict[str, float]:
    """分维度均分 + overall。空则返回 0。"""
    if not records:
        return {}
    dims = [d["name"] for d in rubric["dimensions"]]
    out = {}
    for dim in dims:
        vals = [r.scores[dim] for r in records if dim in r.scores]
        out[dim] = round(sum(vals) / len(vals), 3) if vals else 0.0
    out["overall"] = round(sum(judge_mod.overall(r.scores, rubric) for r in records) / len(records), 3)
    out["red_line_fails"] = sum(1 for r in records if r.case_failed_gate)
    return out
