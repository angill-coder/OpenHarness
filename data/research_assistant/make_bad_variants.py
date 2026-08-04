#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_bad_variants.py — 从一个"好 case"(素材+human_report)半自动产出「坏报告」变体骨架,
                        用于快速搭建调研洞察 judge 的校准集(高质锚点 + 各类失分锚点)。

思路: 校准需要既有"好报告打高"、也有"坏报告在不同维度打低"的样本。本脚本读 case 的
human_report, **按里面已定义的 traps / unsupportable_questions / noise / 单一信源 claim**,
自动派生对应的坏报告变体骨架——每个变体只犯一种错(隔离到某个维度),附:
  · report_text 骨架(含【填写:…】提示, 具体内容你补, 好的部分可参考好报告)
  · 建议六维分 + 每维 reasoning(judge 该怎么扣)
你改完即可作为 RecordedJudge 的评分样本喂进 app(校准 judge 用)。

用法:
  python3 make_bad_variants.py --case rr-ds-timelen
      读 data/research_assistant/dataset.jsonl 里该 case, 生成
      data/research_assistant/bad_variants.<case>.jsonl + 打印摘要表。

  python3 make_bad_variants.py --case rr-ds-timelen --into-session ds-timelen
      额外把 好case + 各坏变体 直接装进 app 会话 ds-timelen(导入数据+贴报告+贴六维分),
      启动 app 即可并排看区分度。(需要 app/ 可用)

纯 stdlib(Python 3.9)。坏变体**不写进 dataset.jsonl**(尺子保持干净), 只写单独文件/会话。
"""
import argparse
import copy
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATASET = os.path.join(HERE, "dataset.jsonl")

# 六维顺序(与 rubric_research.json 一致)
DIMS = ["traceability", "structure", "narrative", "insight", "coverage", "expression"]


def _first(seq, default=None):
    return seq[0] if seq else default


def _single_source_claim(human_report):
    for c in human_report.get("supported_claims", []):
        if len(c.get("source_ids", [])) < 2:
            return c
    return None


def _has_trap(human_report, t):
    return any(tr.get("type") == t for tr in human_report.get("traps", []))


def _trap_detail(human_report, t):
    for tr in human_report.get("traps", []):
        if tr.get("type") == t:
            return tr.get("detail", "")
    return ""


# ---------------------------------------------------------------------------
# 缺陷目录: 每项 = (类型, 触发条件, 目标维度→分, 骨架生成, reasoning 生成)
# 只有 case 的 human_report 里"存在对应把柄"时才派生该变体(否则不硬造)。
# 基线其余维度给 4(否则很好、只坏在一处), 以便把失分隔离到目标维度。
# ---------------------------------------------------------------------------
def _mk(case, defect_type, note, overrides, reasoning, body):
    v = copy.deepcopy(case)
    v["case_id"] = "%s-bad-%s" % (case["case_id"], defect_type)
    v["_demo_variant"] = note
    scores = {d: 4 for d in DIMS}
    scores.update(overrides)
    topic = case.get("topic", "调研主题")
    report_text = "# %s\n\n%s\n\n## 其余分析\n【填写:补齐其余章节(事实/结构/人群等),这部分可写好,坏点只在上面那一处】\n" % (topic, body)
    return {"case_id": v["case_id"], "defect_type": defect_type, "note": note,
            "case": v, "report_text": report_text,
            "suggested_scores": scores, "reasoning": reasoning}


def build_variants(case):
    human_report = case.get("human_report", {})
    out = []

    # 1) 硬答"素材答不了的问题" → 可回溯性红线(2)
    q = _first(human_report.get("unsupportable_questions", []))
    if q:
        out.append(_mk(case, "hardanswer",
            "硬答素材答不了的问题(踩可回溯性红线)",
            {"traceability": 2, "insight": 3},
            {"traceability": "素材无数据却硬给结论回答『%s』→ 2分封顶(红线)" % q,
             "insight": "建议无据 → 3"},
            "## 建议\n针对『%s』,我们判断【填写:硬给一个素材根本支撑不了的具体结论/动作,如具体投放渠道与预算】。" % q))

    # 2) 越界外推(趋势将持续) → 可回溯性红线(2) + 洞察(3)
    if _has_trap(human_report, "unsupported_extrapolation"):
        out.append(_mk(case, "overclaim",
            "把历史趋势外推成未来定论(越界)",
            {"traceability": 2, "insight": 3},
            {"traceability": "仅历史趋势却断言『将持续/未来再翻倍/天花板远未到』→ 越界外推,2分封顶",
             "insight": "趋势判断过度外推、未标置信 → 3"},
            "## 趋势判断\n【填写:基于历史线性趋势,断言未来将持续增长/再翻倍且'增长确定可持续'——不要留任何不确定性】"))

    # 3) 孤证当全局定论(单一信源未降级) → 可回溯性
    ssc = _single_source_claim(human_report)
    if ssc or _has_trap(human_report, "single_modality"):
        cid = (ssc or {}).get("id", "某单一信源结论")
        out.append(_mk(case, "single_source",
            "单一信源/定性结论当作全局硬定论,未标待验证",
            {"traceability": 2},
            {"traceability": "仅单一信源(如访谈)就下全局硬定论(%s)、未标『定性/待验证』→ 2分封顶" % cid},
            "## 结论\n【填写:拿一条仅靠单一信源/几例访谈支撑的结论,写成'已确证的全局事实',不加任何'待验证/定性'限定】"))

    # 4) 混用冲突素材(不指出口径差异) → 可回溯性红线(2)
    if _has_trap(human_report, "source_conflict"):
        out.append(_mk(case, "conflict",
            "把口径冲突的素材直接混用",
            {"traceability": 2},
            {"traceability": "把口径/来源冲突的素材当一致直接比较、未指出不可比 → 2分封顶(红线)"},
            "## 对比\n【填写:把两个口径不同的数(如不同来源/不同定义)直接放一起比较得结论,不说明口径不可比】"))

    # 4b) 选有利口径、不注明(口径依赖却当稳健结论) → 可回溯性(3)
    if _has_trap(human_report, "metric_caveat"):
        out.append(_mk(case, "metric_caveat",
            "选有利口径且不注明(轻度曲解)",
            {"traceability": 3},
            {"traceability": "挑选对结论有利的口径/分母得出强结论、不注明口径依赖(如换口径结论就不成立) → 3分。detail: %s" % _trap_detail(human_report, "metric_caveat")},
            "## 关键指标\n【填写:挑一个口径依赖的数(如用最有利的分母/样本),把结论说得斩钉截铁,不加任何口径说明或敏感性提示】"))

    # 4c) 无视样本/选择偏差 → 可回溯性(3)
    if _has_trap(human_report, "selection_bias"):
        out.append(_mk(case, "selection_bias",
            "无视样本/选择偏差,把有偏结果当客观",
            {"traceability": 3},
            {"traceability": "已知样本存在系统性偏差却不提示、把有偏比较当客观结论 → 3分。detail: %s" % _trap_detail(human_report, "selection_bias")},
            "## 对比结论\n【填写:拿一个已知有偏差的样本/来源,直接得出对我方有利的横向比较结论,不提任何偏差或'需谨慎解读'】"))

    # 4d) 把异常/离群当趋势(错误归因) → 可回溯性红线(2) + 洞察(3)
    if _has_trap(human_report, "outlier_confound"):
        out.append(_mk(case, "outlier",
            "把一次性异常/离群当成趋势或错误归因",
            {"traceability": 2, "insight": 3},
            {"traceability": "把明显的一次性异常/离群点当作长期趋势或错误归因、未作交叉解释 → 2分封顶。detail: %s" % _trap_detail(human_report, "outlier_confound"),
             "insight": "基于离群点过度解读 → 3"},
            "## 趋势\n【填写:抓住一个明显的一次性峰值/异常(如某节假日突增),硬说成'持续趋势'或错误归因,不作交叉/情境解释】"))

    # 5) 引用噪音片段充数 → 提炼与洞察(3)
    noise = human_report.get("noise_source_ids", [])
    if noise:
        out.append(_mk(case, "noise",
            "引用噪音片段充数(剔噪失败)",
            {"insight": 3},
            {"insight": "引用与主题无关的噪音片段(%s)充数、未剔噪 → 3" % ", ".join(noise)},
            "## 附加发现\n【填写:引用噪音源 %s 的内容,硬扯进结论,好像它支撑了什么】" % ", ".join(noise)))

    # 6) 案例罗列而非提炼 → 提炼与洞察(3)
    if human_report.get("expected_insights"):
        out.append(_mk(case, "listing",
            "案例逐条罗列、不提炼成规律",
            {"insight": 3},
            {"insight": "把访谈/案例逐条抄一遍、不抽象成模式或归因 → 3(未命中 expected_insights)"},
            "## 用户案例\n【填写:把访谈引用逐条摘抄 5~8 条,不做任何提炼/归因/共性总结】"))

    # 7) 摘要是背景铺陈而非结论 → 结构封顶(2)
    out.append(_mk(case, "summary",
        "摘要写成背景铺陈而非结论",
        {"structure": 2},
        {"structure": "摘要全是背景/过程/数据来源介绍、无一条结论 → 2分封顶"},
        "## 摘要\n- 本报告基于多维度数据对【填写:主题】进行了系统性分析。\n- 数据来源包括【填写:素材来源罗列】。\n- 报告涵盖【填写:维度罗列】等多个方面。"))

    # 8) "不是,而是"句式 + 术语注水 → 表达封顶(2)
    out.append(_mk(case, "style",
        "'不是,而是'句式 + 术语注水",
        {"expression": 2},
        {"expression": "出现『不是…而是…』句式 + '数据驱动/闭环/赋能/范式重构'式术语注水 → 2分封顶(风格红线)"},
        "## 核心发现\n【填写:用『增长并非来自X,而是来自Y』句式,并堆砌'数据驱动/闭环赋能/范式重构/全方位多层次'等大词】"))

    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--case", required=True, help="dataset.jsonl 里的 case_id")
    ap.add_argument("--dataset", default=DATASET)
    ap.add_argument("--into-session", default=None, help="可选: 直接装进该 app 会话 sid")
    args = ap.parse_args()

    cases = {json.loads(l)["case_id"]: json.loads(l)
             for l in open(args.dataset, encoding="utf-8") if l.strip()}
    if args.case not in cases:
        raise SystemExit("未找到 case: %s (可选: %s)" % (args.case, ", ".join(cases)))
    case = cases[args.case]
    variants = build_variants(case)

    out_path = os.path.join(HERE, "bad_variants.%s.jsonl" % args.case)
    with open(out_path, "w", encoding="utf-8") as f:
        for v in variants:
            f.write(json.dumps(v, ensure_ascii=False) + "\n")

    print("为 case '%s' 生成 %d 个坏变体骨架 -> %s\n" % (args.case, len(variants), os.path.relpath(out_path)))
    print("%-14s %-26s %s" % ("defect", "打到的维度", "note"))
    print("-" * 78)
    for v in variants:
        hit = ", ".join("%s=%d" % (d, s) for d, s in v["suggested_scores"].items() if s < 4)
        print("%-14s %-26s %s" % (v["defect_type"], hit, v["note"]))
    print("\n下一步: 打开该文件, 把每条 report_text 里的【填写:…】补成具体正文(坏点保留、其余可写好),")
    print("       核对建议六维分, 然后作为 RecordedJudge 样本喂进 app(或用 --into-session 一键装入)。")

    if args.into_session:
        _load_into_session(args.into_session, case, variants)


def _load_into_session(sid, good_case, variants):
    import sys
    app_dir = os.path.join(os.path.dirname(HERE), "..", "app")
    sys.path.insert(0, os.path.abspath(app_dir))
    import session as S  # noqa
    import persistence as P  # noqa
    snap = P.load_snapshot(sid)
    if not snap:
        raise SystemExit("会话 %s 不存在, 请先在 app 里建好并导入好 case" % sid)
    sess = S.Session.restore(snap)
    # 合并: 保留会话已有的所有 case(如多条真实好 case), 再并入本 case + 其坏变体(按 case_id 去重)
    merged = {c["case_id"]: c for c in sess.cases}
    merged[good_case["case_id"]] = good_case
    for v in variants:
        merged[v["case_id"]] = v["case"]
    sess.import_data(list(merged.values()))
    for v in variants:
        sess.import_output(v["case_id"], v["report_text"])
        sess.import_judgment(v["case_id"], v["suggested_scores"], v["reasoning"])
    print("\n已并入会话 %s: +%d 坏变体(该会话现共 %d 条)。启动 app 打开该会话即可并排查看。"
          % (sid, len(variants), len(sess.cases)))


if __name__ == "__main__":
    main()
