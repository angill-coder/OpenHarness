# -*- coding: utf-8 -*-
"""创建“面向总裁的汇报助手”LLM 改写实验会话。

会话使用当前 research_insight 六维 rubric 的原样副本，导入 20 条统一
openharness-wb/v1 真实项目 case，并配置会话级 early-stop：
overall >= 4.8，或连续 4 个候选版本未提升已采纳最佳 overall。
"""

from __future__ import annotations

import json
from pathlib import Path

import persistence as persist
from session import Session
from workbuddy_batch.dataset import openharness_rows


ROOT = Path(__file__).resolve().parents[1]
SID = "president-report-llm"
DATASET = ROOT / "data" / "20260727_real_project_package" / "data.json"
REQUIREMENT = (
    "面向总裁的汇报助手。用户先输入话题和原始素材；助手再通过交互补齐"
    "汇报背景、hypothesis、材料重点分布（重点主题、重点素材及预期篇幅或权重）。"
    "输出严格三段式：摘要、关键发现、启示。写作遵守结论先行、金字塔原理、"
    "MECE；所有结论和数据仅来自原始素材，不新增、删除或篡改原始事实和数据，"
    "原始素材文件只读；数据密集部分用图表展示；整体风格简洁严谨。"
)
OPTIMIZER_STOP = {
    "overall_target": 4.8,
    "max_no_improvement": 4,
}


def main():
    if persist.load_snapshot(SID):
        raise SystemExit(
            "会话 %s 已存在；为避免覆盖实验历史，本脚本拒绝重复创建。"
            % SID
        )
    with DATASET.open(encoding="utf-8") as handle:
        rows = openharness_rows(json.load(handle))
    session = Session(
        SID,
        REQUIREMENT,
        "research_insight",
        optimizer_mode="llm_rewrite",
        optimizer_stop=OPTIMIZER_STOP,
    )
    state = session.import_data(rows, account="local")
    print(
        "created=%s optimizer=%s cases=%d stop=%s"
        % (
            SID,
            state["optimizer_mode"],
            state["n_cases"],
            json.dumps(state["optimizer_stop"], ensure_ascii=False),
        )
    )


if __name__ == "__main__":
    main()
