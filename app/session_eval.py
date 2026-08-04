# -*- coding: utf-8 -*-
"""
session_eval.py — 评估 / 记录叠加 / 失败聚类 / rubric 编辑 / 推进 (SessionEval mixin)

由 session.py 组合进 class Session。本文件负责「跑分与推进」:
  import_data   —— 导入数据集(校验最小字段), 触发首次评估
  evaluate      —— 用当前版本 skill 跑分, 叠加真实产物, 聚类失败, 算 dev/test 均分
  _apply_recorded —— 把平台真实报告/评分覆盖 mock(有则真实, 无则占位)
  _rec_view / _output_summary —— 组装单条 case 的可呈现视图
  edit_rubric   —— 改权重/阈值, 存为新 rubric 版本并重评
  advance       —— optimizer 提候选 -> dev gate -> 采纳/拒绝

依赖 SessionCore 的 _current/view/_save 等。
"""
import copy
import sys
import os
from typing import Any, Dict, List

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
import optimizer_registry                            # noqa: E402
import optimizer_pipeline                            # noqa: E402


class SessionEval:
    """评估与推进 mixin。"""

    # ---------- 数据导入 ----------
    def import_data(self, rows: List[Dict[str, Any]], account=None):
        # 同一份 openharness-wb/v1 JSON 同时服务 WB 生成和平台评测。
        # 旧数组/JSONL 在迁移期仍可读取，但不再静默跳过坏数据。
        normalized = openharness_rows(rows)
        is_research = self.rubric.get("product") == "research_insight"
        clean = []
        for source in normalized:
            r = copy.deepcopy(source)
            if not is_research and "human_report_findings" not in r:
                raise ValueError(
                    "算数字型 case %s 缺少 human_report_findings"
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
        persist.append_event(self.id, "import_data", {
            "n_cases": len(clean), "splits": self._split_counts(),
        })
        # 重新评估当前版本
        r = self.evaluate(account)
        self._save()
        return r

    # ---------- 评估当前版本 ----------
    def evaluate(self, account=None):
        """计算模型 Judge/mock 分、失败聚类和 dev/test 均分，并暂存基础记录。"""
        cur = self._current()
        eval_cases = self._cases_for(cur)
        if not eval_cases:
            return {"error": "尚未导入数据"}
        skill = cur["skill"]
        ver = cur["version"]
        if cur.get("workflow_block"):
            cur["workflow_block"] = None

        train_dev = [c for c in eval_cases if c["split"] in ("train", "dev")]
        dev = [c for c in eval_cases if c["split"] == "dev"]
        test = [c for c in eval_cases if c["split"] == "test"]

        recs_all = runner_mod.run_split(
            skill,
            eval_cases,
            self.rubric,
            self.backend,
            ver,
        )
        # 用平台真实报告 + LLM-judge 评分覆盖 mock(有则真实, 无则保留 mock 作占位)
        self._apply_recorded(recs_all, ver)
        dev_recs = [r for r in recs_all if r.dataset_split == "dev"]
        test_recs = [r for r in recs_all if r.dataset_split == "test"]

        cur["dev"] = runner_mod.mean_scores(dev_recs, self.rubric) if dev else runner_mod.mean_scores(recs_all, self.rubric)
        cur["test"] = runner_mod.mean_scores(test_recs, self.rubric) if test else None
        eligible_recs = (
            [r for r in recs_all if r.dataset_split in ("train", "dev")]
            or recs_all
        )
        if self.rubric.get("product") == "research_insight":
            try:
                failure_report = clustering_mod.analyze_real_judgments(
                    self.judge_checks.get(ver, {}),
                    self.rubric,
                )
                cur["failure_mapping_error"] = []
            except clustering_mod.FailureMappingError as exc:
                failure_report = []
                cur["failure_mapping_error"] = exc.check_ids
            cur["failure_report"] = failure_report
            cur["failures"] = failure_report
        else:
            cur["failures"] = clustering_mod.analyze_mock_records(
                eligible_recs,
                product=self.rubric.get("product"),
            )
            cur["failure_report"] = cur["failures"]
            cur["failure_mapping_error"] = []
        cur["_recs"] = recs_all

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
        无真实评分的 case 保留 mock 分(占位/自测), 由 score_source 区分。"""
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
                r.judge_checks = dict(jc)
                r.scores = judge_mod.dim_from_checks(jc, self.rubric)
                r.judge_reasoning = (jchecks.get(r.case_id) or {}).get("reasoning", {})
                r.score_source = "recorded"
            elif jv is not None:
                r.scores = {
                    d: round(float(s), 3)
                    for d, s in jv["scores"].items()
                    if d in self.dims
                }
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

    def _rec_view(self, r: EvalRecord) -> Dict[str, Any]:
        ver = self._current()["version"]
        real_report = self.report_outputs.get(ver, {}).get(r.case_id)
        jc = (self.judge_checks.get(ver, {}).get(r.case_id) or {}).get("checks", {})
        jr = (self.judge_checks.get(ver, {}).get(r.case_id) or {}).get("reasoning", {})
        return {
            "case_id": r.case_id, "split": r.dataset_split,
            "scores": r.scores, "judge_reasoning": r.judge_reasoning,
            "flagged": r.flagged, "red_line": r.case_failed_gate,
            "output_summary": self._output_summary(r.output),
            "report_text": real_report,          # None 表示该 case 尚未导入真实报告
            "score_source": getattr(r, "score_source", "mock"),  # recorded=平台LLM-judge真实分 / mock=占位
            "check_judge": jc,                    # {check_id: 1/0.5/0} Opus judge
            "check_judge_reason": jr,
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
        clustering_mod.validate_optimizer_mappings(self.rubric)
        current_version = self._current()["version"]
        self._invalidate_judge_checks(
            current_version,
            list(self.judge_checks.get(current_version, {})),
            "rubric_changed",
        )
        persist.append_event(self.id, "edit_rubric", {
            "new_version": rb["version"],
            "weights": {d["name"]: d["weight"] for d in rb["dimensions"]},
            "target": rb["target"],
        })
        r = self.evaluate(account)
        self._save()
        return r

    # ---------- 推进到下一版 ----------
    def advance(
        self,
        account=None,
        llm_backend="workbuddy",
        llm_model=None,
    ):
        """按 optimizer_mode 派发。switch_search 走原路径(逐字不变);
        llm_rewrite 走 LLM 改写 + 异步 gate。"""
        if not self.cases:
            return {"error": "尚未导入数据"}
        if getattr(self, "optimizer_mode", "switch_search") == "llm_rewrite":
            # 重启兜底:上一轮 pending 候选若已判分完但未结算,先结算
            if getattr(self, "pending_idx", None) is not None:
                self.settle_pending_candidate(account)
            return self._advance_llm_rewrite(
                account,
                llm_backend=llm_backend,
                llm_model=llm_model,
            )
        return self._advance_switch_search(account)

    def _advance_llm_rewrite(
        self,
        account=None,
        llm_backend="workbuddy",
        llm_model=None,
    ):
        """LLM 自由改写整段 instructions -> 候选态(不动 current_idx)-> 待真实判分后 settle。"""
        cur = self._current()
        if cur["failures"] is None:
            self.evaluate(account)
            cur = self._current()
        stop = self._mark_optimizer_stopped_if_needed()
        if stop["stopped"]:
            state = self.view(account)
            state["advance_result"] = {
                "status": "converged",
                "code": stop["code"],
                "message": "优化 loop 已停止：" + stop["reason"] + "。",
                "optimizer_stop": stop,
            }
            return state
        skill = cur["skill"]
        state = self.view(account)
        advance_action = state["actions"]["advance"]
        if not advance_action["enabled"]:
            state["advance_result"] = {
                "status": "blocked",
                "code": "workflow_not_ready",
                "message": advance_action["reason"] or "当前版本不可推进(需先生成并判分)",
            }
            return state

        failures = (cur.get("failure_report") or cur.get("failures")) or []
        strategy = optimizer_registry.get_strategy("llm_rewrite")
        context = optimizer_pipeline.build_optimizer_context(self, account)
        proposal = strategy.propose(
            self,
            skill,
            failures,
            context,
            llm_backend=llm_backend,
            llm_model=llm_model,
        )
        if proposal is None:
            # propose 内部(红线守卫/无产出)已把拒绝原因写进 opt_history
            note = (self.opt_history[-1].get("reason")
                    if self.opt_history else None) or "LLM 未产出可用改写"
            self._save()
            v = self.view(account)
            v["advance_result"] = {
                "status": "blocked",
                "code": "llm_rewrite_no_change",
                "message": note,
            }
            return v

        version_nums = []
        for item in self.versions:
            try:
                version_nums.append(int(str(item["version"]).lstrip("v")))
            except (TypeError, ValueError):
                continue
        cand_ver = "v%d" % (max(version_nums, default=0) + 1)
        candidate = strategy.apply_proposal(skill, proposal, cand_ver)
        self._add_version(candidate, adopted=False, proposal=proposal)
        self.versions[-1]["candidate_state"] = "pending"
        self.pending_idx = len(self.versions) - 1   # 关键:不动 current_idx(防回退)
        self.opt_history.append({
            "target": "instructions_freeform",
            "parent": skill.version,
            "candidate": cand_ver,
            "change_summary": proposal.get("change_summary", ""),
            "targets": proposal.get("targets_failures", []),
            "result": "pending_real_evaluation",
            "llm_backend": llm_backend,
            "model": llm_model,
        })
        persist.append_event(self.id, "version_proposed", {
            "version": cand_ver,
            "parent": skill.version,
            "strategy": "llm_rewrite",
            "proposal": {k: proposal.get(k) for k in
                         ("change_summary", "targets_failures", "preserved",
                          "hypothesis", "self_check_no_hack")},
            "validation": "pending_real_evaluation",
            "llm_backend": llm_backend,
            "model": llm_model,
        })
        self._save()
        v = self.view(account)
        v["advance_result"] = {
            "status": "proposed",
            "version": cand_ver,
            "proposal": proposal,
            "requires_real_evaluation": True,
            "message": ("已生成待验证候选 %s(LLM 改写)。请对该候选执行 WB 生成 + 批量真实 Judge,"
                        "判分完成后平台自动结算采纳/回滚。" % cand_ver),
        }
        return v

    # ---------- 推进到下一版(switch_search 原路径,逐字不变) ----------
    def _advance_switch_search(self, account=None):
        """optimizer 读当前失败 -> 提候选 -> dev gate -> 采纳成为新版本。"""
        if not self.cases:
            return {"error": "尚未导入数据"}
        cur = self._current()
        if cur["failures"] is None:
            self.evaluate(account)
            cur = self._current()

        skill = cur["skill"]
        state = self.view(account)
        advance_action = state["actions"]["advance"]
        if not advance_action["enabled"]:
            state["advance_result"] = {
                "status": "blocked",
                "code": "workflow_not_ready",
                "message": advance_action["reason"] or "当前版本不可推进",
            }
            return state

        failures = (
            cur.get("failure_report")
            if self.rubric.get("product") == "research_insight"
            else cur["failures"]
        ) or []
        proposal = optimizer_mod.propose(
            skill,
            failures,
            self.opt_history,
        )
        if proposal is None:
            if failures:
                note = (
                    "真实 Judge 仍有失败项，但 Optimizer 没有可应用的新改动；"
                    "请检查 directive 状态或扩展优化动作。"
                )
                cur["workflow_block"] = note
                persist.append_event(
                    self.id,
                    "optimizer_blocked",
                    {
                        "at_version": skill.version,
                        "failure_patterns": [
                            item.get("pattern_id")
                            for item in failures
                        ],
                        "note": note,
                    },
                )
                self._save()
                state = self.view(account)
                state["advance_result"] = {
                    "status": "blocked",
                    "code": "optimizer_no_applicable_change",
                    "message": note,
                }
                return state
            note = self._plateau_note(failures)
            persist.append_event(self.id, "converged", {
                "at_version": skill.version, "note": note})
            self._save()
            return {**self.view(account), "advance_result": {
                "status": "converged",
                "message": "优化器无更多可提议改动 => 平台期/收敛。" + note}}

        version_nums = []
        for item in self.versions:
            try:
                version_nums.append(
                    int(str(item["version"]).lstrip("v"))
                )
            except (TypeError, ValueError):
                continue
        vnum = max(version_nums, default=0) + 1
        cand_ver = "v%d" % vnum
        candidate = optimizer_mod.apply_proposal(skill, proposal, cand_ver)

        if self.rubric.get("product") == "research_insight":
            self._add_version(candidate, adopted=True, proposal=proposal)
            self.current_idx = len(self.versions) - 1
            self.opt_history.append({
                "target": proposal["target"],
                "directive": proposal.get("directive"),
                "fewshot": proposal.get("fewshot"),
                "result": "pending_real_evaluation",
            })
            self.evaluate(account)
            result = {
                "status": "proposed",
                "version": cand_ver,
                "proposal": proposal,
                "requires_real_evaluation": True,
                "message": (
                    "已生成待验证候选 %s: %s。"
                    "请执行 WB CLI + 批量真实 Judge。"
                )
                % (cand_ver, proposal["change"]),
            }
            persist.append_event(
                self.id,
                "version_proposed",
                {
                    "version": cand_ver,
                    "parent": skill.version,
                    "proposal": proposal,
                    "validation": "pending_real_evaluation",
                    "directives_on": [
                        key
                        for key, enabled in candidate.directives().items()
                        if enabled
                    ],
                },
            )
            self._save()
            view = self.view(account)
            view["advance_result"] = result
            return view

        # dev gate 使用模型 Judge/mock 分。
        dev = [c for c in self.cases if c["split"] == "dev"] or self.cases
        cand_recs = runner_mod.run_split(candidate, dev, self.rubric, self.backend, cand_ver)
        cand_dev = runner_mod.mean_scores(cand_recs, self.rubric)

        cur_dev = cur["dev"] or runner_mod.mean_scores(
            runner_mod.run_split(skill, dev, self.rubric, self.backend, skill.version), self.rubric)

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
            return " 当前版本全部 Judge check 均已满足。"
        top = failures[0]
        if top.get("directive_hint") is None:
            return " 首要失败'%s'无指令级修法 => 触发结构优化(Phase3)信号, 需人工回改 v0 结构。" % top["pattern"]
        return " 剩余失败仍是指令/内容级, 未到动结构的时候。"
