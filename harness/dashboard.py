# -*- coding: utf-8 -*-
"""
dashboard.py — 回归看板 (对应架构文档 REGRESSION DASHBOARD)

平台是否成立就看这里:
  - 每版 skill 在 dev/test 上的分维度分数曲线
  - held-out(test) 是否过拟合
  - 失败模式随版本消长
输出到 console + markdown 文件。
"""
from typing import Any, Dict, List


DIMS = ["data_accuracy", "completeness", "insight", "conciseness"]
DIM_ZH = {"data_accuracy": "数据准确性", "completeness": "完整性",
          "insight": "洞察质量", "conciseness": "简洁性"}


def _dims_of(rubric):
    """从 rubric 取维度名 + 中文名; rubric=None 时回退到算数字型(向后兼容)。"""
    if not rubric:
        return DIMS, DIM_ZH, "report-assistant"
    dims = [d["name"] for d in rubric["dimensions"]]
    zh = {d["name"]: d.get("name_zh", d["name"]) for d in rubric["dimensions"]}
    return dims, zh, rubric.get("product", "report-assistant")


def _fmt(scores, key):
    if not scores:
        return "  -  "
    return "%5.2f" % scores.get(key, 0)


def render_console(store, failure_history, target, rubric=None):
    dims, dim_zh, product = _dims_of(rubric)
    lines = []
    P = lines.append
    P("")
    P("=" * 78)
    P("  REGRESSION DASHBOARD — %s" % product)
    P("=" * 78)

    # 分数曲线
    P("")
    P("  [分数曲线 — 每版 skill]   (D=dev  T=test held-out)")
    header = "    ver   " + "".join("%-14s" % dim_zh[d] for d in dims) + "overall   red-line"
    P(header)
    P("    " + "-" * (len(header) - 4))
    for v in store.adopted_history():
        dev, test = v["dev"], v.get("test")
        row = "    %-6s" % v["version"]
        for d in dims:
            dv = dev.get(d, 0)
            tv = test.get(d, 0) if test else None
            cell = "D%.1f" % dv + (("/T%.1f" % tv) if tv is not None else "")
            row += "%-14s" % cell
        row += "%-9s" % ("D%.2f%s" % (dev.get("overall", 0),
                                      ("/T%.2f" % test["overall"]) if test else ""))
        row += "  %d" % dev.get("red_line_fails", 0)
        P(row)

    # 过拟合检查
    P("")
    P("  [过拟合检查]  dev 涨而 test 不涨 = 过拟合信号")
    hist = store.adopted_history()
    if len(hist) >= 2 and hist[-1].get("test") and hist[0].get("test"):
        d_dev = hist[-1]["dev"]["overall"] - hist[0]["dev"]["overall"]
        d_test = hist[-1]["test"]["overall"] - hist[0]["test"]["overall"]
        flag = "⚠️ 可能过拟合" if (d_dev > 0.3 and d_test < 0.1) else "✅ test 同步上升, 未见过拟合"
        P("    dev Δ=%.2f   test Δ=%.2f   %s" % (d_dev, d_test, flag))

    # 失败模式消长
    P("")
    P("  [失败模式随版本消长]")
    for i, fr in enumerate(failure_history):
        total = sum(p["hit_count"] for p in fr)
        top = fr[0]["pattern"] if fr else "(无)"
        P("    v%d: 失败命中 %2d 条, 首要模式: %s" % (i, total, top))

    # 目标达成
    P("")
    P("  [目标达成 vs rubric target]")
    last = hist[-1]["dev"] if hist else {}
    for d in dims:
        tgt = target.get(d)
        val = last.get(d, 0)
        ok = "✅" if (tgt is not None and val >= tgt) else "…"
        P("    %s: %.2f / 目标 %s  %s" % (dim_zh[d], val, ("%.2f" % tgt) if tgt is not None else "-", ok))
    ov = last.get("overall", 0)
    P("    overall: %.2f / 目标 %.2f  %s" % (ov, target["overall"],
                                            "✅" if ov >= target["overall"] else "…"))
    P("=" * 78)
    return "\n".join(lines)


def render_markdown(store, target, rubric=None):
    dims, dim_zh, product = _dims_of(rubric)
    L = []
    L.append("# 回归看板 — %s\n" % product)
    L.append("## 分数曲线\n")
    L.append("| 版本 | 父版 | 打开的 directive | dev overall | test overall | 红线失败 |")
    L.append("|------|------|------------------|-------------|--------------|---------|")
    for v in store.adopted_history():
        L.append("| %s | %s | %s | %.2f | %s | %d |" % (
            v["version"], v["parent"] or "-", ", ".join(v["directives_on"]) or "-",
            v["dev"]["overall"], ("%.2f" % v["test"]["overall"]) if v.get("test") else "-",
            v["dev"].get("red_line_fails", 0)))
    L.append("")
    return "\n".join(L)
