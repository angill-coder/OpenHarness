#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从命令行通过 OpenHarness Runner 生成真实报告。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import runner as runner_mod
from external_run_models import ExternalRunRequest, ReportOutputContract
from workbuddy_runner import ExternalRunConfigurationError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="通过 OpenHarness Runner 调用 WorkBuddy 批量生成真实报告"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("generation_runs"))
    parser.add_argument("--session-id")
    parser.add_argument("--version", default="fixed-research-report")
    skill = parser.add_mutually_exclusive_group()
    skill.add_argument(
        "--skill",
        help="已安装的 WorkBuddy Skill 名称；默认 research-report",
    )
    skill.add_argument(
        "--skill-path",
        type=Path,
        help="本地文件型 Skill 目录；正式版本化执行优先使用此参数",
    )
    parser.add_argument("--model")
    parser.add_argument("--effort")
    parser.add_argument("--parallel", type=int, default=20)
    parser.add_argument(
        "--repetition",
        type=int,
        default=1,
        help="兼容参数，只允许 1；条件重试由 Runner 控制",
    )
    parser.add_argument("--max-report-retries", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--stall-timeout", type=float, default=180)
    parser.add_argument(
        "--required-report",
        default="deliverables/report.md",
    )
    parser.add_argument(
        "--allowed-extension",
        action="append",
        help="允许的报告扩展名，可重复；默认 .md",
    )
    parser.add_argument("--min-report-bytes", type=int, default=500)
    parser.add_argument(
        "--material-root",
        action="append",
        type=Path,
        help="允许读取的材料根目录，可重复；默认 dataset 所在目录",
    )
    parser.add_argument(
        "--case-map",
        type=Path,
        help="可选 WB case ID → OpenHarness case ID/split JSON 映射",
    )
    parser.add_argument("--cli-path")
    parser.add_argument("--workbuddy-home", type=Path)
    parser.add_argument("--product-config", type=Path)
    parser.add_argument("--allowed-tool", action="append")
    parser.add_argument("--disallowed-tool", action="append")
    return parser


def _load_case_map(path: Path | None) -> dict[str, dict[str, str]]:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("case-map 必须是 JSON 对象")
    result = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            raise ValueError(f"case-map.{key} 必须是对象")
        result[str(key)] = {
            str(field): str(item)
            for field, item in value.items()
            if field in {"openharness_case_id", "split"}
        }
    return result


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.repetition != 1:
            raise ExternalRunConfigurationError(
                "OpenHarness 外部执行固定 repetition=1；"
                "请用 --max-report-retries 设置条件重试"
            )
        request = ExternalRunRequest(
            case_file=args.dataset,
            output_root=args.output,
            skill_version=args.version,
            session_id=args.session_id,
            skill_name=(
                None
                if args.skill_path
                else (args.skill or "research-report")
            ),
            skill_path=args.skill_path,
            model=args.model,
            effort=args.effort,
            parallel=args.parallel,
            timeout_seconds=args.timeout,
            stall_timeout_seconds=args.stall_timeout,
            max_report_retries=args.max_report_retries,
            output_contract=ReportOutputContract(
                required_glob=args.required_report,
                allowed_extensions=tuple(
                    args.allowed_extension or [".md"]
                ),
                min_bytes=args.min_report_bytes,
                max_files=1,
            ),
            command=(args.cli_path,) if args.cli_path else None,
            workbuddy_home=args.workbuddy_home,
            product_config=args.product_config,
            allowed_material_roots=tuple(args.material_root or ()),
            allowed_tools=tuple(args.allowed_tool or ()),
            disallowed_tools=tuple(args.disallowed_tool or ()),
            case_map=_load_case_map(args.case_map),
        )
        result = runner_mod.run_external_cases(request)
    except (ExternalRunConfigurationError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(
        json.dumps(
            result.to_dict(include_report_text=False),
            ensure_ascii=False,
            indent=2,
        )
    )
    print(f"Generation result: {result.output_dir}")
    return 0 if result.succeeded else 1


if __name__ == "__main__":
    raise SystemExit(main())
