# -*- coding: utf-8 -*-
"""模型 Judge 的批量执行逻辑。

一次 HTTP 操作覆盖当前版本全部 case；每个 case 仍使用独立 Prompt，
以避免报告之间互相污染，并通过有限并发缩短总耗时。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, Iterable, List, Optional


_VALID_CHECK_VALUES = {"met", "partial", "miss", 1, 1.0, 0.5, 0, 0.0}


def _expected_check_ids(rubric: Dict) -> List[str]:
    return [
        str(check["id"])
        for dimension in rubric.get("dimensions", [])
        for check in dimension.get("checks", [])
        if check.get("id")
    ]


def _validate_payload(payload: Dict, expected_ids: Iterable[str]) -> tuple[Dict, Dict]:
    if not isinstance(payload, dict) or not isinstance(payload.get("checks"), dict):
        raise ValueError("judge 输出缺少 checks 对象")
    checks = payload["checks"]
    expected = list(expected_ids)
    missing = [check_id for check_id in expected if check_id not in checks]
    if missing:
        raise ValueError("judge 漏评 check: %s" % ", ".join(missing))
    invalid = [
        check_id
        for check_id in expected
        if checks.get(check_id) not in _VALID_CHECK_VALUES
    ]
    if invalid:
        raise ValueError("judge check 值非法: %s" % ", ".join(invalid))
    reasoning = payload.get("reasoning")
    if not isinstance(reasoning, dict):
        reasoning = {}
    return (
        {check_id: checks[check_id] for check_id in expected},
        {check_id: str(reasoning.get(check_id, "")) for check_id in expected},
    )


def judge_cases(
    cases: Iterable[Dict],
    reports: Dict[str, str],
    rubric: Dict,
    build_prompt: Callable[[Dict, str, Dict], str],
    call_model: Callable[[str], str],
    extract_json: Callable[[str], Dict | None],
    parallel: int = 3,
    on_result: Optional[Callable[[Dict], Optional[Dict]]] = None,
) -> List[Dict]:
    """批量 Judge，返回与输入 case 顺序一致的逐 case 结果。

    单 case 的模型错误或解析错误不会中断其它 case。没有报告的 case 会
    明确标记为 ``missing_report``，由调用方决定是否允许后续版本推进。
    """
    case_list = list(cases)
    expected = _expected_check_ids(rubric)
    if not expected:
        raise ValueError("Rubric 未配置任何 Judge checks")

    def run_one(index_case):
        index, case = index_case
        case_id = str(case.get("case_id") or "")
        report = (reports.get(case_id) or "").strip()
        if not report:
            return index, {
                "case_id": case_id,
                "status": "missing_report",
                "error": "当前版本尚未导入报告",
            }
        case_context = {
            "case_id": case_id,
            "input": case.get("input") or {},
            # ground_truth 只进入 Judge，不会经过 WB loader 发送给生成模型。
            "ground_truth": case.get("ground_truth") or {},
            "audience": case.get("audience"),
            "required_sections": case.get("required_sections") or [],
        }
        try:
            raw = call_model(build_prompt(rubric, report, case_context))
            parsed = extract_json(raw)
            checks, reasoning = _validate_payload(parsed, expected)
            return index, {
                "case_id": case_id,
                "status": "judged",
                "checks": checks,
                "reasoning": reasoning,
            }
        except Exception as exc:
            return index, {
                "case_id": case_id,
                "status": "failed",
                "error": str(exc),
            }

    if not case_list:
        return []
    workers = max(1, min(int(parallel or 1), len(case_list)))
    ordered = [None] * len(case_list)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(run_one, item): item[0]
            for item in enumerate(case_list)
        }
        for future in as_completed(futures):
            index, result = future.result()
            if on_result is not None:
                replacement = on_result(result)
                if replacement is not None:
                    result = replacement
            ordered[index] = result
    return ordered
