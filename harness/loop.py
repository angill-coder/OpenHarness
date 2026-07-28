# -*- coding: utf-8 -*-
"""
loop.py — 优化闭环编排 (对应架构文档「优化闭环的一次迭代」+ Optimizer 机制3/5)

一次迭代:
  ① Runner 在 train+dev 跑当前 skill -> records
  ② Judge 打分(runner 内完成)
  ③ Clustering 把低分 case 聚成失败模式
  ④ Optimizer 读失败报告 -> 提一个候选改动
  ⑤ Gate: 候选在 dev 重跑; 目标维度↑ 且 其它维度不塌 且 不引入红线 -> 采纳
       (每 K 轮在 test 上验证防过拟合)
  ⑥ 收敛判定: A 达 target / B 连续无提升(平台期) / C 预算上限
"""
from typing import Any, Dict, List
import runner as runner_mod
import clustering as clustering_mod
import optimizer as optimizer_mod


def _split(cases, name):
    return [c for c in cases if c["split"] == name]


def _dim_regressed(new_scores, old_scores, dims_to_watch, tolerance, all_dims):
    """任一(非目标)维度较上版下跌超过容差 => 塌了。"""
    for d in all_dims:
        if d in dims_to_watch:
            continue
        if old_scores.get(d, 0) - new_scores.get(d, 0) > tolerance:
            return d
    return None


def run_loop(skill0, cases, rubric, backend, store,
             max_rounds=12, plateau_patience=2, test_every=1, verbose=True):
    log = []
    def say(s):
        log.append(s)
        if verbose:
            print(s)

    train_dev = _split(cases, "train") + _split(cases, "dev")
    dev = _split(cases, "dev")
    test = _split(cases, "test")
    no_reg_tol = next((g["drop_tolerance"] for g in rubric["gates"] if g["id"] == "no_regression"), 0.15)
    target = rubric["target"]
    product = rubric.get("product")
    all_dims = [d["name"] for d in rubric["dimensions"]]

    # v0 基线
    recs0 = runner_mod.run_split(skill0, train_dev, rubric, backend, "v0")
    dev0 = runner_mod.mean_scores([r for r in recs0 if r.dataset_split == "dev"], rubric)
    test0 = runner_mod.mean_scores(
        runner_mod.run_split(skill0, test, rubric, backend, "v0"), rubric)
    store.add(skill0, dev0, test0, adopted=True, proposal=None)
    failure_history = [clustering_mod.cluster(recs0, product=product)]
    say("[loop] v0 基线: dev overall=%.2f, test overall=%.2f" % (dev0["overall"], test0["overall"]))

    history = []          # optimizer 记忆: 试过的改动 + 结果
    current = skill0
    cur_dev = dev0
    plateau = 0
    vnum = 0

    for rnd in range(1, max_rounds + 1):
        # ③ 聚类当前失败
        recs = runner_mod.run_split(current, train_dev, rubric, backend, current.version)
        failures = clustering_mod.cluster(recs, product=product)

        # ④ 提议
        proposal = optimizer_mod.propose(current, failures, history)
        if proposal is None:
            say("[round %d] optimizer 无更多可提议改动 => 收敛(平台期)。" % rnd)
            # 平台期分流: 失败是否还集中且架构性?
            _plateau_diagnosis(say, failures)
            break

        vnum += 1
        cand_ver = "v%d" % vnum
        candidate = optimizer_mod.apply_proposal(current, proposal, cand_ver)
        say("[round %d] 提议 %s: %s | 假设: %s" % (
            rnd, cand_ver, proposal["change"], proposal["hypothesis"]))

        # ⑤ Gate: dev 重跑
        cand_recs = runner_mod.run_split(candidate, dev, rubric, backend, cand_ver)
        cand_dev = runner_mod.mean_scores(cand_recs, rubric)

        target_dims = proposal["affected_dims"]
        improved = any(cand_dev.get(d, 0) - cur_dev.get(d, 0) > 0.001 for d in target_dims)
        regressed = _dim_regressed(cand_dev, cur_dev, target_dims, no_reg_tol, all_dims)
        red_line_new = cand_dev.get("red_line_fails", 0) > cur_dev.get("red_line_fails", 0)

        adopt = improved and (regressed is None) and (not red_line_new)

        if adopt:
            # 每 K 轮 test 验证
            cand_test = None
            if vnum % test_every == 0:
                cand_test = runner_mod.mean_scores(
                    runner_mod.run_split(candidate, test, rubric, backend, cand_ver), rubric)
            store.add(candidate, cand_dev, cand_test, adopted=True, proposal=proposal)
            history.append({**_hkey(proposal), "result": "adopted",
                            "delta": round(cand_dev["overall"] - cur_dev["overall"], 3)})
            say("        -> 采纳 ✅  dev overall %.2f -> %.2f (目标维度 %s ↑)" % (
                cur_dev["overall"], cand_dev["overall"], "/".join(target_dims)))
            current = candidate
            cur_dev = cand_dev
            plateau = 0
            failure_history.append(failures)
        else:
            reason = ("目标维度未涨" if not improved
                      else ("维度 %s 回退" % regressed if regressed else "引入红线失败"))
            store.add(candidate, cand_dev, None, adopted=False, proposal=proposal)
            history.append({**_hkey(proposal), "result": "rejected", "reason": reason})
            say("        -> 丢弃 ❌ (%s), 写入 history 不再重试" % reason)
            plateau += 1

        # ⑥ 收敛: A 达 target
        if all(cur_dev.get(d, 0) >= target.get(d, 0) for d in all_dims) \
           and cur_dev["overall"] >= target["overall"]:
            say("[round %d] 达到 rubric target => 成功收敛。" % rnd)
            break
        # B 平台期
        if plateau >= plateau_patience:
            say("[round %d] 连续 %d 轮无有效改动 => 平台期, 停止。" % (rnd, plateau))
            _plateau_diagnosis(say, failures)
            break

    return store, failure_history, log


def _hkey(p):
    return {"target": p["target"], "directive": p.get("directive"), "fewshot": p.get("fewshot")}


def _plateau_diagnosis(say, failures):
    """平台期分流(机制5下半支): 失败是否指向架构缺失 -> 触发结构优化信号。"""
    if not failures:
        say("        诊断: 失败模式已零散 => 当前结构够用, 收工。")
        return
    top = failures[0]
    # 若剩余失败仍是 directive 能覆盖的, 说明只是没调完; 若是结构性(此处示意), 触发信号
    if top.get("directive_hint") is None:
        say("        诊断: 首要失败'%s'无对应指令级修法 => 可能是架构性缺陷," % top["pattern"])
        say("             触发 Phase3(结构优化)信号: 需人工回去改 v0 结构。")
    else:
        say("        诊断: 剩余失败仍是指令/内容级(%s), 未到动结构的时候。" % top["pattern"])
