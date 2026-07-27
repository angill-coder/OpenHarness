# -*- coding: utf-8 -*-
"""
session_core.py — 会话状态 / 快照 / 恢复 / 版本管理 (SessionCore mixin)

Session 已按职责拆成三个 mixin, 由 session.py 组合成 class Session(SessionCore, SessionEval, SessionLabel)。
本文件负责「状态骨架」:
  __init__          —— 从需求生成 v0 skill+rubric, 建立会话
  to_snapshot/_save —— 序列化 durable 输入, 落盘
  restore           —— 从快照重建并重算派生量
  view              —— 汇总当前会话状态给页面(纯模型 Judge 模式)
  版本管理           —— _add_version / _current / _human_for / _human_checks_for

本文件是模块级常量与 helper(_dims_from_rubric / _migrate_human_labels)的所有者。
"""
import sys
import os
from typing import Any, Dict, List, Optional

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


def _migrate_human_labels(hl):
    """把旧结构 {version:{case:{dim:score}}} 迁移成按账号 {version:{account:{case:{dim}}}}。
    旧记录归入哨兵账号 '_legacy'。已是新结构(内层值为 dict)的原样返回。"""
    out = {}
    for ver, vmap in (hl or {}).items():
        if not isinstance(vmap, dict) or not vmap:
            out[ver] = vmap if isinstance(vmap, dict) else {}
            continue
        # 判断: 新结构里 vmap 的值是 {case:{dim}}(dict of dict); 旧结构里是 {dim:score}(dict of num)
        sample = next(iter(vmap.values()))
        is_new = isinstance(sample, dict) and all(isinstance(v, dict) for v in sample.values()) if sample else True
        out[ver] = vmap if is_new else {"_legacy": vmap}
    return out


class SessionCore:
    """状态骨架 mixin。方法体依赖的 evaluate/_rec_view/_check_calibration 等由其它 mixin 提供。"""

    def __init__(self, sid: str, requirement: str, product_id: str, prefer_real=False,
                 _restoring=False):
        self.id = sid
        self.requirement = requirement
        self.product_id = product_id
        self._persist = True          # 落盘开关(恢复时先关, 重建完再开)

        gen = generator_mod.generate_v0(requirement, product_id, prefer_real=prefer_real)
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
        self.human_labels: Dict[str, Dict[str, Dict[str, int]]] = {}  # {version: {case_id: {dim: score}}}
        self.report_outputs: Dict[str, Dict[str, str]] = {}  # {version: {case_id: report_text}} 平台真实报告
        self.report_judgments: Dict[str, Dict[str, Dict]] = {}  # {version: {case_id: {scores,reasoning,flagged}}} 平台LLM-judge
        self.human_checks: Dict[str, Dict[str, Dict[str, float]]] = {}   # {version:{case:{check_id:1/0.5/0}}} 专家逐check
        self.judge_checks: Dict[str, Dict[str, Dict]] = {}   # {version:{case:{checks,reasoning}}} Opus逐check
        self.generation_imports: Dict[str, Dict[str, str]] = {}

        self._add_version(v0, adopted=True, proposal=None)

        if not _restoring:
            # 新建会话: 写 meta + created 事件 + 首个快照
            persist.init_session(sid, requirement, product_id)
            persist.append_event(sid, "created", {
                "product_id": product_id, "detected": self.detected,
                "rubric_version": self.rubric["version"],
                "v0_skill": v0.to_dict(), "rubric": self.rubric,
            })
            self._save()

    # ---------- 版本管理 ----------
    def _add_version(self, skill, adopted, proposal):
        self.versions.append({
            "skill": skill, "version": skill.version, "parent": skill.parent_version,
            "changelog": skill.changelog, "adopted": adopted, "proposal": proposal,
            "dev": None, "test": None, "eval": None, "failures": None, "calib": None,
            "failure_report": None, "failure_mapping_error": [],
            "workflow_block": None,
        })

    def _current(self):
        return self.versions[self.current_idx]

    def _human_for(self, version: str, account: str) -> Dict[str, Dict[str, int]]:
        """某账号在某版本的维度级人工标注 {case_id: {dim: score}}(可变, 供写入)。"""
        return self.human_labels.setdefault(version, {}).setdefault(account or "_legacy", {})

    def _human_checks_for(self, version: str, account) -> Dict[str, Dict[str, float]]:
        """某账号在某版本的逐 check 人工标注 {case_id: {check_id: val}}。account 为空则返回空(不叠加)。"""
        if not account:
            return {}
        return self.human_checks.get(version, {}).get(account, {})

    # ---------- 落盘 / 恢复 ----------
    def _save(self):
        """写最新快照(state.json)。派生量(dev/eval/failures/calib)不入快照, 恢复后重算。"""
        if not getattr(self, "_persist", True):
            return
        persist.save_snapshot(self.id, self.to_snapshot())

    def to_snapshot(self) -> Dict[str, Any]:
        """可序列化的持久态: 只存 durable 输入, 不存可重算的派生量。"""
        return {
            "id": self.id, "requirement": self.requirement, "product_id": self.product_id,
            "rubric": self.rubric, "gen_rationale": self.gen_rationale, "detected": self.detected,
            "current_idx": self.current_idx,
            "opt_history": self.opt_history,
            "cases": self.cases,
            "human_labels": self.human_labels,
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
            } for v in self.versions],
        }

    @classmethod
    def restore(cls, snap: Dict[str, Any], prefer_real=False) -> "SessionCore":
        """从快照重建 Session, 并重算派生量。"""
        self = cls.__new__(cls)
        self.id = snap["id"]
        self.requirement = snap["requirement"]
        self.product_id = snap["product_id"]
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
        self.human_labels = _migrate_human_labels(snap.get("human_labels", {}))
        self.report_outputs = persist.load_outputs(self.id)   # 真实报告文本由 outputs.jsonl 恢复
        self.report_judgments = persist.load_judgments(self.id)  # 真实报告的 LLM-judge 评分由 judgments.jsonl 恢复
        self.human_checks = persist.load_check_labels(self.id)   # 逐check人工标注由 check_labels.jsonl 恢复
        self.judge_checks = persist.load_check_judgments(self.id)  # 逐check judge 由 check_judgments.jsonl 恢复
        self.generation_imports = snap.get("generation_imports", {})
        self.failure_history = []
        self.versions = []
        for vd in snap["versions"]:
            self.versions.append({
                "skill": SkillArtifact.from_dict(vd["skill"]),
                "version": vd["skill"]["version"], "parent": vd["skill"].get("parent_version"),
                "changelog": vd["skill"].get("changelog", ""),
                "adopted": vd["adopted"], "proposal": vd["proposal"],
                "dev": None, "test": None, "eval": None, "failures": None, "calib": None,
                "failure_report": vd.get("failure_report"),
                "failure_mapping_error": vd.get(
                    "failure_mapping_error",
                    [],
                ),
                "workflow_block": vd.get("workflow_block"),
            })
        self.current_idx = snap.get("current_idx", 0)
        if self.cases:                 # 有数据则重算每个采纳版本的派生量(恢复分数曲线)
            saved_idx = self.current_idx
            for i, v in enumerate(self.versions):
                if not v["adopted"]:
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
        cur = self._current()
        recs = cur.get("_recs") or []
        # Web 流程已切换为纯模型 Judge；旧人工标注仍可恢复，但不再参与视图和门禁。
        current_eval = [self._rec_view(r, None) for r in recs]
        version = cur["version"]
        case_ids = {str(case["case_id"]) for case in self.cases}
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
            "current_version": cur["version"],
            "rubric": self.rubric,
            "versions": [self._version_view(v) for v in self.versions],
            "curve": [
                {
                    "version": v["version"],
                    "dev": v["dev"],
                    "test": v["test"],
                }
                for v in adopted
                if (
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
            # 保留字段形状，避免旧客户端崩溃；人工校准能力已停用。
            "calib": None,
            "check_calib": None,
            "dims": self.dims, "dim_zh": self.dim_zh,
            "target": self.rubric["target"],
            "can_advance": actions["advance"]["enabled"],
            "opt_history": self.opt_history,
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
