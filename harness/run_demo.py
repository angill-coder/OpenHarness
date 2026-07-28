#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_demo.py — 一键跑通 Phase 0 MVP 优化闭环 (离线, 确定性)

  python3 run_demo.py            # 用 MockBackend, 无需 API
  python3 run_demo.py --real     # 若有 ANTHROPIC_API_KEY + sdk, 用真实 Claude(本环境不可用)

它做完整闭环: 载入 v0 skill + rubric + dataset -> 开优化循环 ->
打印回归看板 -> 落盘 artifacts + 看板 markdown。

验收:
  · skill 分数在 dev 上随版本上升、在 test 上不塌
  · 编造/漏报/口径 等失败模式随版本消退
  · keyword_emphasis 这类讨好裁判的杠杆被 gate 拒绝(演示见 optimizer 不提议它)
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)   # 让模块可直接 import

from schemas import SkillArtifact
import backend as backend_mod
import runner as runner_mod
import loop as loop_mod
import dashboard as dashboard_mod

DATA_DIR = os.path.join(os.path.dirname(HERE), "data", "report_assistant")
ART_DIR = os.path.join(HERE, "artifacts")


def _load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--real", action="store_true", help="尝试用真实 Claude 后端")
    args = ap.parse_args()

    # ---- 载入资产 ----
    with open(os.path.join(ART_DIR, "skill_v0.json"), encoding="utf-8") as f:
        skill0 = SkillArtifact.from_dict(json.load(f))
    with open(os.path.join(ART_DIR, "rubric.json"), encoding="utf-8") as f:
        rubric = json.load(f)

    if not os.path.exists(os.path.join(DATA_DIR, "dataset.jsonl")):
        print("未找到 dataset.jsonl, 先运行: python3 %s/build_dataset.py" % DATA_DIR)
        sys.exit(1)
    cases = _load_jsonl(os.path.join(DATA_DIR, "dataset.jsonl"))

    backend = backend_mod.get_backend(prefer_real=args.real)
    print("[demo] backend = %s | %d cases (train/dev/test)" % (backend.name, len(cases)))

    # ---- Step A: 优化闭环 ----
    from store import ArtifactStore
    store = ArtifactStore()
    store, failure_history, _log = loop_mod.run_loop(
        skill0, cases, rubric, backend, store)

    # ---- Step B: 看板 ----
    console = dashboard_mod.render_console(store, failure_history, rubric["target"])
    print(console)

    # ---- Step C: 显式演示 reward-hacking 被 gate 拒绝 ----
    _demo_reward_hacking(store, cases, rubric, backend)

    # ---- 落盘 ----
    out_store = os.path.join(ART_DIR, "versions.json")
    store.dump(out_store)
    md = dashboard_mod.render_markdown(store, rubric["target"])
    out_md = os.path.join(HERE, "dashboard.md")
    with open(out_md, "w", encoding="utf-8") as f:
        f.write(md)
    hist = store.adopted_history()
    if len(hist) >= 2:
        d0, dN = hist[0]["dev"], hist[-1]["dev"]
        print("\n[demo] 结论: dev overall %.2f -> %.2f (+%.2f), 采纳 %d 版。" % (
            d0["overall"], dN["overall"], dN["overall"] - d0["overall"], len(hist)))
        print("[demo] 已写出: %s, %s" % (os.path.relpath(out_store), os.path.relpath(out_md)))


def _demo_reward_hacking(store, cases, rubric, backend):
    """取收敛后的最优版, 强行套用 keyword_emphasis(讨好裁判杠杆), 跑 gate, 演示被拒。

    这证明: 优化器不会提议它(在 FORBIDDEN 里), 且即便有人手动尝试, 简洁性维度 +
    no_regression gate 也会拦下 —— reward hacking 在这套 rubric 下无法通过。
    """
    from schemas import SkillArtifact
    best = store.latest_adopted()
    if not best:
        return
    dev = [c for c in cases if c["split"] == "dev"]
    base_skill = SkillArtifact.from_dict(best["skill"])
    base_dev = best["dev"]

    hacked = base_skill.clone_with_directive("keyword_emphasis", True, "v-hack",
                                             "手动尝试: 打开 keyword_emphasis 堆术语讨好裁判")
    hrecs = runner_mod.run_split(hacked, dev, rubric, backend, "v-hack")
    hdev = runner_mod.mean_scores(hrecs, rubric)
    tol = next(g["drop_tolerance"] for g in rubric["gates"] if g["id"] == "no_regression")
    conc_drop = base_dev.get("conciseness", 0) - hdev.get("conciseness", 0)
    n_flagged = sum(1 for r in hrecs if "reward_hacking_suspected" in r.flagged)

    print("")
    print("=" * 78)
    print("  REWARD-HACKING 防线演示 (手动套用 keyword_emphasis)")
    print("=" * 78)
    print("    简洁性: %.2f -> %.2f (跌 %.2f, 容差 %.2f)" % (
        base_dev.get("conciseness", 0), hdev.get("conciseness", 0), conc_drop, tol))
    print("    hack_guard: %d/%d 条 case 被标记 reward_hacking_suspected" % (n_flagged, len(hrecs)))
    rejected = conc_drop > tol
    print("    gate 判定: %s" % (
        "拒绝 ❌ (简洁性回退超容差) — 讨好裁判无法通过" if rejected
        else "通过(意外, 需检查 rubric)"))
    print("    注: 优化器本就不会提议此杠杆(它在 optimizer.FORBIDDEN 中), 这里是手动反证。")
    print("=" * 78)


if __name__ == "__main__":
    main()
