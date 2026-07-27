# -*- coding: utf-8 -*-
"""
session_eval.py — 评估 / 记录叠加 / 失败聚类 / rubric 编辑 / 推进 (SessionEval mixin)

由 session.py 组合进 class Session。本文件负责「跑分与推进」:
  import_data   —— 导入数据集(校验最小字段) + 可选初始人工标注, 触发首次评估
  evaluate      —— 用当前版本 skill 跑分, 叠加真实产物, 聚类失败, 算 dev/test 均分
  _apply_recorded —— 把平台真实报告/评分覆盖 mock(有则真实, 无则占位)
  _rec_view / _output_summary —— 组装单条 case 的可呈现视图
  edit_rubric   —— 改权重/阈值, 存为新 rubric 版本并重评
  advance       —— optimizer 提候选 -> dev gate -> 采纳/拒绝

依赖 SessionCore 的 _current/_human_for/_human_checks_for/view/_save 等。
"""
import copy
import sys
import os
from typing import Any, Dict, List, Optional

HARNESS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness")
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

from schemas import EvalRecord                     # noqa: E402
import runner as runner_mod                         # noqa: E402
import judge as judge_mod                           # noqa: E402
import clustering as clustering_mod                 # noqa: E402
import optimizer as optimizer_mod                   # noqa: E402
from workbuddy_batch.dataset import openharness_rows  # noqa: E402

import persistence as persist                        # noqa: E402


class SessionEval:
    """评估与推进 mixin。"""

    # ---------- 数据导入 ----------
    def import_data(self, rows: List[Dict[str, Any]], labels: Optional[List[Dict]] = None, account=None):
        # 同一份 openharness-wb/v1 JSON 同时服务 WB 生成和平台评测。
        # 旧数组/JSONL 在迁移期仍可读取，但不再静默跳过坏数据。
        normalized = openharness_rows(rows)
        is_research = self.rubric.get("product") == "research_insight"
        clean = []
        for source in normalized:
            r = copy.deepcopy(source)
            if not is_research and "ground_truth_findings" not in r:
                raise ValueError(
                    "算数字型 case %s 缺少 ground_truth_findings"
                    % r["case_id"]
                )
            r.setdefault("split", "dev")
            if r["split"] not in {"train", "dev", "test"}:
                raise ValueError(
                    "case %s 的 split 非法: %s"
                    % (r["case_id"], r["split"])
                )
            r.setdefault("hard_case_tags", [])
            r.setdefault("required_sections", [])
            r.setdefault("audience", self.detected.get("audience", "exec"))
            r.setdefault("key_finding_ids", [])
            clean.append(r)
        self.cases = clean
        # 可选: 导入初始人工标注(作用于 v0)
        if labels:
            v0 = self.versions[0]["version"]
            store = self._human_for(v0, account)
            for l in labels:
                if "case_id" in l and "human_scores" in l:
                    store[l["case_id"]] = l["human_scores"]
        persist.append_event(self.id, "import_data", {
            "n_cases": len(clean), "splits": self._split_counts(),
            "with_initial_labels": bool(labels),
        })
        # 重新评估当前版本
        r = self.evaluate(account)
        self._save()
        return r

    # ---------- 评估当前版本 ----------
    def evaluate(self, account=None):
        """计算与账号无关的基础量(judge/mock 分、失败聚类、dev/test 均分)并暂存基础记录;
        人工标注叠加与校准是按账号的, 放到 view(account) 时再算(线程安全: 不把账号数据写进共享缓存)。"""
        if not self.cases:
            return {"error": "尚未导入数据"}
        cur = self._current()
        skill = cur["skill"]
        ver = cur["version"]

        train_dev = [c for c in self.cases if c["split"] in ("train", "dev")]
        dev = [c for c in self.cases if c["split"] == "dev"]
        test = [c for c in self.cases if c["split"] == "test"]

        # 基础评估与账号无关: 不注入人工分(人工 overlay 在 view 时按账号叠加)
        recs_all = runner_mod.run_split(skill, self.cases, self.rubric, self.backend, ver, {})
        # 用平台真实报告 + LLM-judge 评分覆盖 mock(有则真实, 无则保留 mock 作占位)
        self._apply_recorded(recs_all, ver)
        dev_recs = [r for r in recs_all if r.dataset_split == "dev"]
        test_recs = [r for r in recs_all if r.dataset_split == "test"]

        cur["dev"] = runner_mod.mean_scores(dev_recs, self.rubric) if dev else runner_mod.mean_scores(recs_all, self.rubric)
        cur["test"] = runner_mod.mean_scores(test_recs, self.rubric) if test else None
        cur["failures"] = clustering_mod.cluster(
            [r for r in recs_all if r.dataset_split in ("train", "dev")] or recs_all,
            product=self.rubric.get("product"))
        cur["_recs"] = recs_all         # 暂存基础记录(账号无关), 供 view(account) 叠加人工分

        # 记录失败历史(用于看板消长)
        if len(self.failure_history) <= self.current_idx:
            self.failure_history.append(cur["failures"])
        else:
            self.failure_history[self.current_idx] = cur["failures"]
        return self.view(account)

    def _apply_recorded(self, recs, version):
        """把平台真实产物叠加到 mock 记录上:
          · report_outputs -> 记录的 output 换成真实报告文本(标 recorded)
          · judge 分(优先逐 check 派生, 其次旧 import_judgment) -> 覆盖 mock 六维分并重算红线
        无真实评分的 case 保留 mock 分(占位/自测), 由 score_source 区分。
        人工分(逐 check 派生的 human_label)与账号相关, 不在此叠加, 由 view(account) 时按账号算。"""
        outs = self.report_outputs.get(version, {})
        juds = self.report_judgments.get(version, {})
        jchecks = self.judge_checks.get(version, {})
        floor = judge_mod._hard_floor(self.rubric, "traceability")
        for r in recs:
            rt = outs.get(r.case_id)
            if rt is not None:
                r.output = {"report_text": rt, "signals": {}, "recorded": True,
                            "audience": r.output.get("audience", "exec")}
            # judge 分:逐 check 派生 > 旧 import_judgment > mock
            jc = (jchecks.get(r.case_id) or {}).get("checks")
            jv = juds.get(r.case_id)
            if jc:
                r.scores = judge_mod.dim_from_checks(jc, self.rubric)
                r.judge_reasoning = (jchecks.get(r.case_id) or {}).get("reasoning", {})
                r.score_source = "recorded"
            elif jv is not None:
                r.scores = {d: int(s) for d, s in jv["scores"].items() if d in self.dims}
                r.judge_reasoning = dict(jv.get("reasoning", {}))
                r.flagged = list(jv.get("flagged", []))
                r.score_source = "recorded"
            else:
                r.score_source = "mock"
            if r.score_source == "recorded":
                tr = r.scores.get("traceability")
                r.case_failed_gate = floor is not None and tr is not None and tr < floor
                if r.case_failed_gate and not any(str(f).startswith("RED_LINE") for f in r.flagged):
                    r.flagged.append("RED_LINE:traceability<%d" % floor)

    def _rec_view(self, r: EvalRecord, account=None) -> Dict[str, Any]:
        # 平台真实报告文本(当前版本已粘贴的), 供人工按 rubric 逐维标注时对照阅读
        ver = self._current()["version"]
        real_report = self.report_outputs.get(ver, {}).get(r.case_id)
        hc = self._human_checks_for(ver, account).get(r.case_id, {})      # 当前账号的逐 check 标注
        jc = (self.judge_checks.get(ver, {}).get(r.case_id) or {}).get("checks", {})
        jr = (self.judge_checks.get(ver, {}).get(r.case_id) or {}).get("reasoning", {})
        human_label = judge_mod.dim_from_checks(hc, self.rubric) if hc \
            else self._human_for(ver, account).get(r.case_id)
        return {
            "case_id": r.case_id, "split": r.dataset_split,
            "scores": r.scores, "judge_reasoning": r.judge_reasoning,
            "flagged": r.flagged, "red_line": r.case_failed_gate,
            "human_label": human_label,
            "output_summary": self._output_summary(r.output),
            "report_text": real_report,          # None 表示该 case 尚未导入真实报告
            "score_source": getattr(r, "score_source", "mock"),  # recorded=平台LLM-judge真实分 / mock=占位
            # 逐 check 层(真实标注/校准线)
            "check_human": hc,                    # {check_id: 1/0.5/0} 当前账号
            "check_judge": jc,                    # {check_id: 1/0.5/0} Opus judge
            "check_judge_reason": jr,
            "dims_human": judge_mod.dim_from_checks(hc, self.rubric) if hc else {},
            "dims_judge": judge_mod.dim_from_checks(jc, self.rubric) if jc else {},
        }

    def _output_summary(self, out):
        # 调研洞察: 报告文本 + signals; 算数字型: flag 概要
        if "report_text" in out or "signals" in out:
            txt = out.get("report_text", "") or ""
            return {
                "report_text_preview": txt[:300],
                "report_len": len(txt),
                "signals_on": [k for k, v in out.get("signals", {}).items() if v],
            }
        return {
            "sections": out.get("sections", []),
            "cited": len(out.get("findings_cited", [])),
            "uncited": len(out.get("findings_uncited", [])),
            "fabricated": out.get("fabricated_values", []),
            "unit_error": out.get("unit_error", False),
            "anomaly_reported": out.get("anomaly_reported", False),
            "buzzword": out.get("buzzword_stuffing", False),
            "insight_types": out.get("insight_types", []),
        }

    # ---------- 编辑 rubric ----------
    def edit_rubric(self, updates: Dict[str, Any], account=None):
        """updates 可含: weights{dim:val}, target{key:val}, gates 覆盖。存为新 rubric 版本号。"""
        rb = copy.deepcopy(self.rubric)
        if "weights" in updates:
            for d in rb["dimensions"]:
                if d["name"] in updates["weights"]:
                    d["weight"] = float(updates["weights"][d["name"]])
            # 归一
            s = sum(d["weight"] for d in rb["dimensions"])
            if s > 0:
                for d in rb["dimensions"]:
                    d["weight"] = round(d["weight"] / s, 3)
        if "target" in updates:
            rb["target"].update({k: float(v) for k, v in updates["target"].items()})
        # rubric 版本号 +1
        old = rb.get("version", "v0")
        try:
            n = int(old.lstrip("rv")) + 1
        except ValueError:
            n = 1
        rb["version"] = "r%d" % n
        self.rubric = rb
        persist.append_event(self.id, "edit_rubric", {
            "new_version": rb["version"],
            "weights": {d["name"]: d["weight"] for d in rb["dimensions"]},
            "target": rb["target"],
        })
        r = self.evaluate(account)
        self._save()
        return r

    # ---------- 推进到下一版 ----------
    def advance(self, account=None):
        """optimizer 读当前失败 -> 提候选 -> dev gate -> 采纳成为新版本。"""
        if not self.cases:
            return {"error": "尚未导入数据"}
        cur = self._current()
        if cur["failures"] is None:
            self.evaluate(account)
            cur = self._current()

        skill = cur["skill"]
        proposal = optimizer_mod.propose(skill, cur["failures"], self.opt_history)
        if proposal is None:
            note = self._plateau_note(cur["failures"])
            persist.append_event(self.id, "converged", {
                "at_version": skill.version, "note": note})
            self._save()
            return {**self.view(account), "advance_result": {
                "status": "converged",
                "message": "优化器无更多可提议改动 => 平台期/收敛。" + note}}

        vnum = sum(1 for v in self.versions if v["adopted"])   # 下一个版本号
        cand_ver = "v%d" % vnum
        candidate = optimizer_mod.apply_proposal(skill, proposal, cand_ver)

        # dev gate(与账号无关: gate 用 judge/mock 分, 不用人工分)
        dev = [c for c in self.cases if c["split"] == "dev"] or self.cases
        human = {}
        cand_recs = runner_mod.run_split(candidate, dev, self.rubric, self.backend, cand_ver, human)
        cand_dev = runner_mod.mean_scores(cand_recs, self.rubric)

        cur_dev = cur["dev"] or runner_mod.mean_scores(
            runner_mod.run_split(skill, dev, self.rubric, self.backend, skill.version, human), self.rubric)

        target_dims = proposal["affected_dims"]
        tol = next(g["drop_tolerance"] for g in self.rubric["gates"] if g["id"] == "no_regression")
        improved = any(cand_dev.get(d, 0) - cur_dev.get(d, 0) > 0.001 for d in target_dims)
        regressed = None
        for d in self.dims:
            if d in target_dims:
                continue
            if cur_dev.get(d, 0) - cand_dev.get(d, 0) > tol:
                regressed = d
                break
        red_line_new = cand_dev.get("red_line_fails", 0) > cur_dev.get("red_line_fails", 0)
        adopt = improved and regressed is None and not red_line_new

        if adopt:
            self._add_version(candidate, adopted=True, proposal=proposal)
            self.current_idx = len(self.versions) - 1
            self.opt_history.append({"target": proposal["target"],
                                     "directive": proposal.get("directive"),
                                     "fewshot": proposal.get("fewshot"),
                                     "result": "adopted",
                                     "delta": round(cand_dev["overall"] - cur_dev["overall"], 3)})
            self.evaluate(account)
            result = {"status": "adopted", "version": cand_ver, "proposal": proposal,
                      "message": "采纳 %s: %s，dev overall %.2f -> %.2f" % (
                          cand_ver, proposal["change"], cur_dev["overall"], cand_dev["overall"])}
            persist.append_event(self.id, "version_adopted", {
                "version": cand_ver, "parent": skill.version, "proposal": proposal,
                "directives_on": [k for k, on in candidate.directives().items() if on],
                "fewshots": [f.get("kind") for f in candidate.few_shots if isinstance(f, dict)],
                "dev": cand_dev, "changelog": candidate.changelog,
            })
        else:
            reason = ("目标维度未涨" if not improved
                      else ("维度 %s 回退超容差" % self.dim_zh.get(regressed, regressed) if regressed
                            else "引入红线失败"))
            # 记录被拒版本(不推进 current)
            self._add_version(candidate, adopted=False, proposal=proposal)
            self.opt_history.append({"target": proposal["target"],
                                     "directive": proposal.get("directive"),
                                     "fewshot": proposal.get("fewshot"),
                                     "result": "rejected", "reason": reason})
            result = {"status": "rejected", "version": cand_ver, "proposal": proposal,
                      "reason": reason,
                      "message": "候选 %s 被 gate 拒绝: %s (已记入 history, 不再重试)" % (cand_ver, reason)}
            persist.append_event(self.id, "version_rejected", {
                "version": cand_ver, "parent": skill.version, "proposal": proposal,
                "reason": reason, "dev": cand_dev,
            })
        self._save()
        v = self.view(account)
        v["advance_result"] = result
        return v

    def _plateau_note(self, failures):
        if not failures:
            return " 失败模式已零散, 当前结构够用。"
        top = failures[0]
        if top.get("directive_hint") is None:
            return " 首要失败'%s'无指令级修法 => 触发结构优化(Phase3)信号, 需人工回改 v0 结构。" % top["pattern"]
        return " 剩余失败仍是指令/内容级, 未到动结构的时候。"
