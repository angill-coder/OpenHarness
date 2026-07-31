#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Public workflow API and CLI for dataset preparation and quality auditing.

The public surface intentionally lives in this one module.  The sizeable
preparation and audit implementations remain private so callers do not need to
coordinate three separate scripts.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Sequence

try:
    from . import _data_prepare
    from ._data_audit import (
        CodexJsonRunner,
        DataQualityError,
        DataQualityCancelled,
        DataQualityRequest,
        DataQualityBatchResult,
        run_data_quality,
    )
except ImportError:  # Allow ``python harness/data_workflow.py ...``.
    import _data_prepare
    from _data_audit import (
        CodexJsonRunner,
        DataQualityError,
        DataQualityCancelled,
        DataQualityRequest,
        DataQualityBatchResult,
        run_data_quality,
    )


# Workflow-oriented public names.  The legacy aliases above remain importable
# from this module for callers that are migrating incrementally.
DataWorkflowRequest = DataQualityRequest
DataWorkflowResult = DataQualityBatchResult


def run_data_workflow(
    request: DataWorkflowRequest,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> DataWorkflowResult:
    """Run Metadata -> Audit -> optional Repair for one or more cases."""

    return run_data_quality(
        request,
        progress_callback=progress_callback,
        should_cancel=should_cancel,
    )


def _add_audit_arguments(parser: argparse.ArgumentParser) -> None:
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument("--dataset", type=Path, help="OpenHarness data.json")
    inputs.add_argument(
        "--source",
        action="append",
        type=Path,
        help="Standalone 原始文件或目录，可重复",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        help="dataset 模式可重复筛选；Standalone 模式只允许一个",
    )
    parser.add_argument("--groundtruth", type=Path, help="Standalone groundtruth 文件")
    parser.add_argument("--background", default="", help="Standalone 研究背景")
    parser.add_argument("--metadata", type=Path, help="复用已有 Metadata")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="只生成 Metadata，不运行质检",
    )
    parser.add_argument(
        "--repair-metadata",
        action="store_true",
        help="质检后回查 source，补充 Metadata 抽取遗漏（3.1）",
    )
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--retries", type=int, default=1)
    parser.add_argument("--force-metadata", action="store_true")
    parser.add_argument("--force-audit", action="store_true")
    parser.add_argument("--force-repair", action="store_true")
    parser.add_argument(
        "--publish-metadata",
        action="store_true",
        help="同时把 Metadata 发布到 OpenHarness case 目录",
    )
    parser.add_argument("--codex-cli", default="codex")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="OpenHarness 数据准备、清洗与质检 workflow"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    prepare = commands.add_parser(
        "prepare",
        help="生成原子 case 或合并为 data.json",
        add_help=False,
    )
    prepare.add_argument(
        "prepare_args",
        nargs=argparse.REMAINDER,
        help="传给 prepare 引擎：generate / generate-projects / merge",
    )

    audit = commands.add_parser(
        "audit",
        help="运行 Metadata 与质量审计",
    )
    _add_audit_arguments(audit)

    run = commands.add_parser(
        "run",
        help="运行完整质量 workflow（Metadata -> Audit -> 可选 Repair）",
    )
    _add_audit_arguments(run)
    return parser


def _run_audit_command(args: argparse.Namespace) -> int:
    case_ids = tuple(args.case_id or ())
    if args.source and len(case_ids) != 1:
        print("error: Standalone 模式必须且只能提供一个 --case-id", file=sys.stderr)
        return 2
    if args.metadata_only and args.repair_metadata:
        print("error: --metadata-only 不能与 --repair-metadata 同时使用", file=sys.stderr)
        return 2
    try:
        stages = (
            ("metadata",)
            if args.metadata_only
            else (
                ("metadata", "audit", "repair")
                if args.repair_metadata
                else ("metadata", "audit")
            )
        )
        request = DataWorkflowRequest(
            output_root=args.output,
            dataset=args.dataset,
            case_ids=case_ids if args.dataset else (),
            source_paths=tuple(args.source or ()),
            groundtruth=args.groundtruth,
            case_id=case_ids[0] if args.source else None,
            background=args.background,
            metadata=args.metadata,
            stages=stages,
            model=args.model,
            effort=args.effort,
            parallel=args.parallel,
            timeout_seconds=args.timeout,
            retries=args.retries,
            force_metadata=args.force_metadata,
            force_audit=args.force_audit,
            force_repair=args.force_repair,
            publish_metadata=args.publish_metadata,
            codex_command=(args.codex_cli,),
        )
        result = run_data_workflow(request)
    except (DataQualityError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    print(f"Data-quality artifacts: {result.output_root}")
    return 0 if result.succeeded else 1


def main(argv: Sequence[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    # Delegate before parsing so the preparation engine retains its complete
    # nested help and argument validation without duplicating its large parser.
    if raw_args and raw_args[0] == "prepare":
        return _data_prepare.main(raw_args[1:])
    args = _parser().parse_args(raw_args)
    return _run_audit_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
