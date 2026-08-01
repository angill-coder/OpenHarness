# -*- coding: utf-8 -*-
"""
session_core.py — 会话状态 / 快照 / 恢复 / 版本管理 (SessionCore mixin)

Session 已按职责拆成三个 mixin, 由 session.py 组合成 class Session(SessionCore, SessionEval, SessionLabel)。
本文件负责「状态骨架」:
  __init__          —— 从需求生成 v0 skill+rubric, 建立会话
  to_snapshot/_save —— 序列化 durable 输入, 落盘
  restore           —— 从快照重建并重算派生量
  view              —— 汇总当前会话状态给页面(纯模型 Judge 模式)
  版本管理           —— _add_version / _current

本文件是模块级常量与 helper(_dims_from_rubric)的所有者。
"""
import sys
import os
from typing import Any, Dict, List

HARNESS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness")
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

from schemas import SkillArtifact, EvalRecord   # noqa: E402,F401
import backend as backend_mod                    # noqa: E402
import clustering as clustering_mod              # noqa: E402

import generator as generator_mod                 # noqa: E402
import persistence as persist                      # noqa: E402


DIMS = ["data_accuracy", "completeness", "insight", "conciseness"]
DIM_ZH = {"data_accuracy": "数据准确性", "completeness": "完整性",
          "insight": "洞察质量", "conciseness": "简洁性"}


def _dims_from_rubric(rubric):
    """从 rubric 取维度名与中文名(产品无关)。"""
    dims = [d["name"] for d in rubric["dimensions"]]
    zh = {d["name"]: d.get("name_zh", d["name"]) for d in rubric["dimensions"]}
    return dims, zh


def _normalize_optimizer_stop(value=None):
    """校验会话级 early-stop；它独立于 rubric.target，不改评分标准。"""
    value = value or {}
    target = value.get("overall_target")
    patience = value.get("max_no_improvement")
    if target in ("", None):
        target = None
    else:
        target = float(target)
        if not 1.0 <= target <= 5.0:
            raise ValueError("停止条件 overall_target 必须在 1.0–5.0 之间")
    if patience in ("", None):
        patience = None
    else:
        if isinstance(patience, bool):
            raise ValueError("max_no_improvement 必须是正整数")
        patience = int(patience)
        if patience < 1:
            raise ValueError("max_no_improvement 必须是正整数")
    return {
        "overall_target": target,
        "max_no_improvement": patience,
    }


def _new_optimization_progress():
    return {
        "best_overall": None,
        "no_improvement_streak": 0,
        "evaluated_candidates": 0,
        "last_candidate": None,
        "last_candidate_overall": None,
        "last_outcome": None,
        "stopped": False,
        "reason": None,
    }


class SessionCore:
    """状态骨架 mixin。方法体依赖的 evaluate/_rec_view 等由其它 mixin 提供。"""

    def __init__(
        self,
        sid: str,
        requirement: str,
        product_id: str,
        prefer_real=False,
        _restoring=False,
        optimizer_mode="switch_search",
        optimizer_stop=None,
        v0_strategy="base_skill",
    ):
        self.id = sid
        self.requirement = requirement
        self.product_id = product_id
        self.optimizer_mode = optimizer_mode or "switch_search"
        self.v0_strategy = v0_strategy or "base_skill"
        self.optimizer_stop = _normalize_optimizer_stop(optimizer_stop)
        self.optimization_progress = _new_optimization_progress()
        self.pending_idx = None       # llm_rewrite 的评测游标(候选态);None 时 = _current()
        self._persist = True          # 落盘开关(恢复时先关, 重建完再开)

        gen = generator_mod.generate_v0(
            requirement,
            product_id,
            prefer_real=prefer_real,
            optimizer_mode=self.optimizer_mode,
            v0_strategy=self.v0_strategy,
        )
        self.rubric = gen["rubric"]
        generator_mod.hydrate_research_optimizer_metadata(self.rubric)
        clustering_mod.validate_optimizer_mappings(self.rubric)
        self.gen_rationale = gen["rationale"]
        self.detected = gen["detected"]
        self.dims, self.dim_zh = _dims_from_rubric(self.rubric)
        # 后端按 rubric 的 product 选择(research_insight -> ResearchMockBackend)
        self.backend = backend_mod.get_backend(product_id=self.rubric.get("product"))

        v0 = SkillArtifact.from_dict(gen["skill"])
        # versions: 有序列表, 每项 {skill, dev, test, adopted, proposal, eval(每case结果)}
        self.versions: List[Dict[str, Any]] = []
        self.current_idx = 0
        self.opt_history: List[Dict[str, Any]] = []   # optimizer 记忆
        self.failure_history: List[List[Dict]] = []

        self.cases: List[Dict[str, Any]] = []
        self.data_source: Dict[str, Any] = {"kind": "none"}
        # 数据集分组: {ds_id: {"id","name","case_ids":[...],"created_at"}}。
        # 一份 session 可并存多个数据集分组(同一份 data.json 里的不同 case 子集);
        # 每个版本通过 version_entry["dataset_id"] 绑定到其中一个分组来评测/生成/判分。
        self.datasets: Dict[str, Dict[str, Any]] = {}
        self.active_dataset_id: Optional[str] = None
        self.report_outputs: Dict[str, Dict[str, str]] = {}  # {version: {case_id: report_text}} 平台真实报告
        self.report_judgments: Dict[str, Dict[str, Dict]] = {}  # {version: {case_id: {scores,reasoning,flagged}}} 平台LLM-judge
        self.judge_checks: Dict[str, Dict[str, Dict]] = {}   # {version:{case:{checks,reasoning}}} Opus逐check
        self.generation_imports: Dict[str, Dict[str, str]] = {}

        self._add_version(v0, adopted=True, proposal=None)

        if not _restoring:
            # 新建会话: 写 meta + created 事件 + 首个快照
            persist.init_session(sid, requirement, product_id)
            persist.append_event(sid, "created", {
                "product_id": product_id, "detected": self.detected,
                "optimizer_mode": self.optimizer_mode,
                "v0_strategy": self.v0_strategy,
                "rubric_version": self.rubric["version"],
                "v0_skill": v0.to_dict(), "rubric": self.rubric,
            })
            self._save()

    # ---------- 版本管理 ----------
    def _add_version(self, skill, adopted, proposal, dataset_id=None):
        self.versions.append({
            "skill": skill, "version": skill.version, "parent": skill.parent_version,
            "changelog": skill.changelog, "adopted": adopted, "proposal": proposal,
            "dev": None, "test": None, "eval": None, "failures": None,
            "failure_report": None, "failure_mapping_error": [],
            "workflow_block": None,
            # 该版本评测/生成/判分所用的数据集分组;None 时回退到 active/全集(向后兼容)。
            "dataset_id": dataset_id if dataset_id is not None else self.active_dataset_id,
            # llm_rewrite 异步 gate 用:pending -> adopted/rejected
            "candidate_state": None, "verdict": None, "verdict_reasons": None,
        })

    def _current(self):
        return self.versions[self.current_idx]

    # ---------- 数据集分组 ----------
    def _new_dataset_id(self) -> str:
        import time as _t
        n = len(self.datasets) + 1
        return "ds%d-%s" % (n, _t.strftime("%H%M%S"))

    def add_dataset(self, case_ids, name=None) -> str:
        """登记一份数据集分组(引用 self.cases 里的一批 case_id), 返回 dataset_id。"""
        import time as _t
        ds_id = self._new_dataset_id()
        ordered = list(dict.fromkeys(str(c) for c in case_ids))
        self.datasets[ds_id] = {
            "id": ds_id,
            "name": name or ("数据集 %d" % len(self.datasets),),
            "case_ids": ordered,
            "created_at": _t.time(),
        }
        # name 上面误写成 tuple 的兜底(保持字符串)
        if isinstance(self.datasets[ds_id]["name"], tuple):
            self.datasets[ds_id]["name"] = self.datasets[ds_id]["name"][0]
        return ds_id

    def _dataset_case_ids(self, dataset_id):
        """某数据集分组的 case_id 集合;分组不存在则 None(表示回退全集)。"""
        if not dataset_id or dataset_id not in self.datasets:
            return None
        return set(self.datasets[dataset_id]["case_ids"])

    def _case_ids_for(self, version_entry):
        """某版本参与评测/判分的 case_id 集合。未绑定分组则回退到 self.cases 全集。"""
        ids = self._dataset_case_ids((version_entry or {}).get("dataset_id"))
        all_ids = {str(c["case_id"]) for c in self.cases}
        if ids is None:
            return all_ids
        # 只保留仍存在于 self.cases 里的(防止陈旧引用)
        return {cid for cid in all_ids if cid in ids}

    def _cases_for(self, version_entry):
        """某版本参与评测的 case 列表(按其绑定分组过滤 self.cases)。"""
        ids = self._case_ids_for(version_entry)
        return [c for c in self.cases if str(c["case_id"]) in ids]

    def _eval_target(self):
        """评测/生成/判分面向的版本:有 pending 候选时指向候选,否则 = 当前最优。

        switch_search 从不设置 pending_idx,故恒等于 _current(),行为零变化。
        """
        if getattr(self, "pending_idx", None) is not None:
            return self.versions[self.pending_idx]
        return self._current()

    def settle_pending_candidate(self, account=None):
        """llm_rewrite 异步 gate 结算:候选真实判分完 -> 对比当前最优 -> 采纳或回滚。

        判分未全完则不结算(幂等,返回 None)。采纳则 current_idx 移到候选;
        回滚则 current_idx 留在 parent(候选标 rejected)。两种结果都写 opt_history。
        """
        import optimizer_pipeline
        pidx = getattr(self, "pending_idx", None)
        if pidx is None:
            return None
        cand = self.versions[pidx]
        cand_ver = cand["version"]
        if not self._version_real_judge_complete(cand_ver):
            return None  # 判分未全完 -> 不结算

        saved = self.current_idx
        # 父版 = 候选分叉自的当前最优(按版本号定位;兜底用 saved)
        parent_idx = saved
        for i, v in enumerate(self.versions):
            if v["version"] == cand.get("parent"):
                parent_idx = i
                break
        # 算候选真实分
        self.current_idx = pidx
        self.evaluate(account)
        cand_dims = cand.get("dev") or {}
        # 算/取父版(当前最优)真实分
        self.current_idx = parent_idx
        self.evaluate(account)
        parent = self.versions[parent_idx]
        parent_dims = parent.get("dev") or {}

        target_dims = (cand.get("proposal") or {}).get("affected_dims") or []
        tol = optimizer_pipeline.no_regression_tol(self.rubric)
        adopt, verdict, reasons = optimizer_pipeline.evaluate_gate(
            parent_dims, cand_dims, target_dims, tol, list(self.dims)
        )
        cand["verdict"] = verdict
        cand["verdict_reasons"] = reasons
        if adopt:
            cand["adopted"] = True
            cand["candidate_state"] = "adopted"
            self.current_idx = pidx
        else:
            cand["candidate_state"] = "rejected"
            self.current_idx = parent_idx   # 天然回滚:指针留在最优

        # 回写 opt_history 中该候选那条 pending 记录
        for h in reversed(self.opt_history):
            if h.get("candidate") == cand_ver and h.get("result") == "pending_real_evaluation":
                h["result"] = verdict
                h["reasons"] = reasons
                break

        self.pending_idx = None
        persist.append_event(self.id, "candidate_settled", {
            "candidate": cand_ver,
            "parent": parent["version"],
            "verdict": verdict,
            "reasons": reasons,
            "cand_dev": cand_dims,
            "parent_dev": parent_dims,
            "optimizer_stop": self._record_optimizer_outcome(
                cand,
                parent,
                adopt,
            ),
        })
        self._save()
        return {
            "candidate": cand_ver,
            "verdict": verdict,
            "reasons": reasons,
            "optimizer_stop": self._optimizer_stop_state(),
        }

    def _optimizer_stop_state(self):
        """返回 LLM 优化 loop 的会话级停止状态（不修改 rubric）。"""
        config = getattr(
            self,
            "optimizer_stop",
            _normalize_optimizer_stop(),
        )
        is_llm_rewrite = (
            getattr(self, "optimizer_mode", "switch_search")
            == "llm_rewrite"
        )
        progress = getattr(
            self,
            "optimization_progress",
            _new_optimization_progress(),
        )
        current_version = self._current().get("version")
        scores_are_real = (
            self.rubric.get("product") != "research_insight"
            or self._version_real_judge_complete(current_version)
        )
        cur_dev = (self._current().get("dev") or {})
        current_overall = (
            cur_dev.get("overall") if scores_are_real else None
        )
        best_overall = progress.get("best_overall")
        if best_overall is None and current_overall is not None:
            best_overall = current_overall

        target = config.get("overall_target")
        patience = config.get("max_no_improvement")
        streak = int(progress.get("no_improvement_streak") or 0)
        reached_target = (
            is_llm_rewrite
            and target is not None
            and current_overall is not None
            and current_overall >= target - 0.001
        )
        plateau = (
            is_llm_rewrite
            and patience is not None
            and streak >= patience
        )
        stopped = bool(reached_target or plateau)
        if reached_target:
            reason = (
                "当前最佳已采纳版 overall %.2f ≥ 停止目标 %.2f"
                % (current_overall, target)
            )
            code = "overall_target_reached"
        elif plateau:
            reason = (
                "连续 %d 个候选版本未提升已采纳最佳 overall"
                % patience
            )
            code = "no_improvement_patience_reached"
        else:
            reason = None
            code = None
        return {
            "enabled": is_llm_rewrite and (
                target is not None or patience is not None
            ),
            "stopped": stopped,
            "code": code,
            "reason": reason,
            "overall_target": target,
            "max_no_improvement": patience,
            "current_overall": current_overall,
            "best_overall": best_overall,
            "no_improvement_streak": streak,
            "evaluated_candidates": int(
                progress.get("evaluated_candidates") or 0
            ),
        }

    def _record_optimizer_outcome(self, candidate, parent, adopted):
        """候选结算后更新 patience：只有已采纳版 overall 创新高才算提升。"""
        progress = self.optimization_progress
        parent_overall = (parent.get("dev") or {}).get("overall")
        candidate_overall = (candidate.get("dev") or {}).get("overall")
        best = progress.get("best_overall")
        if best is None:
            best = parent_overall
        improved = (
            bool(adopted)
            and candidate_overall is not None
            and (best is None or candidate_overall - best > 0.001)
        )
        if improved:
            best = candidate_overall
            progress["no_improvement_streak"] = 0
        else:
            progress["no_improvement_streak"] = (
                int(progress.get("no_improvement_streak") or 0) + 1
            )
        progress["best_overall"] = best
        progress["evaluated_candidates"] = (
            int(progress.get("evaluated_candidates") or 0) + 1
        )
        progress["last_candidate"] = candidate.get("version")
        progress["last_candidate_overall"] = candidate_overall
        progress["last_outcome"] = (
            "overall_improved" if improved else "overall_not_improved"
        )

        state = self._optimizer_stop_state()
        newly_stopped = state["stopped"] and not progress.get("stopped")
        progress["stopped"] = state["stopped"]
        progress["reason"] = state["reason"]
        if newly_stopped:
            persist.append_event(self.id, "optimization_stopped", state)
        return state

    def _mark_optimizer_stopped_if_needed(self):
        """在生成候选前同步 target 型停止条件，并只记录一次事件。"""
        state = self._optimizer_stop_state()
        progress = self.optimization_progress
        newly_stopped = state["stopped"] and not progress.get("stopped")
        progress["stopped"] = state["stopped"]
        progress["reason"] = state["reason"]
        if newly_stopped:
            persist.append_event(self.id, "optimization_stopped", state)
            self._save()
        return state

    # ---------- 落盘 / 恢复 ----------
    def _save(self):
        """写最新快照(state.json)。派生量(dev/eval/failures)不入快照, 恢复后重算。"""
        if not getattr(self, "_persist", True):
            return
        persist.save_snapshot(self.id, self.to_snapshot())

    def to_snapshot(self) -> Dict[str, Any]:
        """可序列化的持久态: 只存 durable 输入, 不存可重算的派生量。"""
        return {
            "id": self.id, "requirement": self.requirement, "product_id": self.product_id,
            "optimizer_mode": getattr(self, "optimizer_mode", "switch_search"),
            "v0_strategy": getattr(self, "v0_strategy", "base_skill"),
            "optimizer_stop": getattr(
                self,
                "optimizer_stop",
                _normalize_optimizer_stop(),
            ),
            "optimization_progress": getattr(
                self,
                "optimization_progress",
                _new_optimization_progress(),
            ),
            "pending_idx": getattr(self, "pending_idx", None),
            "rubric": self.rubric, "gen_rationale": self.gen_rationale, "detected": self.detected,
            "current_idx": self.current_idx,
            "opt_history": self.opt_history,
            "cases": self.cases,
            "data_source": getattr(self, "data_source", {"kind": "none"}),
            "generation_imports": self.generation_imports,
            "versions": [{
                "skill": v["skill"].to_dict(),
                "adopted": v["adopted"],
                "proposal": v["proposal"],
                "failure_report": v.get("failure_report"),
                "failure_mapping_error": v.get(
                    "failure_mapping_error",
                    [],
                ),
                "workflow_block": v.get("workflow_block"),
                "candidate_state": v.get("candidate_state"),
                "verdict": v.get("verdict"),
                "verdict_reasons": v.get("verdict_reasons"),
            } for v in self.versions],
        }

    @classmethod
    def restore(cls, snap: Dict[str, Any], prefer_real=False) -> "SessionCore":
        """从快照重建 Session, 并重算派生量。"""
        self = cls.__new__(cls)
        self.id = snap["id"]
        self.requirement = snap["requirement"]
        self.product_id = snap["product_id"]
        self.optimizer_mode = snap.get("optimizer_mode", "switch_search")
        self.v0_strategy = snap.get("v0_strategy", "base_skill")
        self.optimizer_stop = _normalize_optimizer_stop(
            snap.get("optimizer_stop")
        )
        self.optimization_progress = _new_optimization_progress()
        self.optimization_progress.update(
            snap.get("optimization_progress") or {}
        )
        self.pending_idx = snap.get("pending_idx")
        self._persist = False          # 重建期间不写盘
        self.rubric = generator_mod.hydrate_research_optimizer_metadata(
            snap["rubric"]
        )
        clustering_mod.validate_optimizer_mappings(self.rubric)
        self.gen_rationale = snap.get("gen_rationale", "")
        self.detected = snap.get("detected", {})
        self.dims, self.dim_zh = _dims_from_rubric(self.rubric)
        self.backend = backend_mod.get_backend(product_id=self.rubric.get("product"))
        self.opt_history = snap.get("opt_history", [])
        self.cases = snap.get("cases", [])
        self.data_source = snap.get("data_source") or {"kind": "legacy"}
        self.report_outputs = persist.load_outputs(self.id)   # 真实报告文本由 outputs.jsonl 恢复
        self.report_judgments = persist.load_judgments(self.id)  # 真实报告的 LLM-judge 评分由 judgments.jsonl 恢复
        self.judge_checks = persist.load_check_judgments(self.id)  # 逐check judge 由 check_judgments.jsonl 恢复
        self.generation_imports = snap.get("generation_imports", {})
        # __init__ 设了但 restore 早期漏了这两个 -> 恢复后 advance 调 _add_version
        # 读 self.active_dataset_id 会 AttributeError。二者默认态与新建会话一致。
        self.datasets = snap.get("datasets", {})
        self.active_dataset_id = snap.get("active_dataset_id")
        self.failure_history = []
        self.versions = []
        for vd in snap["versions"]:
            self.versions.append({
                "skill": SkillArtifact.from_dict(vd["skill"]),
                "version": vd["skill"]["version"], "parent": vd["skill"].get("parent_version"),
                "changelog": vd["skill"].get("changelog", ""),
                "adopted": vd["adopted"], "proposal": vd["proposal"],
                "dev": None, "test": None, "eval": None, "failures": None,
                "failure_report": vd.get("failure_report"),
                "failure_mapping_error": vd.get(
                    "failure_mapping_error",
                    [],
                ),
                "workflow_block": vd.get("workflow_block"),
                "candidate_state": vd.get("candidate_state"),
                "verdict": vd.get("verdict"),
                "verdict_reasons": vd.get("verdict_reasons"),
            })
        self.current_idx = snap.get("current_idx", 0)
        if self.cases:                 # 有数据则重算曲线所需版本的派生量
            saved_idx = self.current_idx
            for i, v in enumerate(self.versions):
                judged_rejected = (
                    v.get("candidate_state") == "rejected"
                    and self._version_real_judge_complete(v["version"])
                )
                if not v["adopted"] and not judged_rejected:
                    continue
                self.current_idx = i
                try:
                    self.evaluate()
                except Exception:
                    pass
            self.current_idx = saved_idx
        self._persist = True
        return self

    # ---------- 视图 ----------
    def view(self, account=None):
        adopted = [v for v in self.versions if v["adopted"]]
        cur = self._eval_target()
        recs = cur.get("_recs") or []
        current_eval = [self._rec_view(r) for r in recs]
        version = cur["version"]
        case_ids = self._case_ids_for(cur)
        reports = self.report_outputs.get(version, {})
        check_judgments = self.judge_checks.get(version, {})
        direct_judgments = self.report_judgments.get(version, {})
        reports_ready = {
            case_id for case_id in case_ids if (reports.get(case_id) or "").strip()
        }
        judged = {
            case_id
            for case_id in case_ids
            if (check_judgments.get(case_id) or {}).get("checks")
            or case_id in direct_judgments
        }
        requires_model_judge = self.rubric.get("product") == "research_insight"
        curve_versions = (
            [
                item
                for item in self.versions
                if item["adopted"]
                or item.get("candidate_state") == "rejected"
            ]
            if requires_model_judge
            else adopted
        )
        judge_complete = bool(case_ids) and judged == case_ids
        judge_progress = {
            "required": requires_model_judge,
            "complete": judge_complete,
            "total_cases": len(case_ids),
            "reports_ready": len(reports_ready),
            "judged_cases": len(judged),
            "missing_report_case_ids": sorted(case_ids - reports_ready),
            "pending_judge_case_ids": sorted(case_ids - judged),
        }
        version_status, actions = self._workflow_state(
            cur,
            case_ids,
            reports_ready,
            judged,
            requires_model_judge,
        )
        optimizer_stop = self._optimizer_stop_state()
        if optimizer_stop["stopped"]:
            version_status = "converged"
            actions["advance"] = {
                "enabled": False,
                "reason": optimizer_stop["reason"],
            }
        source = getattr(self, "data_source", {}) or {}
        dataset_path = source.get("dataset_path")
        quality_available = bool(
            self.cases
            and source.get("kind") in {"configured", "uploaded"}
            and dataset_path
            and os.path.isfile(dataset_path)
        )
        if quality_available:
            quality_reason = None
        elif not self.cases:
            quality_reason = "请先导入数据"
        elif source.get("kind") not in {"configured", "uploaded"}:
            quality_reason = (
                "当前数据没有可解析的本地素材路径；"
                "请上传 source 项目文件夹或 ZIP"
            )
        else:
            quality_reason = "原始数据集路径不存在"
        return {
            "session_id": self.id,
            "requirement": self.requirement,
            "product_id": self.product_id,
            "account": account,
            "backend": self.backend.name,
            "detected": self.detected,
            "gen_rationale": self.gen_rationale,
            "n_cases": len(self.cases),
            "splits": self._split_counts(),
            "data_quality": {
                "available": quality_available,
                "reason": quality_reason,
                "source_kind": source.get("kind"),
                "dataset_path": dataset_path,
            },
            "current_version": cur["version"],
            "rubric": self.rubric,
            "versions": [self._version_view(v) for v in self.versions],
            "curve": [
                {
                    "version": v["version"],
                    "dev": v["dev"],
                    "test": v["test"],
                    "adopted": v["adopted"],
                    "candidate_state": v.get("candidate_state"),
                    "verdict": v.get("verdict"),
                    "verdict_reasons": v.get("verdict_reasons"),
                }
                for v in curve_versions
                if v["dev"] is not None
                and (
                    not requires_model_judge
                    or self._version_real_judge_complete(v["version"])
                )
            ],
            "current_eval": current_eval,
            "current_failures": (
                cur.get("failure_report")
                if requires_model_judge
                else cur["failures"]
            ),
            "failure_report": cur.get("failure_report"),
            "failure_mapping_error": cur.get(
                "failure_mapping_error",
                [],
            ),
            "evaluation_mode": "model_only",
            "version_status": version_status,
            "actions": actions,
            "judge_progress": judge_progress,
            "dims": self.dims, "dim_zh": self.dim_zh,
            "target": self.rubric["target"],
            "can_advance": actions["advance"]["enabled"],
            "opt_history": self.opt_history,
            "optimizer_mode": getattr(self, "optimizer_mode", "switch_search"),
            "v0_strategy": getattr(self, "v0_strategy", "base_skill"),
            "optimizer_stop": optimizer_stop,
            "pending_candidate": (
                {
                    "version": cur["version"],
                    "parent": cur.get("parent"),
                    "candidate_state": cur.get("candidate_state"),
                    "proposal": cur.get("proposal"),
                }
                if getattr(self, "pending_idx", None) is not None
                else None
            ),
            "best_version": self._current()["version"],
            "history": persist.load_events(self.id),
        }

    def _version_view(self, v):
        sk = v["skill"]
        return {
            "version": v["version"], "parent": v["parent"], "adopted": v["adopted"],
            "changelog": v["changelog"],
            "directives_on": [k for k, on in sk.directives().items() if on],
            "dev": v["dev"], "test": v["test"],
            "proposal": v["proposal"],
            "instructions_prose": (
                sk.instructions or {}
            ).get("prose", ""),
            "requirement_contract": (
                sk.instructions or {}
            ).get("requirement_contract", ""),
            "candidate_state": v.get("candidate_state"),
            "verdict": v.get("verdict"),
            "verdict_reasons": v.get("verdict_reasons"),
            "evaluation_status": self._version_status(v),
        }

    def _version_real_judge_complete(self, version):
        case_ids = {str(case["case_id"]) for case in self.cases}
        if not case_ids:
            return False
        checks = self.judge_checks.get(version, {})
        direct = self.report_judgments.get(version, {})
        judged = {
            case_id
            for case_id in case_ids
            if (checks.get(case_id) or {}).get("checks")
            or case_id in direct
        }
        return judged == case_ids

    def _version_status(self, version_entry):
        version = version_entry["version"]
        case_ids = {str(case["case_id"]) for case in self.cases}
        reports = self.report_outputs.get(version, {})
        ready = {
            case_id
            for case_id in case_ids
            if (reports.get(case_id) or "").strip()
        }
        checks = self.judge_checks.get(version, {})
        direct = self.report_judgments.get(version, {})
        judged = {
            case_id
            for case_id in case_ids
            if (checks.get(case_id) or {}).get("checks")
            or case_id in direct
        }
        status, _actions = self._workflow_state(
            version_entry,
            case_ids,
            ready,
            judged,
            self.rubric.get("product") == "research_insight",
        )
        return status

    def _workflow_state(
        self,
        version_entry,
        case_ids,
        reports_ready,
        judged,
        requires_model_judge,
    ):
        block = version_entry.get("workflow_block")
        mapping_error = version_entry.get("failure_mapping_error") or []
        if block or mapping_error:
            status = "blocked"
        elif case_ids and not requires_model_judge:
            status = "optimizable"
        elif not case_ids or reports_ready != case_ids:
            status = "awaiting_generation"
        elif requires_model_judge and judged != case_ids:
            status = "reports_ready"
        else:
            status = "optimizable"

        missing_reports = len(case_ids - reports_ready)
        pending_judge = len(case_ids - judged)
        generation_reason = None
        judge_reason = None
        advance_reason = None
        if not case_ids:
            generation_reason = "尚未导入 case"
        elif reports_ready == case_ids:
            generation_reason = "当前版本已有完整报告"
        if reports_ready != case_ids:
            judge_reason = "尚缺 %d 份报告" % missing_reports
        elif requires_model_judge and judged == case_ids:
            judge_reason = "当前版本已完成全部 Judge"
        if status != "optimizable":
            if status == "blocked":
                advance_reason = block or (
                    "存在未映射 Judge check: "
                    + ", ".join(mapping_error)
                )
            elif pending_judge:
                advance_reason = "尚有 %d 个 case 未完成 Judge" % pending_judge
            else:
                advance_reason = "当前版本报告尚未就绪"
        return status, {
            "run_generation": {
                "enabled": bool(case_ids) and reports_ready != case_ids,
                "reason": generation_reason,
            },
            "run_judge": {
                "enabled": (
                    bool(case_ids)
                    and reports_ready == case_ids
                    and (
                        not requires_model_judge
                        or judged != case_ids
                    )
                ),
                "reason": judge_reason,
            },
            "advance": {
                "enabled": status == "optimizable",
                "reason": advance_reason,
            },
        }

    def _split_counts(self):
        from collections import Counter
        return dict(Counter(c["split"] for c in self.cases))
