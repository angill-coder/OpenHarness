# -*- coding: utf-8 -*-
"""
session_label.py — 导入真实产物与模型 Judge 评分 (SessionLabel mixin)

由 session.py 组合进 class Session。本文件负责「真实产物与模型评分导入」:
  import_output      —— 存平台跑出的真实报告文本(app 粘贴/上传)
  import_judgment    —— 存平台 LLM-judge 的六维评分(RecordedJudge, 覆盖 mock)
  set_judge_checks   —— 存 Opus 逐 check 判分(不分账号)

依赖 SessionCore 的 _current/view/_save, 及 self.dims。
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
    """真实产物与模型 Judge 结果导入 mixin。"""

    # ---------- 导入平台真实报告文本(app 粘贴) ----------
    def import_output(self, case_id: str, report_text: str, version: str = None, account=None):
        """存一条平台跑出的真实报告文本, 关联到 (version, case_id)。默认当前版本。"""
        version = version or self._current()["version"]
        report_text = (report_text or "").strip()
        if not case_id or not report_text:
            return {"error": "缺少 case_id 或 report_text"}
        previous = self.report_outputs.get(version, {}).get(case_id)
        if previous is not None and previous != report_text:
            self._invalidate_judge_checks(
                version,
                [case_id],
                "report_changed",
            )
        self.report_outputs.setdefault(version, {})[case_id] = report_text
        persist.append_output(self.id, version, case_id, report_text)
        persist.append_event(self.id, "import_output", {
            "version": version, "case_id": case_id, "n_chars": len(report_text)})
        # 重新组装视图(把真实报告文本带进 eval 行, 供标注对照)
        self.evaluate(account)
        self._save()
        return self.view(account)

    # ---------- 导入平台 LLM-judge 的六维评分(RecordedJudge) ----------
    def import_judgment(self, case_id: str, scores: Dict[str, float],
                        reasoning: Dict[str, str] = None, version: str = None, account=None):
        """存一条平台 LLM-as-judge 对真实报告的六维评分, 关联到 (version, case_id)。
        它会覆盖该 case 的 mock 分, 参与分数曲线和红线判定。默认当前版本。"""
        version = version or self._current()["version"]
        clean = {
            d: round(float(s), 3)
            for d, s in (scores or {}).items()
            if d in self.dims and s is not None
        }
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

    # ---------- 模型 Judge 逐 check 层 ----------
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

    def set_judge_checks_batch(
        self,
        judgments,
        version=None,
        account=None,
        evaluate_now=True,
    ):
        """批量存模型逐-check判分，只重评和落盘一次。

        ``judgments`` 形如
        ``{case_id: {"checks": {...}, "reasoning": {...}}}``。
        无有效判分时不改变 Session。
        """
        version = version or self._current()["version"]
        valid_case_ids = {case["case_id"] for case in self.cases}
        clean_batch = {}
        for case_id, judgment in (judgments or {}).items():
            if case_id not in valid_case_ids:
                continue
            clean = self._norm_checks((judgment or {}).get("checks"))
            if not clean:
                continue
            clean_batch[case_id] = {
                "checks": clean,
                "reasoning": (judgment or {}).get("reasoning") or {},
                "report_sha256": (judgment or {}).get(
                    "report_sha256"
                ),
                "rubric_sha256": (judgment or {}).get(
                    "rubric_sha256"
                ),
                "llm_backend": (judgment or {}).get("llm_backend"),
                "model": (judgment or {}).get("model"),
            }
        if not clean_batch:
            return {"error": "批量 Judge 未产出有效评分"}
        self.judge_checks.setdefault(version, {}).update(clean_batch)
        persist.append_check_judgments(self.id, version, clean_batch)
        persist.append_event(
            self.id,
            "run_judge_batch",
            {
                "version": version,
                "case_ids": list(clean_batch),
                "n_cases": len(clean_batch),
                "llm_backend": next(
                    (
                        item.get("llm_backend")
                        for item in clean_batch.values()
                        if item.get("llm_backend")
                    ),
                    None,
                ),
                "model": next(
                    (
                        item.get("model")
                        for item in clean_batch.values()
                        if item.get("model")
                    ),
                    None,
                ),
            },
        )
        if evaluate_now:
            self.evaluate(account)
            self._save()
            return self.view(account)
        return None

    def _invalidate_judge_checks(self, version, case_ids, reason):
        existing = self.judge_checks.setdefault(version, {})
        invalidated = [
            case_id
            for case_id in case_ids
            if case_id in existing
        ]
        for case_id in invalidated:
            existing.pop(case_id, None)
        persist.invalidate_check_judgments(
            self.id,
            version,
            invalidated,
            reason,
        )
        if invalidated:
            persist.append_event(
                self.id,
                "invalidate_judge_checks",
                {
                    "version": version,
                    "case_ids": sorted(invalidated),
                    "reason": reason,
                },
            )
