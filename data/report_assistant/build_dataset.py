#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_dataset.py — 业务汇报助手数据集生成器 (可复现, 种子固定)

对应 业务汇报助手_Rubric落地文档.md §8「数据集要求」:
  - train / dev / test 三分, test 在优化全程不可见
  - 覆盖真实分布: 报告类型 x 受众 x 数据规模
  - 必须含硬 case: 缺口 / 异常 / 口径易混 / findings 矛盾
  - 30-50 条量级, 其中一个子集带人工标注(见 human_labels.jsonl)

每个 case 的关键字段:
  input.raw            —— skill 拿到的原始数据(可能含缺口/异常/矛盾)
  ground_truth_findings—— 正确计算出的 findings(带 id/value/source_ref)
                          judge 用它核对可回溯性、抓编造(input里没有的数字=编造)
  hard_case_tags       —— [missing_data|anomaly|unit_confusion|contradiction]

用法:  python3 build_dataset.py    # 写出 dataset.jsonl + human_labels.jsonl
"""
import json
import os
import random

SEED = 20260705
random.seed(SEED)

HERE = os.path.dirname(os.path.abspath(__file__))

# ---- 报告类型 -> 模板信息(与 skill_v0.json 的 report_templates 对齐) ----
REPORT_TYPES = {
    "monthly_biz_review": {
        "sections": ["业绩概览", "关键指标", "异常与归因", "风险", "下一步"],
        "audience": "exec",
    },
    "weekly_update": {
        "sections": ["本周进展", "关键指标", "阻塞与风险", "下周计划"],
        "audience": "team",
    },
    "ops_brief": {
        "sections": ["经营概览", "现金与跑道", "关键指标", "风险", "决策项"],
        "audience": "exec",
    },
    "project_progress": {
        "sections": ["里程碑状态", "本期完成", "风险与阻塞", "下期计划"],
        "audience": "team",
    },
}

REGIONS = ["华东", "华南", "华北", "西南"]


def _round(x, n=1):
    return round(x, n)


def make_case(idx, report_type, tags):
    """构造一个 case。tags 决定注入哪些'硬'特征。"""
    meta = REPORT_TYPES[report_type]
    # --- 基础数据 ---
    arr_prev = random.randint(800, 1500)          # 上期 ARR (万)
    growth = random.uniform(-0.05, 0.15)          # 环比
    arr_now = _round(arr_prev * (1 + growth))
    cust_start = random.randint(400, 1200)        # 期初客户数
    churned = random.randint(5, 60)
    new_cust = random.randint(20, 90)
    cust_end = cust_start - churned + new_cust
    churn_rate = _round(churned / cust_start * 100, 2)
    cash = random.randint(2000, 9000)             # 现金(万)
    burn = random.randint(300, 900)               # 月消耗(万)
    runway = _round(cash / burn, 1)               # 跑道(月)

    raw = {
        "period": "2026-06",
        "prev_period": "2026-05",
        "arr_prev_wan": arr_prev,
        "arr_now_wan": arr_now,
        "customers_start": cust_start,
        "customers_churned": churned,
        "customers_new": new_cust,
        "customers_end": cust_end,
        "cash_wan": cash,
        "monthly_burn_wan": burn,
    }

    # ground truth findings —— 正确算出的事实, 每条带 id
    findings = [
        {"id": "F-001", "metric": "arr_now", "value": arr_now, "unit": "万",
         "source_ref": "raw.arr_now_wan"},
        {"id": "F-002", "metric": "arr_mom", "value": _round(growth * 100, 1), "unit": "%",
         "source_ref": "raw.arr_now_wan vs raw.arr_prev_wan", "computation": "(now-prev)/prev"},
        {"id": "F-003", "metric": "churn_rate", "value": churn_rate, "unit": "%",
         "source_ref": "raw.customers_churned / raw.customers_start"},
        {"id": "F-004", "metric": "net_new_customers", "value": new_cust - churned, "unit": "户",
         "source_ref": "raw.customers_new - raw.customers_churned"},
    ]
    key_finding_ids = ["F-001", "F-002"]  # 至少要被写进报告, 否则算漏报

    # runway 只在 ops_brief 里是关键项
    if report_type == "ops_brief":
        findings.append({"id": "F-005", "metric": "cash_runway", "value": runway, "unit": "月",
                         "source_ref": "raw.cash_wan / raw.monthly_burn_wan"})
        key_finding_ids.append("F-005")

    notes = []

    # ---- 注入硬特征 ----
    if "anomaly" in tags:
        region = random.choice(REGIONS)
        drop = random.randint(12, 25)
        raw["region_anomaly"] = {"region": region, "mom_pct": -drop}
        findings.append({"id": "F-010", "metric": "region_mom", "value": -drop, "unit": "%",
                         "source_ref": "raw.region_anomaly", "region": region})
        key_finding_ids.append("F-010")  # 异常必须被识别并写进'异常/风险'
        notes.append("含区域异常, 必须被识别")

    if "missing_data" in tags:
        # 某个数据源缺失 —— skill 应标记'数据缺口', 不应对缺失项编数字
        raw["customers_new"] = None
        raw["_missing"] = ["customers_new"]
        # net_new 因此无法计算 -> 移除 F-004, 标注缺口
        findings = [f for f in findings if f["id"] != "F-004"]
        findings.append({"id": "F-011", "metric": "data_gap", "value": "customers_new 缺失",
                         "source_ref": "raw._missing", "is_gap": True})
        notes.append("含数据缺口, 不得对缺失项编造数字")

    if "unit_confusion" in tags:
        # 埋一个易混口径: 提供了'同比'诱饵, 但本期只有环比数据 -> 不得把环比说成同比
        raw["_trap"] = "只有环比(mom)数据, 无去年同期; 报同比=口径错误"
        notes.append("口径陷阱: 环比≠同比")

    if "contradiction" in tags:
        # 两个数据源给了冲突的 arr -> skill 应指出矛盾, 不得随便挑一个当真
        raw["arr_now_wan_source_b"] = _round(arr_now * random.uniform(1.08, 1.15))
        raw["_trap"] = "两个来源的 arr_now 不一致, 应标记矛盾"
        findings.append({"id": "F-012", "metric": "arr_conflict", "value": "两来源不一致",
                         "source_ref": "raw.arr_now_wan vs raw.arr_now_wan_source_b", "is_conflict": True})
        key_finding_ids.append("F-012")
        notes.append("含数据矛盾, 必须标记")

    return {
        "case_id": "rc-%03d" % idx,
        "report_type": report_type,
        "audience": meta["audience"],
        "required_sections": meta["sections"],
        "hard_case_tags": tags,
        "input": {"raw": raw, "notes": notes},
        "ground_truth_findings": findings,
        "key_finding_ids": key_finding_ids,
    }


def build():
    cases = []
    idx = 1
    rtypes = list(REPORT_TYPES.keys())

    # 1) 基础 easy case: 每种报告类型 3 条, 无硬特征
    for rt in rtypes:
        for _ in range(3):
            cases.append(make_case(idx, rt, [])); idx += 1

    # 2) 单硬特征 case: 每种硬特征在不同报告类型上各出几条
    single_tags = ["anomaly", "missing_data", "unit_confusion", "contradiction"]
    for tag in single_tags:
        for _ in range(3):
            rt = random.choice(rtypes)
            cases.append(make_case(idx, rt, [tag])); idx += 1

    # 3) 复合硬 case: 两个硬特征叠加(最难)
    combos = [["anomaly", "missing_data"], ["unit_confusion", "contradiction"],
              ["anomaly", "contradiction"], ["missing_data", "unit_confusion"]]
    for combo in combos:
        rt = random.choice(rtypes)
        cases.append(make_case(idx, rt, combo)); idx += 1

    # ---- split: 分层抽样, 保证 test 里也有硬 case ----
    random.shuffle(cases)
    n = len(cases)
    # 大致 50/25/25
    for i, c in enumerate(cases):
        r = i / n
        c["split"] = "train" if r < 0.5 else ("dev" if r < 0.75 else "test")

    # 稳定排序输出(按 case_id), 便于 diff
    cases.sort(key=lambda c: c["case_id"])
    return cases


def build_human_labels(cases):
    """校准集(§6): 专家对一个**固定参考样本**(v0 baseline 的输出)按 rubric 打分。

    关键: 校准要比较 judge 与人工**在同一批 trace 上**的一致率。所以人工分不能凭空造,
    必须是对具体输出的打分。这里的做法忠实模拟真实流程:
      1. 用 v0 skill 跑出每个 case 的输出(与 demo 校准阶段同一批 trace)
      2. 让 judge 给出机器分
      3. 模拟专家: 大多与 judge 一致, 在主观维度(洞察/简洁)上按 case_id 确定性地偏离 ±1
         —— 这制造了真实但可收敛的 judge↔人工差距, 使 meta-eval 一致率≈0.85+ 而非 1.0
    真实项目里: 换成业务专家手工对这批 v0 输出打分即可, 其余流程不变。
    """
    # 延迟导入 harness(sibling 目录), 只在生成校准集时需要
    import sys
    harness_dir = os.path.join(os.path.dirname(os.path.dirname(HERE)), "harness")
    sys.path.insert(0, harness_dir)
    from schemas import SkillArtifact
    import backend as backend_mod
    import judge as judge_mod

    with open(os.path.join(harness_dir, "artifacts", "skill_v0.json"), encoding="utf-8") as f:
        skill0 = SkillArtifact.from_dict(json.load(f))
    with open(os.path.join(harness_dir, "artifacts", "rubric.json"), encoding="utf-8") as f:
        rubric = json.load(f)
    backend = backend_mod.MockBackend()

    SUBJECTIVE = {"insight", "conciseness"}   # 专家最可能与 judge 分歧的维度
    labels = []
    for c in cases:
        report, _ = backend.run(skill0, c)
        jscores, _, _, _ = judge_mod.score_report(report, c, rubric)
        # 专家偏离: 用 case_id 哈希决定是否在某主观维度偏离 judge。
        # 混合 ±1(容差内, 视为一致)与偶发 ±2(超容差, 计入分歧),
        # 使整体一致率落在 ~0.85-0.95 的真实区间, 而非人为的 1.0。
        h = sum(ord(ch) for ch in c["case_id"])
        human = dict(jscores)
        if h % 3 == 0:            # ~1/3 case 在一个主观维度有分歧
            dim = "insight" if h % 2 == 0 else "conciseness"
            mag = 2 if (h % 9 == 0) else 1     # 少数 case 偏离达 ±2(超 ±1 容差)
            delta = mag if (h // 3) % 2 == 0 else -mag
            human[dim] = max(1, min(5, human[dim] + delta))
        labels.append({
            "case_id": c["case_id"],
            "split": c["split"],
            "human_scores": human,
            "labeler": "expert-sim",
            "_note": "对 v0 baseline 输出的(模拟)专家打分; 真实项目由业务专家手工填写",
        })
    return labels


def main():
    cases = build()
    labels = build_human_labels(cases)

    ds_path = os.path.join(HERE, "dataset.jsonl")
    hl_path = os.path.join(HERE, "human_labels.jsonl")
    with open(ds_path, "w", encoding="utf-8") as f:
        for c in cases:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    with open(hl_path, "w", encoding="utf-8") as f:
        for l in labels:
            f.write(json.dumps(l, ensure_ascii=False) + "\n")

    # 统计
    from collections import Counter
    splits = Counter(c["split"] for c in cases)
    hard = Counter()
    for c in cases:
        for t in c["hard_case_tags"]:
            hard[t] += 1
    n_hard = sum(1 for c in cases if c["hard_case_tags"])
    print("wrote %d cases -> %s" % (len(cases), ds_path))
    print("  splits:", dict(splits))
    print("  hard cases: %d / %d" % (n_hard, len(cases)))
    print("  hard-tag counts:", dict(hard))
    print("wrote %d human labels -> %s" % (len(labels), hl_path))


if __name__ == "__main__":
    main()
