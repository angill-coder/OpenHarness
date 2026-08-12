#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_demo_research.py — 调研洞察汇报助手 六维离线闭环演示 (确定性, 无需 API key)

  python3 run_demo_research.py

它跑完整闭环: 合成一份带 trap 的调研数据集 + v0 skill(六维 directive 全关) + 六维 rubric
-> ResearchMockBackend 产报告文本+signals -> 六维 judge 照锚点打分 ->
开优化循环逐个打开 directive -> 回归看板 -> 显式演示 buzzword_emphasis
(讨好裁判杠杆)被 gate 拒。
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from schemas import SkillArtifact          # noqa: E402
import backend as backend_mod               # noqa: E402
import runner as runner_mod                 # noqa: E402
import loop as loop_mod                     # noqa: E402
import dashboard as dashboard_mod           # noqa: E402

ART_DIR = os.path.join(HERE, "artifacts")


# --------------------------------------------------------------------------
# v0 skill: 六维 directive 全关(优化起跑线)
# --------------------------------------------------------------------------
def build_v0_skill():
    directives = {k: False for k in backend_mod.RESEARCH_DIRECTIVES}
    return SkillArtifact.from_dict({
        "id": "research-insight-assistant", "version": "v0", "parent_version": None,
        "structure": {
            "flow": [
                {"step": 0, "name": "Intake & Scoping（开场四项信息）",
                 "produces": "report_spec{背景, hypothesis, 重点素材, 报告篇幅}",
                 "rule": "生成前先与用户交互拿齐四项:①汇报背景②材料假设hypothesis③标出高质量重点素材④确认页数或字数;四项都用进报告;hypothesis只验证/证伪、不迎合;页数按每页不超过约1000个中文可见字符折算"},
                {"step": 1, "name": "Source Curation", "subagent": "SourceCurator",
                 "produces": "source_slices", "rule": "素材切片配 S-xxx ID"},
                {"step": 2, "name": "Claim Analysis", "subagent": "Analyst",
                 "produces": "claims", "rule": "抽 claim 挂 source_ids, 强制标冲突"},
                {"step": 3, "name": "Insight Extraction", "subagent": "Insight",
                 "produces": "insights", "rule": "案例提炼成规律, 只在 claims 内推理"},
                {"step": 4, "name": "Narrative Composition", "subagent": "Writer",
                 "produces": "draft_report", "rule": "论断携带 [S-xxx]/[C-xxx]"},
                {"step": 5, "name": "Verification", "subagent": "Verifier",
                 "produces": "verification_report", "rule": "独立对抗, 查编造/混用/剔噪", "loop_back_to": 4},
                {"step": 6, "name": "Deliver", "produces": "final_doc"},
            ],
            "subagents": [
                {"name": "SourceCurator", "responsibility": "素材切片、配 ID、去重"},
                {"name": "Analyst", "responsibility": "抽 claim 挂 source、标冲突、判信源充分性"},
                {"name": "Insight", "responsibility": "提炼规律/归因/趋势/建议, 只在 claims 内"},
                {"name": "Writer", "responsibility": "金字塔组织、携带引用、结构化呈现"},
                {"name": "Verifier", "responsibility": "对抗查错(编造/混用/噪音/越界)", "independent": True},
            ],
            "memory_schema": ["config", "facts", "learned_rules"],
        },
        "instructions": {
            "prose": "你是调研洞察汇报助手。基于给定异构素材, 为最高管理层产出可编辑的调研报告。",
            "directives": directives,
        },
        "few_shots": [],
        "memory_content": {"config": {}, "facts": {}, "learned_rules": []},
        "changelog": "v0 六维 directive 全关, 作为优化起跑线(结构定上限)。",
    })


# --------------------------------------------------------------------------
# 合成数据集: 三类 case, 各埋不同 trap, 让每个 directive 都能带来可见的 mean 提升
# --------------------------------------------------------------------------
CASE_TYPES = {
    # conflict: 素材冲突(红线) + 过度外推 + 噪音 + 一条单一信源 claim
    "conflict": {
        "topic": "DeepSeek 用户使用时长分析",
        "tags": ["source_conflict", "unsupported_extrapolation", "noise_present"],
        "unsupportable": [],
        "noise": ["S-009"],
        "claims": [{"id": "C-001", "source_ids": ["S-001"]},
                   {"id": "C-002", "source_ids": ["S-001", "S-002"]}],
    },
    # unsupportable: 素材答不了却被硬答(红线) + 无噪音 + 双信源 claim(测 abstract_cases)
    "unsupportable": {
        "topic": "AI Native 公司 Agent 落地分析",
        "tags": ["missing_evidence"],
        "unsupportable": ["下一步投放方向建议？"],
        "noise": [],
        "claims": [{"id": "C-003", "source_ids": ["S-011", "S-012"]}],
    },
    # single_source: 无红线, 一条单一信源 claim + 噪音
    "single_source": {
        "topic": "企业知识库检索质量调研",
        "tags": ["noise_present"],
        "unsupportable": [],
        "noise": ["S-029"],
        "claims": [{"id": "C-005", "source_ids": ["S-021"]}],
    },
}
_ORDER = ["conflict", "unsupportable", "single_source"]
_SECTIONS = ["摘要", "核心发现", "归因分析", "趋势判断", "对我们的启示/建议"]


def build_dataset():
    """train4 / dev3 / test3, 循环三类。确定性(不含随机)。"""
    plan = [("train", 4), ("dev", 3), ("test", 3)]
    cases, n = [], 0
    for split, k in plan:
        for j in range(k):
            ctype = _ORDER[(n) % len(_ORDER)]
            t = CASE_TYPES[ctype]
            cases.append({
                "case_id": "rr-%s-%02d" % (split, j),
                "split": split, "audience": "exec", "topic": t["topic"],
                "report_type": "research_insight",
                "required_sections": list(_SECTIONS),
                "hard_case_tags": list(t["tags"]),
                "input": {"brief": "面向高管, 说明变化原因和下一步, 不超过1.5页"},
                "human_report": {
                    "supported_claims": t["claims"],
                    "key_claim_ids": [c["id"] for c in t["claims"]],
                    "expected_insights": [{"id": "I-%02d" % n, "insight": "提炼出的非平凡判断"}],
                    "unsupportable_questions": list(t["unsupportable"]),
                    "noise_source_ids": list(t["noise"]),
                    "traps": [{"type": tag} for tag in t["tags"] if tag != "noise_present"],
                },
            })
            n += 1
    return cases


def main():
    rubric = json.load(open(os.path.join(ART_DIR, "v2_rubric_research.json"), encoding="utf-8"))
    skill0 = build_v0_skill()
    cases = build_dataset()
    backend = backend_mod.get_backend(product_id="research_insight")
    print("[demo] backend = %s | %d cases (train/dev/test) | 六维 rubric %s"
          % (backend.name, len(cases), rubric["version"]))

    # ---- Step A: 优化闭环 ----
    from store import ArtifactStore
    store = ArtifactStore()
    store, failure_history, _log = loop_mod.run_loop(
        skill0, cases, rubric, backend, store,
        max_rounds=20, plateau_patience=3)

    # ---- Step B: 看板 ----
    print(dashboard_mod.render_console(store, failure_history, rubric["target"], rubric=rubric))

    # ---- Step C: reward-hacking 防线演示 ----
    _demo_reward_hacking(store, cases, rubric, backend)

    # ---- 落盘 ----
    out_store = os.path.join(ART_DIR, "versions_research.json")
    store.dump(out_store)
    md = dashboard_mod.render_markdown(store, rubric["target"], rubric=rubric)
    out_md = os.path.join(HERE, "dashboard_research.md")
    open(out_md, "w", encoding="utf-8").write(md)
    hist = store.adopted_history()
    if len(hist) >= 2:
        d0, dN = hist[0]["dev"], hist[-1]["dev"]
        print("\n[demo] 结论: dev overall %.2f -> %.2f (+%.2f), 采纳 %d 版。"
              % (d0["overall"], dN["overall"], dN["overall"] - d0["overall"], len(hist)))
        print("[demo] 已写出: %s, %s" % (os.path.relpath(out_store), os.path.relpath(out_md)))


def _demo_reward_hacking(store, cases, rubric, backend):
    """取最优版, 手动套用 buzzword_emphasis(讨好裁判杠杆), 跑 gate, 演示被拒。"""
    best = store.latest_adopted()
    if not best:
        return
    dev = [c for c in cases if c["split"] == "dev"]
    base_skill = SkillArtifact.from_dict(best["skill"])
    base_dev = best["dev"]
    hacked = base_skill.clone_with_directive("buzzword_emphasis", True, "v-hack",
                                             "手动尝试: 打开 buzzword_emphasis 堆大词讨好裁判")
    hrecs = runner_mod.run_split(hacked, dev, rubric, backend, "v-hack")
    hdev = runner_mod.mean_scores(hrecs, rubric)
    tol = next(g["drop_tolerance"] for g in rubric["gates"] if g["id"] == "no_regression")
    expr_drop = base_dev.get("expression", 0) - hdev.get("expression", 0)
    n_flagged = sum(1 for r in hrecs if "reward_hacking_suspected" in r.flagged)

    print("\n" + "=" * 78)
    print("  REWARD-HACKING 防线演示 (手动套用 buzzword_emphasis)")
    print("=" * 78)
    print("    表达与受众契合: %.2f -> %.2f (跌 %.2f, 容差 %.2f)"
          % (base_dev.get("expression", 0), hdev.get("expression", 0), expr_drop, tol))
    print("    hack_guard: %d/%d 条 case 被标记 reward_hacking_suspected" % (n_flagged, len(hrecs)))
    print("    gate 判定: %s" % ("拒绝 ❌ (表达维度回退超容差) — 讨好裁判无法通过" if expr_drop > tol
                                else "通过(意外, 需检查 rubric)"))
    print("    注: 优化器本就不会提议此杠杆(它在 optimizer.FORBIDDEN 中), 这里是手动反证。")
    print("=" * 78)


if __name__ == "__main__":
    main()
