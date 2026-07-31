# -*- coding: utf-8 -*-
"""
backend.py — skill 执行后端 (Runner 底层)

三个实现:
  - MockBackend         : 旧「算数字型」产品(report-assistant)的确定性模拟。按 directives
                          决定输出质量,吐结构化 flag dict。judge.score_report_bizreport 读它。
  - ResearchMockBackend : 「调研洞察汇报助手」(research_insight)的确定性模拟。按 directives
                          输出**报告文本 + signals**(编造/引用/冲突/罗列vs提炼/"不是而是"/图表…),
                          judge.score_report_research 读 signals 照六维锚点打分。
  - RecordedBackend     : 真实路径。skill 在平台(Claude Code)里跑出的报告文本,经 app 粘贴导入,
                          按 (skill.version, case_id) 查表返回。**不调 API、无需 key** ——
                          平台就是运行时,harness 不再自己拿 key 去调一遍。

设计: report 有两种形态。算数字型是 flag dict;调研洞察型是 {report_text, signals, ...}。
judge 按 rubric["product"] 派发,读对应形态。RecordedBackend 只返回 report_text(真实报告),
其六维评分需平台上的 LLM-as-judge 产出(见 README「数据线」),离线 mock 路径用 signals 自测。
"""
from typing import Any, Dict, List


class Backend:
    """执行一个 skill 于一个 case,返回 (report, trace)。"""
    name = "base"

    def run(self, skill, case: Dict[str, Any]):
        raise NotImplementedError


# --------------------------------------------------------------------------
# MockBackend  (算数字型 report-assistant, 保持原样以兼容旧 demo)
# --------------------------------------------------------------------------
class MockBackend(Backend):
    name = "mock"

    def run(self, skill, case):
        d = skill.directives()
        gt = {f["id"]: f for f in case["ground_truth_findings"]}
        tags = case.get("hard_case_tags", [])
        mem = skill.memory_content

        trace = {"steps": [], "backend": self.name}

        # ---- STEP 1: DataAnalyst 产出 findings ----
        findings = []
        for fid in ["F-001", "F-002", "F-003"]:
            if fid in gt:
                findings.append(self._emit_finding(gt[fid], d))
        if "F-004" in gt:
            findings.append(self._emit_finding(gt["F-004"], d))
        if "F-005" in gt:
            findings.append(self._emit_finding(gt["F-005"], d))

        fabricated = []
        if "missing_data" in tags:
            if d.get("require_citation"):
                findings.append({"id": "F-011", "text": "customers_new 数据缺失,已标记缺口",
                                 "source_ref": "raw._missing", "is_gap": True})
            else:
                fabricated.append("customers_new(缺失却给出了数字)")

        unit_error = False
        if "unit_confusion" in tags and not d.get("require_metric_definitions"):
            unit_error = True

        conflict_unhandled = False
        if "contradiction" in tags:
            if d.get("verifier_check_omissions"):
                findings.append({"id": "F-012", "text": "arr 两来源不一致,已标记矛盾",
                                 "source_ref": "raw.arr_now_wan vs source_b", "is_conflict": True})
            else:
                conflict_unhandled = True
                fabricated.append("arr(两来源冲突却直接取了偏高值)")

        trace["steps"].append({"step": 1, "subagent": "DataAnalyst",
                               "findings": [f["id"] for f in findings if "id" in f],
                               "fabricated": fabricated, "unit_error": unit_error})

        # ---- STEP 2: Insight ----
        insights = []
        if d.get("require_risk_and_next_step"):
            insights.append({"claim": "增长归因: 新增vs留存拆分", "based_on": ["F-002", "F-004"],
                             "type": "attribution"} if d.get("require_attribution")
                            else {"claim": "增长", "based_on": ["F-002"], "type": "restate"})
            insights.append({"claim": "风险识别", "based_on": ["F-010"] if "anomaly" in tags else [],
                             "type": "risk"})
            insights.append({"claim": "下一步建议", "based_on": [], "type": "next_step"})
        trace["steps"].append({"step": 2, "subagent": "Insight",
                               "insight_types": [i["type"] for i in insights]})

        # ---- STEP 3: Writer 组织报告 ----
        sections_written = list(case["required_sections"])
        if not d.get("enforce_required_sections"):
            if len(sections_written) > 3:
                sections_written = sections_written[:-1]
        anomaly_reported = ("anomaly" in tags) and d.get("verifier_check_omissions", False)

        # ---- STEP 4: Verifier ----
        verifier_fixes = []
        if d.get("verifier_check_omissions"):
            missing = [s for s in case["required_sections"] if s not in sections_written]
            if missing:
                sections_written = list(case["required_sections"])
                verifier_fixes.append("补回缺失段落: %s" % missing)
            if "anomaly" in tags and not anomaly_reported:
                anomaly_reported = True
                verifier_fixes.append("补回漏报的区域异常")
        trace["steps"].append({"step": 4, "subagent": "Verifier", "fixes": verifier_fixes})

        # ---- STEP 5: Format ----
        audience = case["audience"]
        length_matched = d.get("match_audience_length", False)
        buzzword = d.get("keyword_emphasis", False)

        report = {
            "sections": sections_written,
            "findings_cited": [f for f in findings if f.get("source_ref")],
            "findings_uncited": [f for f in findings if not f.get("source_ref")],
            "fabricated_values": fabricated,
            "unit_error": unit_error,
            "conflict_unhandled": conflict_unhandled,
            "insight_types": [i["type"] for i in insights],
            "anomaly_reported": anomaly_reported,
            "audience": audience,
            "length_matched": length_matched,
            "buzzword_stuffing": buzzword,
            "used_baseline": d.get("use_historical_baseline", False)
                             and len(mem.get("facts", {}).get("historical_baselines", [])) > 0,
        }
        trace["steps"].append({"step": 5, "audience": audience, "buzzword": buzzword})
        return report, trace

    def _emit_finding(self, gt_finding, directives):
        f = {"id": gt_finding["id"], "value": gt_finding.get("value")}
        if directives.get("require_citation"):
            f["source_ref"] = gt_finding.get("source_ref", "")
        return f


# --------------------------------------------------------------------------
# ResearchMockBackend  (调研洞察汇报助手 research_insight)
# --------------------------------------------------------------------------
# 调研洞察 directive 动作空间(与 optimizer / clustering / generator 对齐)。
RESEARCH_DIRECTIVES = [
    "require_source_ref",            # 论断挂 S-xxx/C-xxx 出处      -> traceability
    "flag_source_conflict",          # 指出素材冲突而非混用          -> traceability
    "honest_on_unsupportable",       # 素材不足诚实留白,不硬给结论    -> traceability
    "require_two_sources",           # 单一信源结论降级"待验证"      -> traceability
    "summary_format",                # 摘要≤3 bullet 结论先行        -> structure
    "pyramid_body",                  # 正文金字塔:每章论点先行        -> structure
    "mece_sections",                 # 章节 MECE、必需段落齐         -> structure / coverage
    "concept_consistency",           # 概念/口径前后一致            -> narrative
    "ensure_narrative_flow",         # 一条主线贯穿、章节递进衔接      -> narrative
    "require_insight_triplet",       # 归因/趋势/建议三要素          -> insight / coverage
    "abstract_cases",                # 案例抽象提炼非罗列            -> insight
    "drop_noise",                    # 不引用噪音片段               -> insight
    "mark_extrapolation_confidence", # 趋势标置信、不过度外推        -> insight
    "crosscheck_outliers",           # 异常/离群作交叉解释、不当趋势   -> insight
    "cover_key_claims",              # 覆盖 key_questions/关键 claim  -> coverage
    "ban_bushi_ershi",               # 禁"不是,而是"句式/注水       -> expression
    "require_charts",                # 关键数据用表/图结构化呈现      -> expression
    "match_exec_length",             # 长度匹配高管受众             -> expression
    "require_rigorous_wording",      # 措辞严谨不含糊(真实杠杆,不入mock评分) -> expression
    "verify_no_fabrication",         # 独立核验、不编造/不曲解素材没有的事实 -> traceability(红线)
    "note_metric_caveat",            # 口径依赖处注明口径/给敏感性     -> traceability
    "disclose_sample_bias",          # 披露样本代表性/选择偏差        -> traceability
    "buzzword_emphasis",             # 【reward-hack 杠杆, FORBIDDEN】堆术语讨好裁判 -> expression↓
]


class ResearchMockBackend(Backend):
    """按 directives 生成「带可控缺陷的报告文本 + signals」。signals 是 judge/clustering
    的唯一事实源:每个 directive 精确对应六维的一个失分点,打开正确 directive -> 对应 signal
    消失 -> 该维度分数上升。真实报告文本走 RecordedBackend,本类只用于离线自测闭环。"""
    name = "research_mock"

    def run(self, skill, case):
        d = skill.directives()
        tags = set(case.get("hard_case_tags", []))
        gt = case.get("ground_truth", {})
        unsupportable = gt.get("unsupportable_questions", [])
        noise = gt.get("noise_source_ids", [])
        claims = gt.get("supported_claims", [])
        single_source = any(len(c.get("source_ids", [])) < 2 for c in claims)

        sig = {
            # --- traceability ---
            "conflict_mishandled": ("source_conflict" in tags) and not d.get("flag_source_conflict"),
            "hard_answered_unsupportable": bool(unsupportable)
                and (tags & {"missing_evidence", "unsupported_extrapolation"})
                and not d.get("honest_on_unsupportable"),
            "uncited": not d.get("require_source_ref"),
            "single_source_not_downgraded": single_source and not d.get("require_two_sources"),
            "fabricated": ("fabrication_risk" in tags) and not d.get("verify_no_fabrication"),
            "metric_caveat_unnoted": ("metric_caveat" in tags) and not d.get("note_metric_caveat"),
            "sample_bias_undisclosed": ("selection_bias" in tags) and not d.get("disclose_sample_bias"),
            # --- structure ---
            "summary_background": not d.get("summary_format"),
            "body_not_pyramid": not d.get("pyramid_body"),
            "mece_violation": not d.get("mece_sections"),
            # --- narrative ---
            "concept_drift": not d.get("concept_consistency"),
            "no_narrative_flow": not d.get("ensure_narrative_flow"),
            # --- insight ---
            "insight_restate": not d.get("require_insight_triplet"),
            "insight_listing": d.get("require_insight_triplet") and not d.get("abstract_cases"),
            "noise_cited": bool(noise) and not d.get("drop_noise"),
            "overclaim": ("unsupported_extrapolation" in tags) and not d.get("mark_extrapolation_confidence"),
            "outlier_unchecked": ("outlier_confound" in tags) and not d.get("crosscheck_outliers"),
            # --- coverage ---
            "key_claims_missed": not d.get("cover_key_claims"),
            # --- expression (反向) ---
            "bushi_ershi": not d.get("ban_bushi_ershi"),
            "no_charts": not d.get("require_charts"),
            "length_mismatch": not d.get("match_exec_length"),
            "imprecise_wording": not d.get("require_rigorous_wording"),  # 定义但不入评分(真实杠杆)
            "buzzword": bool(d.get("buzzword_emphasis")),
        }
        # L2(few-shot)解耦钩子: 被标记的 case, 表达维只由 style_exemplar 范例驱动(见 judge/clustering)。
        # 与 charts/length/bushi 解耦, 避免残留缺陷掩盖 L1 表达 directive(贪心卡死)。
        style_case = "needs_style_exemplar" in tags
        sig["style_case"] = style_case
        sig["style_unpolished"] = style_case and not skill.has_fewshot("style_exemplar")
        # bool 归一(& 运算可能产出 set)
        sig = {k: bool(v) for k, v in sig.items()}

        report_text = self._render_text(case, sig)
        report = {"report_text": report_text, "signals": sig,
                  "audience": case.get("audience", "exec")}
        trace = {"backend": self.name, "signals_on": [k for k, v in sig.items() if v]}
        return report, trace

    def _render_text(self, case, sig):
        """生成一段能体现 signals 的报告文本(供 app 展示 / 人读)。非评分依据。"""
        topic = case.get("topic", "调研主题")
        L = ["# %s — 调研洞察汇报" % topic, "", "## 摘要"]
        if sig["summary_background"]:
            L.append("- 本报告对%s进行了多维度分析,数据来自内部BI与访谈。" % topic)
        else:
            L.append("- 增长由留存加深驱动,拉新贡献有限(结论先行)。")
        L.append("")
        L.append("## 核心发现")
        cite = "" if sig["uncited"] else "[S-001]"
        L.append("人均日时长 Q1→Q2 从18升至27分钟%s。" % cite)
        if not sig["conflict_mishandled"]:
            L.append("注:竞品口径为去重会话时长[S-003],与本数据不可直接比较。")
        if not sig["no_charts"]:
            L.append("\n| 指标 | Q1 | Q2 |\n|---|---|---|\n| 人均时长 | 18 | 27 |")
        if sig["bushi_ershi"]:
            L.append("增长并非来自拉新,而是来自留存加深。")
        if sig["noise_cited"]:
            L.append("AI时代已来,赋能千行百业,重构产业范式[S-009]。")
        L.append("")
        L.append("## 建议")
        if sig["hard_answered_unsupportable"]:
            L.append("下一步应加大投放力度。")
        else:
            L.append("现有素材无投放数据,暂无法判断投放方向,建议补充后再定。")
        return "\n".join(L)


# --------------------------------------------------------------------------
# RecordedBackend  (真实路径: 平台跑出的报告文本, app 粘贴导入)
# --------------------------------------------------------------------------
class RecordedBackend(Backend):
    """返回已导入的、按 (skill.version, case_id) 存储的真实报告文本。

    outputs_map: {version: {case_id: report_text}}。缺失则返回空文本并在 trace 标 missing。
    真实文本的六维评分需平台 LLM-as-judge 产出(本 MVP 不含),故这里只负责把报告文本
    带进 EvalRecord，供模型 Judge 评分。"""
    name = "recorded"

    def __init__(self, outputs_map: Dict[str, Dict[str, str]]):
        self.outputs_map = outputs_map or {}

    def run(self, skill, case):
        version = skill.version
        case_id = case["case_id"]
        text = self.outputs_map.get(version, {}).get(case_id, "")
        report = {"report_text": text, "signals": {},
                  "audience": case.get("audience", "exec")}
        trace = {"backend": self.name, "version": version, "case_id": case_id,
                 "missing": not bool(text)}
        return report, trace


# --------------------------------------------------------------------------
def get_backend(product_id: str = None, outputs_map: Dict[str, Dict[str, str]] = None,
                prefer_real: bool = False):
    """选择后端 —— 不再依赖 ANTHROPIC_API_KEY(平台即运行时)。

      · 有 outputs_map(app 粘贴了真实报告)          -> RecordedBackend
      · product_id == 'research_insight'             -> ResearchMockBackend
      · 其它(算数字型 report-assistant)             -> MockBackend

    prefer_real 保留仅为向后兼容旧 run_demo.py 的 --real 开关;API 路径已移除,此参数无效。
    """
    if outputs_map:
        return RecordedBackend(outputs_map)
    if product_id == "research_insight":
        return ResearchMockBackend()
    return MockBackend()
