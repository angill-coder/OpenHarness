# -*- coding: utf-8 -*-
"""
clustering.py — 失败模式聚类 (对应架构文档 FAILURE CLUSTERING)

把低分 case 按"为什么低分"归成几类, 每类给 {pattern, hit_count, affected_dims,
directive_hint, severity, exemplars}。optimizer 只吃这个结构。

两个产品两套特征规则, 由 product 派发:
  · report-assistant  (算数字型): 读 rec.output 的 flag 字段
  · research_insight  (调研洞察): 真实链路直接读逐 check Judge，mock
    链路读 rec.output["signals"]
"""
from typing import Any, Dict, List, Optional
from collections import defaultdict


# ---------------- 算数字型 ----------------
BIZ_PATTERN_RULES = [
    ("fabrication", "结论/数字编造,不回溯到数据", ["data_accuracy"], "require_citation"),
    ("unhandled_conflict", "数据矛盾未标记,直接取值", ["data_accuracy"], "verifier_check_omissions"),
    ("unit_error", "口径错误(环比当同比)", ["data_accuracy"], "require_metric_definitions"),
    ("uncited", "数字无出处标记,可回溯性差", ["data_accuracy"], "require_citation"),
    ("missing_section", "必需段落缺失", ["completeness"], "enforce_required_sections"),
    ("anomaly_missed", "关键异常漏报", ["completeness"], "verifier_check_omissions"),
    ("shallow_insight", "数据复读,缺归因/风险/下一步", ["insight"], "require_risk_and_next_step"),
    ("no_attribution", "有洞察但归因笼统", ["insight"], "require_attribution"),
    ("length_mismatch", "长度不匹配受众", ["conciseness"], "match_audience_length"),
    ("reward_hacking_suspected", "术语堆砌,疑似讨好裁判", ["conciseness"], None),
]

# ---------------- 调研洞察 ----------------
# 特征键与 ResearchMockBackend 产出的 signals 键一一对应。
RESEARCH_PATTERN_RULES = [
    ("trace_fabrication", "编造/曲解素材里没有的事实", ["traceability"], "verify_no_fabrication"),
    ("trace_conflict", "素材冲突未指出/直接混用", ["traceability"], "flag_source_conflict"),
    ("trace_hardanswer", "素材不足却硬给结论", ["traceability"], "honest_on_unsupportable"),
    ("trace_uncited", "论断无出处,不可回溯", ["traceability"], "require_source_ref"),
    ("trace_single_source", "单一信源结论未降级'待验证'", ["traceability"], "require_two_sources"),
    ("trace_metric_caveat", "选有利口径未注明(轻度曲解)", ["traceability"], "note_metric_caveat"),
    ("trace_sample_bias", "无视样本偏差、把有偏当客观", ["traceability"], "disclose_sample_bias"),
    ("struct_summary", "摘要是背景铺陈而非结论", ["structure"], "summary_format"),
    ("struct_pyramid", "正文非金字塔(支撑先于论点)", ["structure"], "pyramid_body"),
    ("struct_mece", "章节非MECE/缺段/交叉", ["structure", "coverage"], "mece_sections"),
    ("struct_summary_body", "摘要与正文没有严格对应", ["structure"], "summary_format"),
    ("narr_flow", "无清晰主线/章节并列拼接缺推进", ["narrative"], "ensure_narrative_flow"),
    ("narr_drift", "概念漂移/口径前后不一致", ["narrative"], "concept_consistency"),
    ("insight_restate", "素材复述,缺归因/趋势/建议", ["insight"], "require_insight_triplet"),
    ("insight_listing", "案例罗列而非提炼", ["insight"], "abstract_cases"),
    ("insight_noise", "引用噪音片段充数", ["insight"], "drop_noise"),
    ("insight_overclaim", "趋势过度外推未标置信", ["insight"], "mark_extrapolation_confidence"),
    ("insight_outlier", "把一次性异常当趋势/错误归因", ["insight"], "crosscheck_outliers"),
    ("cover_key_missed", "关键claim/可答问题未覆盖全", ["coverage"], "cover_key_claims"),
    ("cover_section_missed", "必需段落缺失或内容空泛", ["coverage"], "mece_sections"),
    ("expr_bushi", "'不是,而是'句式/术语注水", ["expression"], "ban_bushi_ershi"),
    ("expr_nochart", "关键数据未结构化呈现(表/图)", ["expression"], "require_charts"),
    ("expr_length", "长度不匹配高管受众", ["expression"], "match_exec_length"),
    ("expr_rigorous", "结论不够先行或措辞不够严谨", ["expression"], "require_rigorous_wording"),
    ("expr_style_exemplar", "措辞/风格待打磨,需注入风格范例(L2, 无指令级修法)", ["expression"], None),
    ("reward_hacking_suspected", "术语堆砌,疑似讨好裁判", ["expression"], None),
]

# 失败特征 -> few-shot 类型(L2 修法)。仅"无指令级修法"的风格类失败走 few-shot。
_RESEARCH_FEWSHOT_HINT = {"expr_style_exemplar": "style_exemplar"}

# signals 键 -> failure 特征键(调研洞察)。多数同名, 个别映射。
_RESEARCH_SIG_TO_FEAT = {
    "conflict_mishandled": "trace_conflict",
    "hard_answered_unsupportable": "trace_hardanswer",
    "uncited": "trace_uncited",
    "single_source_not_downgraded": "trace_single_source",
    "summary_background": "struct_summary",
    "mece_violation": "struct_mece",
    "concept_drift": "narr_drift",
    "insight_restate": "insight_restate",
    "insight_listing": "insight_listing",
    "noise_cited": "insight_noise",
    "overclaim": "insight_overclaim",
    "bushi_ershi": "expr_bushi",
    "no_charts": "expr_nochart",
    "length_mismatch": "expr_length",
    "buzzword": "reward_hacking_suspected",
}


class FailureMappingError(ValueError):
    def __init__(self, check_ids: List[str]):
        self.check_ids = sorted(set(check_ids))
        super().__init__(
            "这些失败 check 未声明 optimizer 映射: "
            + ", ".join(self.check_ids)
        )


def _check_value(value: Any) -> float:
    if isinstance(value, str):
        mapping = {"met": 1.0, "partial": 0.5, "miss": 0.0}
        if value not in mapping:
            raise ValueError("未知 Judge check 值: %s" % value)
        return mapping[value]
    number = float(value)
    if number not in {0.0, 0.5, 1.0}:
        raise ValueError("Judge check 数值必须为 0/0.5/1: %s" % value)
    return number


def validate_optimizer_mappings(rubric: Dict[str, Any]) -> None:
    """Research rubric 的每条 check 必须明确声明映射或 ``null``。"""

    if rubric.get("product") != "research_insight":
        return
    invalid = []
    for dimension in rubric.get("dimensions", []):
        for check in dimension.get("checks", []):
            check_id = str(check.get("id") or "")
            if "optimizer" not in check:
                invalid.append(check_id or "<missing-id>")
                continue
            mapping = check.get("optimizer")
            if mapping is None:
                continue
            if not isinstance(mapping, dict) or not mapping.get("pattern_id"):
                invalid.append(check_id or "<missing-id>")
    if invalid:
        raise FailureMappingError(invalid)


def analyze_real_judgments(
    judge_checks: Dict[str, Dict[str, Any]],
    rubric: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Build optimizer input directly from every non-met real Judge check."""

    check_index = {}
    rule_map = {item[0]: item for item in RESEARCH_PATTERN_RULES}
    for dimension in rubric.get("dimensions", []):
        for check in dimension.get("checks", []):
            if check.get("id"):
                check_index[str(check["id"])] = (dimension, check)

    buckets: Dict[str, Dict[str, Any]] = {}
    unmapped = []
    for case_id, judgment in (judge_checks or {}).items():
        checks = (judgment or {}).get("checks") or {}
        reasoning = (judgment or {}).get("reasoning") or {}
        for check_id, raw_value in checks.items():
            value = _check_value(raw_value)
            if value >= 1.0:
                continue
            indexed = check_index.get(str(check_id))
            if indexed is None:
                unmapped.append(str(check_id))
                continue
            dimension, check = indexed
            if "optimizer" not in check:
                unmapped.append(str(check_id))
                continue
            mapping = check.get("optimizer")
            if mapping is None:
                continue
            pattern_id = str(mapping.get("pattern_id") or "")
            if not pattern_id:
                unmapped.append(str(check_id))
                continue
            rule = rule_map.get(pattern_id)
            description = (
                rule[1]
                if rule
                else check.get("label", pattern_id)
            )
            affected_dims = (
                list(rule[2])
                if rule
                else [str(dimension.get("name") or "")]
            )
            bucket = buckets.setdefault(
                pattern_id,
                {
                    "pattern_id": pattern_id,
                    "pattern": description,
                    "affected_dims": affected_dims,
                    "directive_hint": mapping.get("directive_hint"),
                    "fewshot_hint": mapping.get("fewshot_hint"),
                    "priority": int(mapping.get("priority", 100)),
                    "severity": (
                        "high"
                        if check.get("redline")
                        or dimension.get("name") == "traceability"
                        else "medium"
                    ),
                    "case_ids": [],
                    "evidence": [],
                },
            )
            if case_id not in bucket["case_ids"]:
                bucket["case_ids"].append(case_id)
            bucket["evidence"].append(
                {
                    "case_id": case_id,
                    "check_id": str(check_id),
                    "value": value,
                    "reasoning": str(reasoning.get(check_id, "")),
                }
            )
    if unmapped:
        raise FailureMappingError(unmapped)

    report = []
    for item in buckets.values():
        item["hit_count"] = len(item["case_ids"])
        item["exemplars"] = item["case_ids"][:2]
        report.append(item)
    report.sort(
        key=lambda item: (
            item["priority"],
            item["severity"] != "high",
            -item["hit_count"],
            item["pattern_id"],
        )
    )
    return report


def analyze_mock_records(
    records,
    low_score_threshold: int = 4,
    product: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Legacy deterministic failure clustering used by demos and smoke tests."""

    return _cluster_mock(records, low_score_threshold, product)


def _biz_features(rec) -> List[str]:
    feats = []
    out = rec.output
    if out["fabricated_values"]:
        feats.append("fabrication")
    if out["conflict_unhandled"]:
        feats.append("unhandled_conflict")
    if out["unit_error"]:
        feats.append("unit_error")
    if out["findings_uncited"]:
        feats.append("uncited")
    if rec.scores.get("completeness", 5) <= 3:
        why = rec.judge_reasoning.get("completeness", "")
        if "异常" in why:
            feats.append("anomaly_missed")
        if "段落" in why or "缺" in why:
            feats.append("missing_section")
    if rec.scores.get("insight", 5) <= 2:
        feats.append("shallow_insight")
    elif rec.scores.get("insight", 5) == 3:
        feats.append("no_attribution")
    if "reward_hacking_suspected" in rec.flagged:
        feats.append("reward_hacking_suspected")
    elif rec.scores.get("conciseness", 5) <= 3:
        feats.append("length_mismatch")
    return feats


def _research_features(rec) -> List[str]:
    """按 judge 的锚点优先级, 每个维度只报**当前起约束作用的那一个**失败(与 judge 的
    'elif 链'一致)。这样修好高优先级缺陷后, 下一个被掩盖的缺陷才在下一轮浮现 ->
    optimizer 逐个击破, 不会在被红线掩盖时白试低层修法。"""
    sig = rec.output.get("signals", {})
    feats = []
    # traceability: 红线(编造/冲突/硬答) > 无出处 > 单一信源 > 口径未注明 > 样本偏差
    if sig.get("fabricated"):
        feats.append("trace_fabrication")
    elif sig.get("conflict_mishandled"):
        feats.append("trace_conflict")
    elif sig.get("hard_answered_unsupportable"):
        feats.append("trace_hardanswer")
    elif sig.get("uncited"):
        feats.append("trace_uncited")
    elif sig.get("single_source_not_downgraded"):
        feats.append("trace_single_source")
    elif sig.get("metric_caveat_unnoted"):
        feats.append("trace_metric_caveat")
    elif sig.get("sample_bias_undisclosed"):
        feats.append("trace_sample_bias")
    # structure: 摘要铺陈 > 正文非金字塔 > 非MECE
    if sig.get("summary_background"):
        feats.append("struct_summary")
    elif sig.get("body_not_pyramid"):
        feats.append("struct_pyramid")
    elif sig.get("mece_violation"):
        feats.append("struct_mece")
    # narrative: 无主线 > 概念漂移
    if sig.get("no_narrative_flow"):
        feats.append("narr_flow")
    elif sig.get("concept_drift"):
        feats.append("narr_drift")
    # insight: 复述 > 罗列 > 引噪 > 过度外推 > 异常当趋势
    if sig.get("insight_restate"):
        feats.append("insight_restate")
    elif sig.get("insight_listing"):
        feats.append("insight_listing")
    elif sig.get("noise_cited"):
        feats.append("insight_noise")
    elif sig.get("overclaim"):
        feats.append("insight_overclaim")
    elif sig.get("outlier_unchecked"):
        feats.append("insight_outlier")
    # coverage: 关键claim/可答问题未覆盖(独立于 struct_mece)
    if sig.get("key_claims_missed"):
        feats.append("cover_key_missed")
    # expression: 风格范例待注入(L2, 解耦) > 违禁句式/注水 > 未图表化 > 长度
    if sig.get("style_unpolished"):
        feats.append("expr_style_exemplar")
    elif sig.get("buzzword") or sig.get("bushi_ershi"):
        feats.append("reward_hacking_suspected" if sig.get("buzzword") else "expr_bushi")
    elif sig.get("no_charts"):
        feats.append("expr_nochart")
    elif sig.get("length_mismatch"):
        feats.append("expr_length")
    return feats


def _cluster_mock(records, low_score_threshold=4, product: str = None) -> List[Dict[str, Any]]:
    """把 mock 记录中任一维度低于阈值的 case 归入失败模式。"""
    if product == "research_insight":
        rules, feat_fn, high_dim = RESEARCH_PATTERN_RULES, _research_features, "traceability"
    else:
        rules, feat_fn, high_dim = BIZ_PATTERN_RULES, _biz_features, "data_accuracy"

    buckets = defaultdict(list)
    for rec in records:
        if any(v <= low_score_threshold for v in rec.scores.values()):
            for feat in feat_fn(rec):
                buckets[feat].append(rec.case_id)

    report = []
    rule_map = {r[0]: r for r in rules}
    for feat, cases in buckets.items():
        if feat not in rule_map:
            continue
        _, desc, dims, hint = rule_map[feat]
        sev = "high" if high_dim in dims else ("medium" if len(cases) >= 3 else "low")
        report.append({
            "pattern_id": feat, "pattern": desc, "hit_count": len(cases),
            "affected_dims": dims, "directive_hint": hint,
            "fewshot_hint": _RESEARCH_FEWSHOT_HINT.get(feat), "severity": sev,
            "exemplars": cases[:2],
        })
    # 红线类失败(会封顶并掩盖低层修法)先修, 再修被掩盖的低层缺陷。
    report.sort(key=lambda p: (p["severity"] != "high", _PRIORITY.get(p["pattern_id"], 1),
                               -p["hit_count"]))
    return report


def cluster(records, low_score_threshold=4, product: str = None) -> List[Dict[str, Any]]:
    """Legacy/mock compatibility entry.

    The formal real-Judge Session path calls ``analyze_real_judgments`` with
    the rubric explicitly, so it never passes through the rounded score gate.
    """

    return _cluster_mock(
        list(records),
        low_score_threshold,
        product,
    )


# pattern_id -> 修复优先级(数字小者先)。红线(冲突/硬答)必须在'无出处/单一信源'之前修好,
# 否则封顶掩盖后者, 低层修法白试且被 optimizer 记入 history 不再重试。
_PRIORITY = {
    "trace_fabrication": 0, "trace_conflict": 0, "trace_hardanswer": 0,
    "trace_uncited": 1, "trace_single_source": 2,
    "trace_metric_caveat": 3, "trace_sample_bias": 4,
}
