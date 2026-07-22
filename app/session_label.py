# -*- coding: utf-8 -*-
"""
session_label.py — 人工标注 / 逐 check / 导入真实产物与评分 / 逐 check 校准 (SessionLabel mixin)

由 session.py 组合进 class Session。本文件负责「人在环的标注与真实产物导入」:
  import_output      —— 存平台跑出的真实报告文本(app 粘贴/上传)
  import_judgment    —— 存平台 LLM-judge 的六维评分(RecordedJudge, 覆盖 mock)
  submit_check_labels —— 专家逐 check 标注(满足/部分/不满足), 按账号
  set_judge_checks   —— 存 Opus 逐 check 判分(不分账号)
  _check_calibration —— 逐 check 人工 vs judge 一致率
  submit_labels      —— 维度级人工标注, 按账号合并

依赖 SessionCore 的 _current/_human_for/_human_checks_for/view/_save, 及 self.dims/self.dim_zh。
本文件的写入均触发 evaluate 重算 + 落盘。
"""
import sys
import os
from typing import Dict

HARNESS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness")
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

import persistence as persist                        # noqa: E402


class SessionLabel:
    """标注与真实产物导入 mixin。"""

    # ---------- 导入平台真实报告文本(app 粘贴) ----------
    def import_output(self, case_id: str, report_text: str, version: str = None, account=None):
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
        self.evaluate(account)
        self._save()
        return self.view(account)

    # ---------- 导入平台 LLM-judge 的六维评分(RecordedJudge) ----------
    def import_judgment(self, case_id: str, scores: Dict[str, int],
                        reasoning: Dict[str, str] = None, version: str = None, account=None):
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
        self.evaluate(account)
        self._save()
        return self.view(account)

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
        version = version or self._current()["version"]
        clean = self._norm_checks(checks)
        if not case_id or not clean:
            return {"error": "judge 未产出有效 check 评分"}
        self.judge_checks.setdefault(version, {})[case_id] = {"checks": clean, "reasoning": reasoning or {}}
        persist.append_check_judgment(self.id, version, case_id, clean, reasoning)
        persist.append_event(self.id, "run_judge",
                             {"version": version, "case_id": case_id, "n_checks": len(clean)})
        self.evaluate(account)
        self._save()
        return self.view(account)

    def _check_calibration(self, version, account=None):
        """逐 check 一致率:同 case 上 当前账号人工 vs judge 每条 check 桶匹配(完全一致=agree)。"""
        hc_all = self._human_checks_for(version, account)
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

    def submit_labels(self, version: str, labels: Dict[str, Dict[str, int]], account=None):
        """labels: {case_id: {dim: score,...}}。按账号合并进该版本的人工标注, 重新评估。"""
        store = self._human_for(version, account)
        applied = {}
        for cid, scores in labels.items():
            clean = {d: int(s) for d, s in scores.items() if d in self.dims and s is not None}
            if clean:
                store.setdefault(cid, {}).update(clean)
                applied[cid] = clean
        persist.append_event(self.id, "submit_labels", {
            "version": version, "account": account or "_legacy",
            "labels": applied, "n_cases_labeled": len(applied),
        })
        # 若标注的是当前版本, 重新评估以更新校准
        r = self.evaluate(account)
        self._save()
        return r
