"""Revision Brief 生成的确定性测试。

brief 是 Writer 能看到的**全部**评测信息——原始 Judge 输出被刻意隔离。
因此这里重点验证两件事：

1. 分流规则确定性：同一份 Judgment 必然得到同样的 repair/preserve/avoid；
2. brief 不泄漏 Judge 原文之外不该带的内容，且缺失数据时明确失败而非补全。
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "resources/report/revision_brief.py"
sys.path.insert(0, str(ROOT / "resources" / "report"))

from revision_brief import BriefError, build_brief, normalize_checks  # noqa: E402


PLAN = {
    "dimensions": [
        {
            "id": "traceability",
            "weight": 0.28,
            "hardFloor": 3,
            "checks": [
                {"id": "T1", "label": "不编造", "desc": "不得出现素材外的事实", "redline": True},
                {"id": "T2", "label": "素材忠实", "desc": "引用与素材一致"},
            ],
        },
        {
            "id": "structure",
            "weight": 0.72,
            "checks": [{"id": "S1", "label": "结论先行", "desc": "摘要先给结论"}],
        },
    ]
}


def checks(**statuses: str) -> dict[str, dict[str, str]]:
    return {
        check_id: {"status": value, "evidence": f"证据:{check_id}"}
        for check_id, value in statuses.items()
    }


class NormalizeTests(unittest.TestCase):
    def test_accepts_single_dimension_result_files(self):
        merged = normalize_checks([
            {"dimensionId": "traceability", "checks": [
                {"id": "T1", "status": "met", "evidence": "a"},
                {"id": "T2", "status": "miss", "evidence": "b"},
            ]},
            {"dimensionId": "structure", "checks": [
                {"id": "S1", "status": "partial", "evidence": "c"},
            ]},
        ])

        self.assertEqual(merged["T1"]["status"], "met")
        self.assertEqual(merged["S1"]["evidence"], "c")
        self.assertEqual(len(merged), 3)

    def test_accepts_aggregated_judgment_mapping(self):
        merged = normalize_checks([
            {"checks": {"T1": "met", "T2": "partial"},
             "reasoning": {"T1": "ok", "T2": "缺页码"}},
        ])

        self.assertEqual(merged["T2"]["status"], "partial")
        self.assertEqual(merged["T2"]["evidence"], "缺页码")

    def test_missing_checks_is_rejected(self):
        with self.assertRaises(BriefError):
            normalize_checks([{"dimensionId": "x"}])

    def test_empty_checks_is_rejected(self):
        with self.assertRaises(BriefError):
            normalize_checks([{"checks": []}])


class BriefRulesTests(unittest.TestCase):
    def test_non_met_checks_go_to_repair(self):
        brief = build_brief(
            plan=PLAN,
            best=checks(T1="met", T2="partial", S1="miss"),
            best_version="R1",
        )

        repaired = {item["checkId"] for item in brief["repair"]}
        self.assertEqual(repaired, {"T2", "S1"})
        entry = next(item for item in brief["repair"] if item["checkId"] == "T2")
        self.assertEqual(entry["status"], "partial")
        self.assertEqual(entry["dimension"], "traceability")
        self.assertEqual(entry["requirement"], "引用与素材一致")

    def test_met_checks_go_to_preserve(self):
        brief = build_brief(
            plan=PLAN,
            best=checks(T1="met", T2="met", S1="miss"),
            best_version="R1",
        )

        preserved = {item["checkId"] for item in brief["preserve"]}
        self.assertEqual(preserved, {"T1", "T2"})

    def test_user_override_is_separated_from_preserve(self):
        """因用户要求豁免的 Check 不混进 preserve，避免 Writer 为追分反改。"""
        best = checks(T1="met", T2="met", S1="met")
        best["S1"]["evidence"] = "user_override: 用户明确要求 IMRD 结构"

        brief = build_brief(plan=PLAN, best=best, best_version="R1")

        self.assertEqual([item["checkId"] for item in brief["userOverrides"]], ["S1"])
        self.assertNotIn("S1", {item["checkId"] for item in brief["preserve"]})

    def test_full_width_colon_override_is_recognised(self):
        best = checks(T1="met", T2="met", S1="met")
        best["S1"]["evidence"] = "user_override：用户要求保留原结构"

        brief = build_brief(plan=PLAN, best=best, best_version="R1")

        self.assertEqual(len(brief["userOverrides"]), 1)

    def test_regression_from_rejected_candidate_goes_to_avoid(self):
        brief = build_brief(
            plan=PLAN,
            best=checks(T1="met", T2="partial", S1="met"),
            best_version="R1",
            rejected=checks(T1="met", T2="miss", S1="met"),
            rejected_parent=checks(T1="met", T2="met", S1="met"),
        )

        self.assertEqual([item["checkId"] for item in brief["avoid"]], ["T2"])
        self.assertEqual(brief["avoid"][0]["regressedTo"], "miss")

    def test_improvements_in_rejected_candidate_are_not_avoided(self):
        brief = build_brief(
            plan=PLAN,
            best=checks(T1="met", T2="partial", S1="met"),
            best_version="R1",
            rejected=checks(T1="met", T2="met", S1="met"),
            rejected_parent=checks(T1="met", T2="partial", S1="met"),
        )

        self.assertEqual(brief["avoid"], [])

    def test_missing_check_in_judgment_is_an_error(self):
        """Plan 有但 Judgment 未覆盖：属于评测不完整，不静默当作已达成。"""
        with self.assertRaises(BriefError) as caught:
            build_brief(plan=PLAN, best=checks(T1="met"), best_version="R1")

        self.assertIn("T2", str(caught.exception))

    def test_plan_without_dimensions_is_rejected(self):
        for bad in ({}, {"dimensions": []}, {"dimensions": [{"id": "x"}]}):
            with self.assertRaises(BriefError):
                build_brief(plan=bad, best=checks(T1="met"), best_version="R1")

    def test_same_judgment_always_yields_the_same_brief(self):
        """确定性：手工提炼做不到这一点，这正是用脚本的理由。"""
        best = checks(T1="met", T2="partial", S1="miss")

        first = build_brief(plan=PLAN, best=best, best_version="R1",
                            user_requirements="面向CEO")
        second = build_brief(plan=PLAN, best=best, best_version="R1",
                             user_requirements="面向CEO")

        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True),
            json.dumps(second, ensure_ascii=False, sort_keys=True),
        )

    def test_brief_carries_user_requirements_and_instruction(self):
        brief = build_brief(
            plan=PLAN,
            best=checks(T1="met", T2="partial", S1="met"),
            best_version="R2",
            user_requirements="两页内，面向管理层",
        )

        self.assertEqual(brief["baseVersion"], "R2")
        self.assertEqual(brief["userRequirements"], "两页内，面向管理层")
        self.assertIn("以 baseVersion", brief["instruction"])
        self.assertIn("不得为了追求分数", brief["instruction"])


class CliTests(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        self.plan = self.root / "resolution-plan.json"
        self.plan.write_text(json.dumps(PLAN, ensure_ascii=False), encoding="utf-8")
        self.addCleanup(self._temp.cleanup)

    def write(self, name: str, payload: dict) -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def run_cli(self, *args: str) -> tuple[int, dict]:
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True, text=True, encoding="utf-8",
        )
        return completed.returncode, json.loads(completed.stdout)

    def test_cli_writes_the_brief_file(self):
        best = self.write("R1.json", {"checks": [
            {"id": "T1", "status": "met", "evidence": "ok"},
            {"id": "T2", "status": "partial", "evidence": "缺页码"},
            {"id": "S1", "status": "met", "evidence": "ok"},
        ]})
        output = self.root / "briefs" / "R2.json"

        code, payload = self.run_cli(
            "--plan", str(self.plan), "--best", str(best),
            "--best-version", "R1", "--output", str(output),
        )

        self.assertEqual(code, 0)
        self.assertEqual(payload["marker"], "REVISION_BRIEF_COMPLETED")
        self.assertEqual(payload["repair"], 1)
        brief = json.loads(output.read_text(encoding="utf-8"))
        self.assertEqual(brief["baseVersion"], "R1")

    def test_cli_merges_multiple_dimension_files(self):
        first = self.write("t.json", {"dimensionId": "traceability", "checks": [
            {"id": "T1", "status": "met", "evidence": "ok"},
            {"id": "T2", "status": "met", "evidence": "ok"},
        ]})
        second = self.write("s.json", {"dimensionId": "structure", "checks": [
            {"id": "S1", "status": "miss", "evidence": "缺结论"},
        ]})
        output = self.root / "R2.json"

        code, payload = self.run_cli(
            "--plan", str(self.plan),
            "--best", str(first), "--best", str(second),
            "--best-version", "R1", "--output", str(output),
        )

        self.assertEqual(code, 0)
        self.assertEqual(payload["repair"], 1)
        self.assertEqual(payload["preserve"], 2)

    def test_cli_requires_both_rejected_and_parent(self):
        best = self.write("R1.json", {"checks": [
            {"id": "T1", "status": "met", "evidence": "ok"},
            {"id": "T2", "status": "met", "evidence": "ok"},
            {"id": "S1", "status": "met", "evidence": "ok"},
        ]})

        code, payload = self.run_cli(
            "--plan", str(self.plan), "--best", str(best),
            "--best-version", "R1", "--rejected", str(best),
            "--output", str(self.root / "x.json"),
        )

        self.assertEqual(code, 1)
        self.assertEqual(payload["marker"], "REVISION_BRIEF_FAILED")

    def test_cli_reports_incomplete_judgment_instead_of_guessing(self):
        best = self.write("R1.json", {"checks": [
            {"id": "T1", "status": "met", "evidence": "ok"},
        ]})
        output = self.root / "x.json"

        code, payload = self.run_cli(
            "--plan", str(self.plan), "--best", str(best),
            "--best-version", "R1", "--output", str(output),
        )

        self.assertEqual(code, 1)
        self.assertIn("未覆盖", payload["error"])
        self.assertFalse(output.exists(), "失败时不应留下半份 brief")

    def test_cli_reports_unreadable_input(self):
        code, payload = self.run_cli(
            "--plan", str(self.root / "missing.json"),
            "--best", str(self.plan), "--best-version", "R1",
            "--output", str(self.root / "x.json"),
        )

        self.assertEqual(code, 1)
        self.assertEqual(payload["marker"], "REVISION_BRIEF_FAILED")


if __name__ == "__main__":
    unittest.main()


class PlanSchemaCompatibilityTests(unittest.TestCase):
    """冻结 Plan 用 statement 承载评判要求，不是 desc。

    这是一处真实出过的 bug：脚本只读 desc/requirement，而
    report-resolution-judge 的输出 Schema 里 check 只有 id/statement/redline。
    字段对不上时 requirement 会是空字符串、brief 结构完整、脚本成功退出——
    Writer 却不知道要改成什么。所以这里既测能读到 statement，也测读不到
    任何要求时必须报错而不是静默产出空 brief。
    """

    def plan_with(self, check: dict) -> dict:
        return {"dimensions": [
            {"id": "traceability", "weight": 1.0, "checks": [check]}
        ]}

    def test_statement_is_read_as_requirement(self):
        """Resolution Judge 的真实输出形态。"""
        plan = self.plan_with(
            {"id": "T1", "statement": "不得出现素材外的事实", "redline": True}
        )

        brief = build_brief(
            plan=plan,
            best={"T1": {"status": "miss", "evidence": "第三段有编造"}},
            best_version="R1",
        )

        self.assertEqual(brief["repair"][0]["requirement"], "不得出现素材外的事实")

    def test_desc_still_works_for_base_rubrics(self):
        plan = self.plan_with({"id": "T1", "desc": "引用与素材一致"})

        brief = build_brief(
            plan=plan,
            best={"T1": {"status": "partial", "evidence": "缺页码"}},
            best_version="R1",
        )

        self.assertEqual(brief["repair"][0]["requirement"], "引用与素材一致")

    def test_statement_wins_when_both_present(self):
        plan = self.plan_with(
            {"id": "T1", "statement": "本轮冻结要求", "desc": "Base 原文"}
        )

        brief = build_brief(
            plan=plan,
            best={"T1": {"status": "miss", "evidence": "x"}},
            best_version="R1",
        )

        self.assertEqual(brief["repair"][0]["requirement"], "本轮冻结要求")

    def test_check_without_any_requirement_is_rejected(self):
        """字段名对不上时必须报错，不能产出 requirement 为空的 brief。"""
        plan = self.plan_with({"id": "T1", "redline": True})

        with self.assertRaises(BriefError) as caught:
            build_brief(
                plan=plan,
                best={"T1": {"status": "miss", "evidence": "x"}},
                best_version="R1",
            )

        self.assertIn("T1", str(caught.exception))
        self.assertIn("评判要求", str(caught.exception))

    def test_preserve_entries_also_carry_the_requirement(self):
        plan = self.plan_with({"id": "T1", "statement": "不得出现素材外的事实"})

        brief = build_brief(
            plan=plan,
            best={"T1": {"status": "met", "evidence": "未发现编造"}},
            best_version="R1",
        )

        self.assertEqual(brief["preserve"][0]["requirement"], "不得出现素材外的事实")

    def test_resolution_judge_schema_documents_statement(self):
        """脚本读的字段必须与 Resolution Judge 声明的输出一致。"""
        schema = (ROOT / "agents/report-resolution-judge.md").read_text(encoding="utf-8")

        self.assertIn('"checks": [{"id":"...","statement":"...","redline":false}]', schema)
