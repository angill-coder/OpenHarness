# -*- coding: utf-8 -*-
"""seed_research_llm.py — 一次性建 research-llm 会话(optimizer_mode=llm_rewrite)。

数据集 = 复用 research-run 的同 3 个 case;走 Session/generator/import_data 正常路径,
不手改 state.json。已存在则拒绝覆盖(需先手动删 sessions/research-llm)。
运行: cd app && python3 seed_research_llm.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import persistence as persist
import session as session_mod

SID = "research-llm"


def main():
    if persist.load_snapshot(SID):
        print("已存在会话 %s;如需重建请先删除 sessions/%s 目录。" % (SID, SID))
        return
    src = persist.load_snapshot("research-run")
    if not src:
        print("找不到 research-run 快照,无法复用数据集。")
        return
    cases = src.get("cases") or []
    if not cases:
        print("research-run 无 cases,放弃。")
        return

    s = session_mod.Session(
        SID,
        "调研洞察汇报助手（LLM 自由改写优化器实验会话）",
        "research_insight",
        optimizer_mode="llm_rewrite",
    )
    s.import_data(cases)
    v = s.view(None)
    print("已建 %s | mode=%s | v0=%s | cases=%d | v0.mode=%s" % (
        SID, v.get("optimizer_mode"), v["current_version"], v["n_cases"],
        s._current()["skill"].instructions.get("mode"),
    ))


if __name__ == "__main__":
    main()
