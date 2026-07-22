# -*- coding: utf-8 -*-
"""
session.py — 会话编排层 (人在环的逐版推进)

把 harness 的自动 run_loop 拆成页面能一版一版驱动的步骤:
  create()        —— 从需求描述生成 v0 skill + rubric, 建立会话
  import_data()   —— 导入数据集(dataset rows) + 可选人工标注
  evaluate()      —— 用当前版本 skill 跑 train+dev, 打分, 聚类失败, 算校准, 组装可呈现结果
  submit_labels() —— 接收人工对"当前版本每个 case 每个维度"的标注, 覆盖模拟分
  edit_rubric()   —— 改维度权重/阈值, 存为新的 rubric(重新评估)
  advance()       —— optimizer 读失败 -> 提候选 -> dev gate -> 采纳则成为新版本; 否则记录被拒
  view()          —— 汇总当前会话状态给页面

状态全内存(单进程演示)。每个 session 一个 id。
"""
import copy
import sys
import os
from typing import Any, Dict, List, Optional

HARNESS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness")
sys.path.insert(0, HARNESS)

from schemas import SkillArtifact, EvalRecord   # noqa: E402
import backend as backend_mod                    # noqa: E402
import runner as runner_mod                      # noqa: E402
import judge as judge_mod                         # noqa: E402
import calibration as calibration_mod             # noqa: E402
import clustering as clustering_mod               # noqa: E402
import optimizer as optimizer_mod                 # noqa: E402

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


<<<<<<< HEAD
=======
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


>>>>>>> origin/main
class Session:
    def __init__(self, sid: str, requirement: str, product_id: str, prefer_real=False,
                 _restoring=False):
        self.id = sid
        self.requirement = requirement
        self.product_id = product_id
        self._persist = True          # 落盘开关(恢复时先关, 重建完再开)

        gen = generator_mod.generate_v0(requirement, product_id, prefer_real=prefer_real)
        self.rubric = gen["rubric"]
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
        })

    def _current(self):
        return self.versions[self.current_idx]

<<<<<<< HEAD
    def _human_for(self, version: str) -> Dict[str, Dict[str, int]]:
        return self.human_labels.setdefault(version, {})
=======
    def _human_for(self, version: str, account: str) -> Dict[str, Dict[str, int]]:
        """某账号在某版本的维度级人工标注 {case_id: {dim: score}}(可变, 供写入)。"""
        return self.human_labels.setdefault(version, {}).setdefault(account or "_legacy", {})

    def _human_checks_for(self, version: str, account) -> Dict[str, Dict[str, float]]:
        """某账号在某版本的逐 check 人工标注 {case_id: {check_id: val}}。account 为空则返回空(不叠加)。"""
        if not account:
            return {}
        return self.human_checks.get(version, {}).get(account, {})
>>>>>>> origin/main

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
            "versions": [{
                "skill": v["skill"].to_dict(), "adopted": v["adopted"], "proposal": v["proposal"],
            } for v in self.versions],
        }

    @classmethod
    def restore(cls, snap: Dict[str, Any], prefer_real=False) -> "Session":
        """从快照重建 Session, 并重算派生量。"""
        self = cls.__new__(cls)
        self.id = snap["id"]
        self.requirement = snap["requirement"]
        self.product_id = snap["product_id"]
        self._persist = False          # 重建期间不写盘
        self.rubric = snap["rubric"]
        self.gen_rationale = snap.get("gen_rationale", "")
        self.detected = snap.get("detected", {})
        self.dims, self.dim_zh = _dims_from_rubric(self.rubric)
        self.backend = backend_mod.get_backend(product_id=self.rubric.get("product"))
        self.opt_history = snap.get("opt_history", [])
        self.cases = snap.get("cases", [])
<<<<<<< HEAD
        self.human_labels = snap.get("human_labels", {})
=======
        self.human_labels = _migrate_human_labels(snap.get("human_labels", {}))
>>>>>>> origin/main
        self.report_outputs = persist.load_outputs(self.id)   # 真实报告文本由 outputs.jsonl 恢复
        self.report_judgments = persist.load_judgments(self.id)  # 真实报告的 LLM-judge 评分由 judgments.jsonl 恢复
        self.human_checks = persist.load_check_labels(self.id)   # 逐check人工标注由 check_labels.jsonl 恢复
        self.judge_checks = persist.load_check_judgments(self.id)  # 逐check judge 由 check_judgments.jsonl 恢复
        self.failure_history = []
        self.versions = []
        for vd in snap["versions"]:
            self.versions.append({
                "skill": SkillArtifact.from_dict(vd["skill"]),
                "version": vd["skill"]["version"], "parent": vd["skill"].get("parent_version"),
                "changelog": vd["skill"].get("changelog", ""),
                "adopted": vd["adopted"], "proposal": vd["proposal"],
                "dev": None, "test": None, "eval": None, "failures": None, "calib": None,
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

    # ---------- 数据导入 ----------
<<<<<<< HEAD
    def import_data(self, rows: List[Dict[str, Any]], labels: Optional[List[Dict]] = None):
=======
    def import_data(self, rows: List[Dict[str, Any]], labels: Optional[List[Dict]] = None, account=None):
>>>>>>> origin/main
        # 校验最小字段(算数字型认 ground_truth_findings; 调研洞察认 ground_truth)
        is_research = self.rubric.get("product") == "research_insight"
        clean = []
        for r in rows:
            if "case_id" not in r or "input" not in r:
                continue
            if is_research and "ground_truth" not in r:
                continue
            if not is_research and "ground_truth_findings" not in r:
                continue
            r.setdefault("split", "dev")
            r.setdefault("hard_case_tags", [])
            r.setdefault("required_sections", [])
            r.setdefault("audience", self.detected.get("audience", "exec"))
            r.setdefault("key_finding_ids", [])
            clean.append(r)
        self.cases = clean
        # 可选: 导入初始人工标注(作用于 v0)
        if labels:
            v0 = self.versions[0]["version"]
<<<<<<< HEAD
            store = self._human_for(v0)
=======
            store = self._human_for(v0, account)
>>>>>>> origin/main
            for l in labels:
                if "case_id" in l and "human_scores" in l:
                    store[l["case_id"]] = l["human_scores"]
        persist.append_event(self.id, "import_data", {
            "n_cases": len(clean), "splits": self._split_counts(),
            "with_initial_labels": bool(labels),
        })
        # 重新评估当前版本
<<<<<<< HEAD
        r = self.evaluate()
=======
        r = self.evaluate(account)
>>>>>>> origin/main
        self._save()
        return r

    # ---------- 评估当前版本 ----------
<<<<<<< HEAD
    def evaluate(self):
=======
    def evaluate(self, account=None):
        """计算与账号无关的基础量(judge/mock 分、失败聚类、dev/test 均分)并暂存基础记录;
        人工标注叠加与校准是按账号的, 放到 view(account) 时再算(线程安全: 不把账号数据写进共享缓存)。"""
>>>>>>> origin/main
        if not self.cases:
            return {"error": "尚未导入数据"}
        cur = self._current()
        skill = cur["skill"]
        ver = cur["version"]

        train_dev = [c for c in self.cases if c["split"] in ("train", "dev")]
        dev = [c for c in self.cases if c["split"] == "dev"]
        test = [c for c in self.cases if c["split"] == "test"]

<<<<<<< HEAD
        # 人工标注(该版本已提交的) 注入 EvalRecord.human_label
        human = self._merge_human_labels(ver)

        recs_all = runner_mod.run_split(skill, self.cases, self.rubric, self.backend, ver, human)
=======
        # 基础评估与账号无关: 不注入人工分(人工 overlay 在 view 时按账号叠加)
        recs_all = runner_mod.run_split(skill, self.cases, self.rubric, self.backend, ver, {})
>>>>>>> origin/main
        # 用平台真实报告 + LLM-judge 评分覆盖 mock(有则真实, 无则保留 mock 作占位)
        self._apply_recorded(recs_all, ver)
        dev_recs = [r for r in recs_all if r.dataset_split == "dev"]
        test_recs = [r for r in recs_all if r.dataset_split == "test"]

        cur["dev"] = runner_mod.mean_scores(dev_recs, self.rubric) if dev else runner_mod.mean_scores(recs_all, self.rubric)
        cur["test"] = runner_mod.mean_scores(test_recs, self.rubric) if test else None
        cur["failures"] = clustering_mod.cluster(
            [r for r in recs_all if r.dataset_split in ("train", "dev")] or recs_all,
            product=self.rubric.get("product"))
<<<<<<< HEAD
        cur["calib"] = calibration_mod.agreement(recs_all, self.rubric)
        cur["eval"] = [self._rec_view(r) for r in recs_all]
=======
        cur["_recs"] = recs_all         # 暂存基础记录(账号无关), 供 view(account) 叠加人工分
>>>>>>> origin/main

        # 记录失败历史(用于看板消长)
        if len(self.failure_history) <= self.current_idx:
            self.failure_history.append(cur["failures"])
        else:
            self.failure_history[self.current_idx] = cur["failures"]
<<<<<<< HEAD
        return self.view()

    def _merge_human_labels(self, version) -> Dict[str, Dict[str, int]]:
        """当前版本已提交的人工标注 -> case_id: scores。未标注的 case 不给 human_label
        (校准只用被标注的)。"""
        return dict(self._human_for(version))
=======
        return self.view(account)
>>>>>>> origin/main

    def _apply_recorded(self, recs, version):
        """把平台真实产物叠加到 mock 记录上:
          · report_outputs -> 记录的 output 换成真实报告文本(标 recorded)
          · judge 分(优先逐 check 派生, 其次旧 import_judgment) -> 覆盖 mock 六维分并重算红线
<<<<<<< HEAD
          · 人工分优先由逐 check 标注派生(dim_from_checks) -> 喂维度级校准(一处 check 标注两处受益)
        无真实评分的 case 保留 mock 分(占位/自测), 由 score_source 区分。"""
        outs = self.report_outputs.get(version, {})
        juds = self.report_judgments.get(version, {})
        hchecks = self.human_checks.get(version, {})
=======
        无真实评分的 case 保留 mock 分(占位/自测), 由 score_source 区分。
        人工分(逐 check 派生的 human_label)与账号相关, 不在此叠加, 由 view(account) 时按账号算。"""
        outs = self.report_outputs.get(version, {})
        juds = self.report_judgments.get(version, {})
>>>>>>> origin/main
        jchecks = self.judge_checks.get(version, {})
        floor = judge_mod._hard_floor(self.rubric, "traceability")
        for r in recs:
            rt = outs.get(r.case_id)
            if rt is not None:
                r.output = {"report_text": rt, "signals": {}, "recorded": True,
                            "audience": r.output.get("audience", "exec")}
<<<<<<< HEAD
            # 人工分:有逐 check 标注则派生成维度分(供维度级校准)
            hc = hchecks.get(r.case_id)
            if hc:
                r.human_label = judge_mod.dim_from_checks(hc, self.rubric)
=======
>>>>>>> origin/main
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

<<<<<<< HEAD
    def _rec_view(self, r: EvalRecord) -> Dict[str, Any]:
        # 平台真实报告文本(当前版本已粘贴的), 供人工按 rubric 逐维标注时对照阅读
        ver = self._current()["version"]
        real_report = self.report_outputs.get(ver, {}).get(r.case_id)
        hc = self.human_checks.get(ver, {}).get(r.case_id, {})
        jc = (self.judge_checks.get(ver, {}).get(r.case_id) or {}).get("checks", {})
        jr = (self.judge_checks.get(ver, {}).get(r.case_id) or {}).get("reasoning", {})
=======
    def _rec_view(self, r: EvalRecord, account=None) -> Dict[str, Any]:
        # 平台真实报告文本(当前版本已粘贴的), 供人工按 rubric 逐维标注时对照阅读
        ver = self._current()["version"]
        real_report = self.report_outputs.get(ver, {}).get(r.case_id)
        hc = self._human_checks_for(ver, account).get(r.case_id, {})      # 当前账号的逐 check 标注
        jc = (self.judge_checks.get(ver, {}).get(r.case_id) or {}).get("checks", {})
        jr = (self.judge_checks.get(ver, {}).get(r.case_id) or {}).get("reasoning", {})
        human_label = judge_mod.dim_from_checks(hc, self.rubric) if hc \
            else self._human_for(ver, account).get(r.case_id)
>>>>>>> origin/main
        return {
            "case_id": r.case_id, "split": r.dataset_split,
            "scores": r.scores, "judge_reasoning": r.judge_reasoning,
            "flagged": r.flagged, "red_line": r.case_failed_gate,
<<<<<<< HEAD
            "human_label": r.human_label,
=======
            "human_label": human_label,
>>>>>>> origin/main
            "output_summary": self._output_summary(r.output),
            "report_text": real_report,          # None 表示该 case 尚未导入真实报告
            "score_source": getattr(r, "score_source", "mock"),  # recorded=平台LLM-judge真实分 / mock=占位
            # 逐 check 层(真实标注/校准线)
<<<<<<< HEAD
            "check_human": hc,                    # {check_id: 1/0.5/0} 专家
=======
            "check_human": hc,                    # {check_id: 1/0.5/0} 当前账号
>>>>>>> origin/main
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

    # ---------- 导入平台真实报告文本(app 粘贴) ----------
<<<<<<< HEAD
    def import_output(self, case_id: str, report_text: str, version: str = None):
=======
    def import_output(self, case_id: str, report_text: str, version: str = None, account=None):
>>>>>>> origin/main
        """存一条平台跑出的真实报告文本, 关联到 (version, case_id)。默认当前版本。"""
        version = version or self._current()["version"]
        report_text = (report_text or "").strip()
        if not case_id or not report_text:
            return {"error": "缺少 case_id 或 report_text"}
        self.report_outputs.setdefault(version, {})[case_id] = report_text
        persist.append_output(self.id, version, case_id, report_text)
        persist.append_event(self.id, "import_output", {
            "version": version, "case_id": case_id, "n_chars": len(report_text)})
        # 重新组装视图(把真实报告文本带进 eval 行, 供标注对照)
<<<<<<< HEAD
        self.evaluate()
        self._save()
        return self.view()

    # ---------- 导入平台 LLM-judge 的六维评分(RecordedJudge) ----------
    def import_judgment(self, case_id: str, scores: Dict[str, int],
                        reasoning: Dict[str, str] = None, version: str = None):
=======
        self.evaluate(account)
        self._save()
        return self.view(account)

    # ---------- 导入平台 LLM-judge 的六维评分(RecordedJudge) ----------
    def import_judgment(self, case_id: str, scores: Dict[str, int],
                        reasoning: Dict[str, str] = None, version: str = None, account=None):
>>>>>>> origin/main
        """存一条平台 LLM-as-judge 对真实报告的六维评分, 关联到 (version, case_id)。
        它会覆盖该 case 的 mock 分, 参与分数曲线/校准/红线判定。默认当前版本。"""
        version = version or self._current()["version"]
        clean = {d: int(s) for d, s in (scores or {}).items() if d in self.dims and s is not None}
        if not case_id or not clean:
            return {"error": "缺少 case_id 或有效的六维分(需属于: %s)" % ", ".join(self.dims)}
        reasoning = {d: str(t) for d, t in (reasoning or {}).items() if d in self.dims}
        self.report_judgments.setdefault(version, {})[case_id] = {
            "scores": clean, "reasoning": reasoning, "flagged": []}
        persist.append_judgment(self.id, version, case_id, clean, reasoning)
        persist.append_event(self.id, "import_judgment", {
            "version": version, "case_id": case_id, "scores": clean})
<<<<<<< HEAD
        self.evaluate()
        self._save()
        return self.view()
=======
        self.evaluate(account)
        self._save()
        return self.view(account)
>>>>>>> origin/main

    # ---------- 逐 check 层:人工标注 & judge ----------
    _CHECK_MAP = {"met": 1.0, "partial": 0.5, "miss": 0.0}

    def _norm_checks(self, checks):
        """把 {check_id: 'met'/'partial'/'miss' 或 1/0.5/0} 归一成数值,丢弃非法值。"""
        out = {}
        for k, v in (checks or {}).items():
            if v is None:
                continue
            num = self._CHECK_MAP.get(v) if isinstance(v, str) else float(v)
            if num is not None:
                out[k] = num
        return out

<<<<<<< HEAD
    def submit_check_labels(self, case_id, checks, version=None):
        """专家逐 check 标注(满足/部分/不满足)。存 + 落盘 + 重评。"""
        version = version or self._current()["version"]
        clean = self._norm_checks(checks)
        if not case_id or not clean:
            return {"error": "缺少 case_id 或有效 check 评分"}
        self.human_checks.setdefault(version, {}).setdefault(case_id, {}).update(clean)
        persist.append_check_label(self.id, version, case_id, self.human_checks[version][case_id])
        persist.append_event(self.id, "submit_check_labels",
                             {"version": version, "case_id": case_id, "n_checks": len(clean)})
        self.evaluate()
        self._save()
        return self.view()

    def set_judge_checks(self, case_id, checks, reasoning=None, version=None):
        """存 LLM-judge 的逐 check 判分(供 /api/run_judge 调用)。"""
=======
    def submit_check_labels(self, case_id, checks, version=None, account=None):
        """专家逐 check 标注(满足/部分/不满足)。按账号存 + 落盘 + 重评。"""
        version = version or self._current()["version"]
        acct = account or "_legacy"
        clean = self._norm_checks(checks)
        if not case_id or not clean:
            return {"error": "缺少 case_id 或有效 check 评分"}
        store = self.human_checks.setdefault(version, {}).setdefault(acct, {}).setdefault(case_id, {})
        store.update(clean)
        persist.append_check_label(self.id, version, case_id, store, acct)
        persist.append_event(self.id, "submit_check_labels",
                             {"version": version, "case_id": case_id, "account": acct, "n_checks": len(clean)})
        self.evaluate(account)
        self._save()
        return self.view(account)

    def set_judge_checks(self, case_id, checks, reasoning=None, version=None, account=None):
        """存 LLM-judge 的逐 check 判分(供 /api/run_judge 调用)。judge 是机器分, 不分账号。"""
>>>>>>> origin/main
        version = version or self._current()["version"]
        clean = self._norm_checks(checks)
        if not case_id or not clean:
            return {"error": "judge 未产出有效 check 评分"}
        self.judge_checks.setdefault(version, {})[case_id] = {"checks": clean, "reasoning": reasoning or {}}
        persist.append_check_judgment(self.id, version, case_id, clean, reasoning)
        persist.append_event(self.id, "run_judge",
                             {"version": version, "case_id": case_id, "n_checks": len(clean)})
<<<<<<< HEAD
        self.evaluate()
        self._save()
        return self.view()

    def _check_calibration(self, version):
        """逐 check 一致率:同 case 上 人工 vs judge 每条 check 桶匹配(完全一致=agree)。"""
        hc_all = self.human_checks.get(version, {})
=======
        self.evaluate(account)
        self._save()
        return self.view(account)

    def _check_calibration(self, version, account=None):
        """逐 check 一致率:同 case 上 当前账号人工 vs judge 每条 check 桶匹配(完全一致=agree)。"""
        hc_all = self._human_checks_for(version, account)
>>>>>>> origin/main
        jc_all = self.judge_checks.get(version, {})
        per_check = {}
        pairs = 0
        for cid in hc_all:
            if cid not in jc_all:
                continue
            pairs += 1
            h, j = hc_all[cid], jc_all[cid].get("checks", {})
            for k in h:
                if k in j:
                    a, t = per_check.get(k, (0, 0))
                    per_check[k] = (a + (1 if h[k] == j[k] else 0), t + 1)
        tot_a = sum(a for a, _ in per_check.values())
        tot = sum(t for _, t in per_check.values())
        rates = {k: round(a / t, 3) for k, (a, t) in per_check.items() if t}
        return {"n_case_pairs": pairs, "n_checks_compared": tot,
                "overall": round(tot_a / tot, 3) if tot else None,
                "per_check": rates,
                "worst": sorted(rates.items(), key=lambda kv: kv[1])[:5]}

<<<<<<< HEAD
    def submit_labels(self, version: str, labels: Dict[str, Dict[str, int]]):
        """labels: {case_id: {dim: score,...}}。合并进该版本的人工标注, 重新评估。"""
        store = self._human_for(version)
=======
    def submit_labels(self, version: str, labels: Dict[str, Dict[str, int]], account=None):
        """labels: {case_id: {dim: score,...}}。按账号合并进该版本的人工标注, 重新评估。"""
        store = self._human_for(version, account)
>>>>>>> origin/main
        applied = {}
        for cid, scores in labels.items():
            clean = {d: int(s) for d, s in scores.items() if d in self.dims and s is not None}
            if clean:
                store.setdefault(cid, {}).update(clean)
                applied[cid] = clean
        persist.append_event(self.id, "submit_labels", {
<<<<<<< HEAD
            "version": version, "labels": applied, "n_cases_labeled": len(applied),
        })
        # 若标注的是当前版本, 重新评估以更新校准
        r = self.evaluate()
=======
            "version": version, "account": account or "_legacy",
            "labels": applied, "n_cases_labeled": len(applied),
        })
        # 若标注的是当前版本, 重新评估以更新校准
        r = self.evaluate(account)
>>>>>>> origin/main
        self._save()
        return r

    # ---------- 编辑 rubric ----------
<<<<<<< HEAD
    def edit_rubric(self, updates: Dict[str, Any]):
=======
    def edit_rubric(self, updates: Dict[str, Any], account=None):
>>>>>>> origin/main
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
<<<<<<< HEAD
        r = self.evaluate()
=======
        r = self.evaluate(account)
>>>>>>> origin/main
        self._save()
        return r

    # ---------- 推进到下一版 ----------
<<<<<<< HEAD
    def advance(self):
=======
    def advance(self, account=None):
>>>>>>> origin/main
        """optimizer 读当前失败 -> 提候选 -> dev gate -> 采纳成为新版本。"""
        if not self.cases:
            return {"error": "尚未导入数据"}
        cur = self._current()
        if cur["failures"] is None:
<<<<<<< HEAD
            self.evaluate()
=======
            self.evaluate(account)
>>>>>>> origin/main
            cur = self._current()

        skill = cur["skill"]
        proposal = optimizer_mod.propose(skill, cur["failures"], self.opt_history)
        if proposal is None:
            note = self._plateau_note(cur["failures"])
            persist.append_event(self.id, "converged", {
                "at_version": skill.version, "note": note})
            self._save()
<<<<<<< HEAD
            return {**self.view(), "advance_result": {
=======
            return {**self.view(account), "advance_result": {
>>>>>>> origin/main
                "status": "converged",
                "message": "优化器无更多可提议改动 => 平台期/收敛。" + note}}

        vnum = sum(1 for v in self.versions if v["adopted"])   # 下一个版本号
        cand_ver = "v%d" % vnum
        candidate = optimizer_mod.apply_proposal(skill, proposal, cand_ver)

<<<<<<< HEAD
        # dev gate
        dev = [c for c in self.cases if c["split"] == "dev"] or self.cases
        human = self._merge_human_labels(skill.version)
=======
        # dev gate(与账号无关: gate 用 judge/mock 分, 不用人工分)
        dev = [c for c in self.cases if c["split"] == "dev"] or self.cases
        human = {}
>>>>>>> origin/main
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
            self.opt_history.append({"target": proposal["target"], "directive": proposal["directive"],
                                     "result": "adopted",
                                     "delta": round(cand_dev["overall"] - cur_dev["overall"], 3)})
<<<<<<< HEAD
            self.evaluate()
=======
            self.evaluate(account)
>>>>>>> origin/main
            result = {"status": "adopted", "version": cand_ver, "proposal": proposal,
                      "message": "采纳 %s: %s，dev overall %.2f -> %.2f" % (
                          cand_ver, proposal["change"], cur_dev["overall"], cand_dev["overall"])}
            persist.append_event(self.id, "version_adopted", {
                "version": cand_ver, "parent": skill.version, "proposal": proposal,
                "directives_on": [k for k, on in candidate.directives().items() if on],
                "dev": cand_dev, "changelog": candidate.changelog,
            })
        else:
            reason = ("目标维度未涨" if not improved
                      else ("维度 %s 回退超容差" % self.dim_zh.get(regressed, regressed) if regressed
                            else "引入红线失败"))
            # 记录被拒版本(不推进 current)
            self._add_version(candidate, adopted=False, proposal=proposal)
            self.opt_history.append({"target": proposal["target"], "directive": proposal["directive"],
                                     "result": "rejected", "reason": reason})
            result = {"status": "rejected", "version": cand_ver, "proposal": proposal,
                      "reason": reason,
                      "message": "候选 %s 被 gate 拒绝: %s (已记入 history, 不再重试)" % (cand_ver, reason)}
            persist.append_event(self.id, "version_rejected", {
                "version": cand_ver, "parent": skill.version, "proposal": proposal,
                "reason": reason, "dev": cand_dev,
            })
        self._save()
<<<<<<< HEAD
        v = self.view()
=======
        v = self.view(account)
>>>>>>> origin/main
        v["advance_result"] = result
        return v

    def _plateau_note(self, failures):
        if not failures:
            return " 失败模式已零散, 当前结构够用。"
        top = failures[0]
        if top.get("directive_hint") is None:
            return " 首要失败'%s'无指令级修法 => 触发结构优化(Phase3)信号, 需人工回改 v0 结构。" % top["pattern"]
        return " 剩余失败仍是指令/内容级, 未到动结构的时候。"

    # ---------- 视图 ----------
<<<<<<< HEAD
    def view(self):
        adopted = [v for v in self.versions if v["adopted"]]
        cur = self._current()
=======
    def view(self, account=None):
        adopted = [v for v in self.versions if v["adopted"]]
        cur = self._current()
        recs = cur.get("_recs") or []
        # 按当前账号叠加人工分, 现算 eval 行与校准(不写共享缓存, 线程安全)
        current_eval = [self._rec_view(r, account) for r in recs]
        acc_recs = []
        for r in recs:
            rr = copy.copy(r)
            rr.human_label = self._rec_view(r, account).get("human_label")
            acc_recs.append(rr)
        calib = calibration_mod.agreement(acc_recs, self.rubric) if recs else None
>>>>>>> origin/main
        return {
            "session_id": self.id,
            "requirement": self.requirement,
            "product_id": self.product_id,
<<<<<<< HEAD
=======
            "account": account,
>>>>>>> origin/main
            "backend": self.backend.name,
            "detected": self.detected,
            "gen_rationale": self.gen_rationale,
            "n_cases": len(self.cases),
            "splits": self._split_counts(),
            "current_version": cur["version"],
            "rubric": self.rubric,
            "versions": [self._version_view(v) for v in self.versions],
            "curve": [{"version": v["version"], "dev": v["dev"], "test": v["test"]}
                      for v in adopted],
<<<<<<< HEAD
            "current_eval": cur["eval"],
            "current_failures": cur["failures"],
            "calib": cur["calib"],
            "check_calib": self._check_calibration(cur["version"]),
=======
            "current_eval": current_eval,
            "current_failures": cur["failures"],
            "calib": calib,
            "check_calib": self._check_calibration(cur["version"], account),
>>>>>>> origin/main
            "dims": self.dims, "dim_zh": self.dim_zh,
            "target": self.rubric["target"],
            "can_advance": bool(self.cases),
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
        }

    def _split_counts(self):
        from collections import Counter
        return dict(Counter(c["split"] for c in self.cases))
