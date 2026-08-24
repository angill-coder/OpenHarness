"""Report Loop 调研报告 Judge Prompt。"""
from __future__ import annotations

import json


def _append_dimension_anchors(lines, dimension):
    """按原 harness Rubric 设计，把维度级评分锚点与正反示例注入提示词。"""
    anchors = dimension.get("anchors") or {}
    criteria = dimension.get("criteria")
    positive = dimension.get("positive_example")
    negative = dimension.get("negative_example")
    if not (anchors or criteria or positive or negative):
        return
    name_zh = dimension.get("name_zh", dimension.get("name", ""))
    lines.append("")
    lines.append("## 评分锚点（%s，1–5 分）" % name_zh)
    if criteria:
        lines.append("维度标准：%s" % criteria)
    for level in ("5", "4", "3", "2", "1"):
        text = anchors.get(level)
        if text is None:
            text = anchors.get(int(level))
        if text:
            lines.append("- %s 分：%s" % (level, text))
    if positive:
        lines.append("正面示例：%s" % positive)
    if negative:
        lines.append("反面示例：%s" % negative)


def build_judge_prompt(rubric, report_text, case_context) -> str:
    dimensions = rubric.get("dimensions") or []
    lines = [
        "你是严格的调研报告评审。",
        "只评价报告正文实际呈现的内容；背景和 Structured Data 只用于核验。",
        "对每条 check 判 met、partial 或 miss；判定时参照对应维度的评分锚点"
        "（1–5 分）与正反示例，使三档判定与锚点分层一致。",
    ]
    if len(dimensions) == 1:
        dimension = dimensions[0]
        lines.extend([
            "",
            "本次只评维度 %s（%s），不得补评其他维度。"
            % (
                dimension.get("name", ""),
                dimension.get("name_zh", ""),
            ),
        ])
    context = dict(case_context or {})
    context.pop("human_report", None)
    if context.get("structured_data"):
        lines.extend([
            "Structured Data 是证据索引而非参考答案。",
            "用它核验事实、冲突、口径和证据边界；"
            "只有明确冲突或无依据的确定性事实才按编造降档。",
        ])
    if context.get("delivery_constraints"):
        lines.append(
            "评价篇幅时以 Delivery Constraints 和 Report Stats 为准。"
        )
    check_ids = []
    for dimension in dimensions:
        _append_dimension_anchors(lines, dimension)
        name_zh = dimension.get("name_zh", dimension.get("name", ""))
        lines.extend(["", "## 逐条 check（%s）" % name_zh])
        for check in dimension.get("checks", []):
            check_id = str(check["id"])
            check_ids.append(check_id)
            redline = " [红线]" if check.get("redline") else ""
            lines.append(
                "- %s（%s·%s%s）：%s；触发降档：%s"
                % (
                    check_id,
                    name_zh,
                    check.get("label", check_id),
                    redline,
                    check.get("desc", ""),
                    check.get("effect", ""),
                )
            )
    for key, title in (
        ("background", "背景信息"),
        ("structured_data", "Structured Data"),
        ("delivery_constraints", "交付篇幅"),
        ("report_stats", "报告长度"),
    ):
        if context.get(key):
            lines.extend([
                "",
                "## " + title,
                json.dumps(context[key], ensure_ascii=False),
            ])
    example = {
        "checks": {check_id: "met" for check_id in check_ids},
        "reasoning": {
            check_id: "一句话说明判定依据"
            for check_id in check_ids
        },
    }
    lines.extend([
        "",
        "## 报告正文",
        report_text or "(空)",
        "",
        "## 输出",
        "只输出严格 JSON，不要附加解释。",
        json.dumps(example, ensure_ascii=False),
    ])
    return "\n".join(lines)
