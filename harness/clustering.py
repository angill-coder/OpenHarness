# -*- coding: utf-8 -*-
"""
clustering.py — 失败模式聚类 (对应架构文档 FAILURE CLUSTERING)

把低分 case 按"为什么低分"归成几类, 每类给 {pattern, hit_count, affected_dims,
directive_hint, severity, exemplars}。optimizer 只吃这个结构。

两个产品两套特征规则, 由 product 派发:
  · report-assistant  (算数字型): 读 rec.output 的 flag 字段
  · research_insight  (调研洞察): 读 rec.output["signals"]
"""
from typing import Any, Dict, List
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
    ("narr_flow", "无清晰主线/章节并列拼接缺推进", ["narrative"], "ensure_narrative_flow"),
    ("narr_drift", "概念漂移/口径前后不一致", ["narrative"], "concept_consistency"),
    ("insight_restate", "素材复述,缺归因/趋势/建议", ["insight"], "require_insight_triplet"),
    ("insight_listing", "案例罗列而非提炼", ["insight"], "abstract_cases"),
    ("insight_noise", "引用噪音片段充数", ["insight"], "drop_noise"),
    ("insight_overclaim", "趋势过度外推未标置信", ["insight"], "mark_extrapolation_confidence"),
    ("insight_outlier", "把一次性异常当趋势/错误归因", ["insight"], "crosscheck_outliers"),
    ("cover_key_missed", "关键claim/可答问题未覆盖全", ["coverage"], "cover_key_claims"),
    ("expr_bushi", "'不是,而是'句式/术语注水", ["expression"], "ban_bushi_ershi"),
    ("expr_nochart", "关键数据未结构化呈现(表/图)", ["expression"], "require_charts"),
    ("expr_length", "长度不匹配高管受众", ["expression"], "match_exec_length"),
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


def cluster(records, low_score_threshold=4, product: str = None) -> List[Dict[str, Any]]:
    """把'任一维度 <= 阈值'的 case 归入失败模式。"""
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


# pattern_id -> 修复优先级(数字小者先)。红线(冲突/硬答)必须在'无出处/单一信源'之前修好,
# 否则封顶掩盖后者, 低层修法白试且被 optimizer 记入 history 不再重试。
_PRIORITY = {
    "trace_fabrication": 0, "trace_conflict": 0, "trace_hardanswer": 0,
    "trace_uncited": 1, "trace_single_source": 2,
    "trace_metric_caveat": 3, "trace_sample_bias": 4,
}
