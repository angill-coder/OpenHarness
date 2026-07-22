# -*- coding: utf-8 -*-
"""
judge.py — LLM-as-judge 的确定性模拟 (对应架构文档 JUDGE 盒 + Rubric 文档)

真实系统里这是一个按 rubric prompt 的 LLM。这里用一个**忠实实现 rubric 锚点**的
确定性打分器: 打分逻辑严格照 rubric 锚点, 于是"打开某 directive 修好某缺陷 -> 该维度
分数上升"是 rubric 定义的必然结果, 而非硬塞。judge 与人工标注的差距(见 calibration)
来自判罚力度与专家不完全一致 —— 正是 meta-eval 要校准的东西。

两个产品两套打分:
  · score_report_bizreport —— 算数字型 report-assistant, 读 flag dict(数据准确/完整/洞察/简洁)
  · score_report_research  —— 调研洞察 research_insight, 读 report["signals"](六维)
顶层 score_report(...) 按 rubric["product"] 派发。
"""
from typing import Any, Dict, Tuple


def score_report(report: Dict[str, Any], case: Dict[str, Any], rubric: Dict[str, Any]
                 ) -> Tuple[Dict[str, int], Dict[str, str], list, bool]:
    if rubric.get("product") == "research_insight":
        return score_report_research(report, case, rubric)
    return score_report_bizreport(report, case, rubric)


# ==========================================================================
# 算数字型 report-assistant (原逻辑, 保持不变)
# ==========================================================================
def score_report_bizreport(report, case, rubric):
    scores, reasoning, flagged = {}, {}, []

    n_cited = len(report["findings_cited"])
    n_uncited = len(report["findings_uncited"])
    n_fab = len(report["fabricated_values"])
    unit_err = report["unit_error"]

    # ---------- 维度 1: 数据准确性 (红线维度) ----------
    if n_fab >= 1:
        acc = 2 if n_fab == 1 else 1
        reasoning["data_accuracy"] = "出现编造数字(%d处): %s -> 2分封顶(锚点2)" % (
            n_fab, ", ".join(report["fabricated_values"]))
        flagged.append("fabrication")
    elif report["conflict_unhandled"]:
        acc = 2
        reasoning["data_accuracy"] = "数据矛盾未标记,直接取偏高值(视同编造) -> 2分"
        flagged.append("unhandled_conflict")
    elif unit_err:
        acc = 3
        reasoning["data_accuracy"] = "口径错误(环比当同比) 1处 -> 3分(锚点3)"
    elif n_uncited > 0 and n_cited == 0:
        acc = 3
        reasoning["data_accuracy"] = "关键数字无出处,无法回溯 -> 3分(锚点3)"
    elif n_uncited > 0:
        acc = 4
        reasoning["data_accuracy"] = "可回溯性基本完整,%d处引用缺失 -> 4分(锚点4)" % n_uncited
    else:
        acc = 5
        reasoning["data_accuracy"] = "全部可回溯、口径正确、无编造 -> 5分"
    scores["data_accuracy"] = acc

    # ---------- 维度 2: 完整性 ----------
    required = case["required_sections"]
    written = report["sections"]
    missing = [s for s in required if s not in written]
    anomaly_missed = ("anomaly" in case.get("hard_case_tags", [])) and not report["anomaly_reported"]
    if len(missing) >= 2:
        comp = 2; reasoning["completeness"] = "缺 %d 个必需段落: %s -> 2分" % (len(missing), missing)
    elif len(missing) == 1 or anomaly_missed:
        comp = 3
        why = ("缺段落 %s" % missing) if missing else ""
        if anomaly_missed:
            why += "; 漏报关键异常"
        reasoning["completeness"] = why + " -> 3分(锚点3)"
    else:
        comp = 5; reasoning["completeness"] = "必需段落全覆盖,关键 finding 无遗漏 -> 5分"
    scores["completeness"] = comp

    # ---------- 维度 3: 洞察质量 ----------
    itypes = set(report["insight_types"])
    has_attr = "attribution" in itypes
    has_risk = "risk" in itypes
    has_next = "next_step" in itypes
    n_elem = sum([has_attr, has_risk, has_next])
    if n_elem == 0:
        ins = 2; reasoning["insight"] = "基本数据复读,无归因/风险/建议 -> 2分(锚点2)"
    elif has_attr and has_risk and has_next:
        ins = 5; reasoning["insight"] = "归因+风险+下一步俱全且有据 -> 5分"
    elif n_elem >= 2:
        ins = 4; reasoning["insight"] = "三要素齐大部分,个别较泛 -> 4分"
    else:
        ins = 3; reasoning["insight"] = "有判断但偏表面 -> 3分(锚点3)"
    scores["insight"] = ins

    # ---------- 维度 4: 简洁性 (反向维度, 防 hack) ----------
    if report["buzzword_stuffing"]:
        conc = 2
        reasoning["conciseness"] = "大量术语堆砌('数据驱动/闭环/赋能'式),信息量低 -> 2分(锚点2)"
        flagged.append("reward_hacking_suspected")
    elif not report["length_matched"]:
        conc = 3
        reasoning["conciseness"] = "长度未匹配受众设定(%s) -> 3分(锚点3)" % report["audience"]
    else:
        conc = 5
        reasoning["conciseness"] = "无冗余,长度匹配受众 -> 5分"
    scores["conciseness"] = conc

    floor = _hard_floor(rubric, "data_accuracy")
    case_failed = floor is not None and acc < floor
    if case_failed:
        flagged.append("RED_LINE:data_accuracy<%d" % floor)
    return scores, reasoning, flagged, case_failed


# ==========================================================================
# 调研洞察 research_insight (六维, 读 report["signals"], 照 Rubric 文档 §2 锚点)
# ==========================================================================
def score_report_research(report, case, rubric):
    scores, reasoning, flagged = {}, {}, []
    sig = report.get("signals", {})

    # ---------- ① 可回溯性与支撑充分 traceability (0.28, 红线) ----------
    if sig.get("fabricated"):
        tr = 2; reasoning["traceability"] = "编造/曲解了素材里没有的事实 -> 2分封顶(红线)"
        flagged.append("trace_fabrication")
    elif sig.get("conflict_mishandled"):
        tr = 2; reasoning["traceability"] = "素材冲突未指出、直接混用 -> 2分封顶(红线)"
        flagged.append("trace_conflict")
    elif sig.get("hard_answered_unsupportable"):
        tr = 2; reasoning["traceability"] = "素材不足却硬给结论 -> 2分封顶(红线)"
        flagged.append("trace_hardanswer")
    elif sig.get("uncited"):
        tr = 3; reasoning["traceability"] = "论断无出处标记,可回溯性差 -> 3分(锚点3)"
    elif sig.get("single_source_not_downgraded"):
        tr = 3; reasoning["traceability"] = "单一信源结论未降级'待验证' -> 3分(锚点3)"
    elif sig.get("metric_caveat_unnoted"):
        tr = 3; reasoning["traceability"] = "选有利口径未注明口径依赖(轻度曲解) -> 3分(锚点3)"
    elif sig.get("sample_bias_undisclosed"):
        tr = 3; reasoning["traceability"] = "无视样本偏差、把有偏结果当客观 -> 3分(锚点3)"
    else:
        tr = 5; reasoning["traceability"] = "论断可回溯、冲突已指出、素材不足处诚实留白 -> 5分"
    scores["traceability"] = tr

    # ---------- ② 结构 structure (0.15) ----------
    if sig.get("summary_background"):
        st = 2; reasoning["structure"] = "摘要是背景铺陈而非结论 -> 2分封顶(锚点2)"
    else:
        sp = int(bool(sig.get("body_not_pyramid"))) + int(bool(sig.get("mece_violation")))
        st = 5 - sp  # 0→5, 1→4, 2→3
        reasoning["structure"] = ("正文金字塔+章节MECE 有 %d 处问题 -> %d分" % (sp, st)) if sp else "摘要结论先行、正文金字塔、章节MECE -> 5分"
    scores["structure"] = st

    # ---------- ③ 逻辑与故事线 narrative (0.12) ----------
    if sig.get("concept_drift") and sig.get("conflict_mishandled"):
        na = 2; reasoning["narrative"] = "概念漂移 + 冲突未处理,故事线断裂 -> 2分封顶"
    elif sig.get("no_narrative_flow"):
        na = 3; reasoning["narrative"] = "无清晰主线、章节并列拼接缺推进 -> 3分(锚点3)"
    elif sig.get("concept_drift"):
        na = 4; reasoning["narrative"] = "主线清楚,但1处概念/口径前后不一致 -> 4分(锚点4)"
    else:
        na = 5; reasoning["narrative"] = "主线清晰、章节衔接自然、概念口径一致 -> 5分"
    scores["narrative"] = na

    # ---------- ④ 提炼与洞察 insight (0.22) ----------
    if sig.get("insight_restate"):
        it = 2; reasoning["insight"] = "基本素材复述,缺归因/趋势/建议 -> 2分封顶(锚点2)"
    elif sig.get("insight_listing"):
        it = 3; reasoning["insight"] = "案例是罗列而非提炼 -> 3分(锚点3)"
    elif sig.get("noise_cited"):
        it = 3; reasoning["insight"] = "引用噪音片段充数,剔噪失败 -> 3分(锚点3)"
    elif sig.get("overclaim"):
        it = 4; reasoning["insight"] = "三要素齐但趋势过度外推未标置信 -> 4分(锚点4)"
    elif sig.get("outlier_unchecked"):
        it = 4; reasoning["insight"] = "把一次性异常/离群当趋势、未作交叉解释 -> 4分(锚点4)"
    else:
        it = 5; reasoning["insight"] = "案例提炼成规律、归因/趋势(标置信)/建议俱全、噪音已剔 -> 5分"
    scores["insight"] = it

    # ---------- ⑤ 覆盖度 coverage (0.08, 素材答不了的不算漏) ----------
    cp = (int(bool(sig.get("mece_violation"))) + int(bool(sig.get("insight_restate")))
          + int(bool(sig.get("key_claims_missed"))))
    cv = max(2, 5 - cp)  # 0→5,1→4,2→3,3→2
    reasoning["coverage"] = ("必需段落/可答问题/关键claim 有 %d 处未覆盖 -> %d分" % (cp, cv)) if cp else "可答问题全答、关键 claim 无遗漏、段落齐 -> 5分"
    scores["coverage"] = cv

    # ---------- ⑥ 表达与受众契合 expression (0.15, 反向 + 风格红线) ----------
    if sig.get("buzzword") or sig.get("bushi_ershi"):
        ex = 2
        reasoning["expression"] = "出现'不是,而是'句式 / 术语堆砌注水 -> 2分封顶(风格红线)"
        if sig.get("buzzword"):
            flagged.append("reward_hacking_suspected")
    elif sig.get("no_charts") and sig.get("length_mismatch"):
        ex = 3; reasoning["expression"] = "关键数据未结构化呈现 + 长度偏离受众 -> 3分(锚点3)"
    elif sig.get("no_charts") or sig.get("length_mismatch"):
        ex = 4; reasoning["expression"] = "基本精炼,1处本该用表却用文字 / 长度略偏 -> 4分"
    else:
        ex = 5; reasoning["expression"] = "结论先行、关键数据用表/图、长度合exec、无违禁句式 -> 5分"
    scores["expression"] = ex

    # ---------- 红线 gate: traceability < hard_floor ----------
    floor = _hard_floor(rubric, "traceability")
    case_failed = floor is not None and tr < floor
    if case_failed:
        flagged.append("RED_LINE:traceability<%d" % floor)
    return scores, reasoning, flagged, case_failed


def _hard_floor(rubric, dim_name):
    for d in rubric["dimensions"]:
        if d["name"] == dim_name:
            return d.get("hard_floor")
    return None


def overall(scores: Dict[str, int], rubric: Dict[str, Any]) -> float:
    w = {d["name"]: d["weight"] for d in rubric["dimensions"]}
    return round(sum(scores[k] * w[k] for k in scores if k in w), 3)


def dim_from_checks(check_scores: Dict[str, float], rubric: Dict[str, Any]) -> Dict[str, int]:
    """把逐 check 的 满足/部分/不满足(1/0.5/0) 汇总成六维 1-5 分(供真实标注/judge 复用)。

      dim = round(1 + 4·mean(该维已打分的 checks));全满足=5、全不满足=1。
      红线:该维任一带 redline 的 check 判 0 -> 该维封顶 2(承接可回溯性/表达红线)。
    只对"有 checks 且至少打了一条"的维度给分;未打分的维度不出现在结果里。
    """
    out = {}
    for d in rubric["dimensions"]:
        checks = d.get("checks") or []
        vals, redline_hit = [], False
        for c in checks:
            if c["id"] in check_scores and check_scores[c["id"]] is not None:
                v = float(check_scores[c["id"]])
                vals.append(v)
                if c.get("redline") and v <= 0:
                    redline_hit = True
        if not vals:
            continue
        score = 1 + 4 * (sum(vals) / len(vals))
        score = int(round(score))
        if redline_hit:
            score = min(score, 2)
        out[d["name"]] = max(1, min(5, score))
    return out

