#!/usr/bin/env python3
"""从 Judgment 确定性地生成 Revision Brief。

为什么用脚本而不是让主理人手工提炼
----------------------------------
Writer 只能看到 `revisionBrief`，看不到原始 Judge 输出——这是刻意的信息屏障，
防止改写去迎合打分细节（reward hacking）。但屏障只有在 brief 内容**确定性**
时才可靠：如果 brief 由主理人凭理解提炼，实际传给 Writer 的东西就随每次归纳
而变，同一份 Judgment 可能得到宽严不一的改写指令。

所以这里把「哪些 Check 进 repair / preserve / avoid」做成纯函数：同样的
Judgment 必然得到同样的 brief。主理人仍负责调度与交付判断，但不再需要自己
挑选和改写 Judge 的原文。

判定规则（与冻结 Plan 一致，不引入新标准）：

* ``repair``   —— 历史最佳里非 ``met`` 的 Check，附维度、要求、状态、原因
* ``preserve`` —— 历史最佳里 ``met`` 的 Check，附已达成的要求
* ``avoid``    —— 被拒绝候选相对其父版本**发生回退**的 Check
* ``userOverrides`` —— 因用户明确要求而豁免的 Check（reason 以
  ``user_override:`` 开头），单独列出，避免 Writer 为追分反改用户指定形式

用法::

    python3 resources/report/revision_brief.py \\
        --plan  <评测与改写记录/resolution-plan.json> \\
        --best  <评测与改写记录/judgments/R1.json> \\
        --best-version R1 \\
        --user-requirements "用户确认的本轮要求" \\
        --output <评测与改写记录/revision-briefs/R2.json> \\
        [--rejected <被拒候选 judgment>] [--rejected-parent <其父版本 judgment>]

``--best`` / ``--rejected`` 接受两种形态：单维 Judge 写出的结果文件（含
``dimensionId`` 与 ``checks`` 数组），或主理人已聚合的 Judgment（``checks``
为 id→status 映射）。目录里有多份单维文件时可重复传入 ``--best``。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

_RANK = {"met": 1.0, "partial": 0.5, "miss": 0.0}
_USER_OVERRIDE = re.compile(r"^\s*user_override\s*[:：]", re.I)


class BriefError(RuntimeError):
    """输入不完整或格式不符；调用方应如实报告而不是生成半份 brief。"""


def _load(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BriefError(f"无法读取 {path}：{exc}") from exc


def normalize_checks(documents: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """把单维结果或聚合 Judgment 统一成 {checkId: {status, evidence}}。"""
    merged: dict[str, dict[str, str]] = {}
    for document in documents:
        checks = document.get("checks")
        if isinstance(checks, list):
            for item in checks:
                check_id = str(item.get("id") or "").strip()
                if not check_id:
                    continue
                merged[check_id] = {
                    "status": str(item.get("status") or ""),
                    "evidence": str(item.get("evidence") or ""),
                }
        elif isinstance(checks, dict):
            reasoning = document.get("reasoning") or {}
            for check_id, status in checks.items():
                merged[str(check_id)] = {
                    "status": str(status),
                    "evidence": str(reasoning.get(check_id) or ""),
                }
        else:
            raise BriefError("Judgment 缺少 checks（应为数组或映射）")
    if not merged:
        raise BriefError("Judgment 未包含任何 Check")
    return merged


def plan_definitions(plan: dict[str, Any]) -> dict[str, dict[str, str]]:
    """从冻结 Plan 取每条 Check 的维度、标签与要求原文。"""
    definitions: dict[str, dict[str, str]] = {}
    dimensions = plan.get("dimensions")
    if not isinstance(dimensions, list) or not dimensions:
        raise BriefError("冻结 Plan 缺少 dimensions[]")
    for dimension in dimensions:
        dimension_id = str(dimension.get("id") or dimension.get("name") or "")
        for check in dimension.get("checks") or []:
            check_id = str(check.get("id") or "").strip()
            if not check_id:
                continue
            definitions[check_id] = {
                "checkId": check_id,
                "dimension": dimension_id,
                "label": str(check.get("label") or check_id),
                # 冻结 Plan 的 Check 用 statement 承载评判要求（见
                # report-resolution-judge 的输出 Schema）；desc / requirement
                # 是 Base Rubrics 与聚合 Judgment 里的别名，一并接受。
                "requirement": str(
                    check.get("statement")
                    or check.get("desc")
                    or check.get("requirement")
                    or ""
                ),
            }
    if not definitions:
        raise BriefError("冻结 Plan 的 dimensions[] 未包含任何 Check")
    # requirement 是 Writer 唯一能看到的"要改成什么"。字段名对不上时它会是空
    # 字符串，brief 结构仍然完整、脚本仍然成功——这种静默失效比报错更糟。
    missing = sorted(k for k, v in definitions.items() if not v["requirement"])
    if missing:
        raise BriefError(
            "冻结 Plan 中以下 Check 缺少评判要求（statement/desc/requirement 均为空）："
            + "、".join(missing)
        )
    return definitions


def build_brief(
    *,
    plan: dict[str, Any],
    best: dict[str, dict[str, str]],
    best_version: str,
    user_requirements: str = "",
    rejected: dict[str, dict[str, str]] | None = None,
    rejected_parent: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    definitions = plan_definitions(plan)
    repair: list[dict[str, Any]] = []
    preserve: list[dict[str, Any]] = []
    user_overrides: list[dict[str, Any]] = []

    for check_id, definition in definitions.items():
        observed = best.get(check_id)
        if observed is None:
            # Plan 里有但 Judgment 没覆盖：属于评测不完整，不静默当作已达成
            raise BriefError(f"历史最佳 Judgment 未覆盖 Check {check_id}")
        status = observed["status"]
        evidence = observed["evidence"]
        if status == "met":
            if _USER_OVERRIDE.match(evidence):
                user_overrides.append({**definition, "reason": evidence})
            else:
                preserve.append(definition)
        else:
            repair.append({**definition, "status": status, "reason": evidence})

    avoid: list[dict[str, Any]] = []
    if rejected and rejected_parent:
        for check_id, prior in rejected_parent.items():
            prior_rank = _RANK.get(prior["status"])
            current = rejected.get(check_id)
            current_rank = _RANK.get(current["status"]) if current else None
            if prior_rank is None or current_rank is None:
                continue
            if current_rank >= prior_rank:
                continue
            avoid.append({
                **definitions.get(check_id, {"checkId": check_id}),
                "regressedTo": current["status"],
                "reason": current["evidence"] or "候选版本引入回退",
            })

    return {
        "baseVersion": best_version,
        "repair": repair,
        "preserve": preserve,
        "avoid": avoid,
        "userRequirements": user_requirements,
        "userOverrides": user_overrides,
        "instruction": (
            "以 baseVersion 对应的历史最佳报告为唯一基线；优先修复 repair，"
            "保持 preserve，避免重新引入 avoid；只做与修复目标有关的修改。"
            "必须继续遵守 userRequirements；userOverrides 中的 Check 已因用户"
            "明确要求豁免，不得为了追求分数反向修改用户指定的结构、表达或交付形式。"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="revision_brief.py",
        description="从 Judgment 确定性生成 Revision Brief（不含 Judge 原文）",
    )
    parser.add_argument("--plan", required=True, help="冻结 resolution-plan.json")
    parser.add_argument("--best", required=True, action="append",
                        help="历史最佳的 Judgment 或单维结果，可重复")
    parser.add_argument("--best-version", required=True, help="历史最佳候选标识，如 R1")
    parser.add_argument("--user-requirements", default="", help="用户确认的本轮要求")
    parser.add_argument("--rejected", action="append", default=[],
                        help="被拒候选的 Judgment 或单维结果，可重复")
    parser.add_argument("--rejected-parent", action="append", default=[],
                        help="被拒候选父版本的 Judgment，可重复")
    parser.add_argument("--output", required=True, help="brief 输出绝对路径")
    args = parser.parse_args(argv)

    try:
        plan = _load(Path(args.plan))
        best = normalize_checks([_load(Path(item)) for item in args.best])
        rejected = (
            normalize_checks([_load(Path(item)) for item in args.rejected])
            if args.rejected else None
        )
        parent = (
            normalize_checks([_load(Path(item)) for item in args.rejected_parent])
            if args.rejected_parent else None
        )
        if bool(rejected) != bool(parent):
            raise BriefError("--rejected 与 --rejected-parent 必须同时提供")
        brief = build_brief(
            plan=plan,
            best=best,
            best_version=args.best_version,
            user_requirements=args.user_requirements,
            rejected=rejected,
            rejected_parent=parent,
        )
    except BriefError as exc:
        print(json.dumps(
            {"marker": "REVISION_BRIEF_FAILED", "error": str(exc)},
            ensure_ascii=False, indent=2,
        ))
        return 1

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".brief.tmp")
    temporary.write_text(
        json.dumps(brief, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(output)
    print(json.dumps({
        "marker": "REVISION_BRIEF_COMPLETED",
        "path": str(output.resolve()),
        "baseVersion": brief["baseVersion"],
        "repair": len(brief["repair"]),
        "preserve": len(brief["preserve"]),
        "avoid": len(brief["avoid"]),
        "userOverrides": len(brief["userOverrides"]),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
