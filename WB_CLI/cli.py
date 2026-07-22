from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .adapter import discover_command, infer_product_config
from .dataset import load_cases
from .models import BatchConfig
from .runner import BatchRunner


def _list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return tuple(str(item) for item in value)
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


def _paths(value: Any, base_dir: Path) -> tuple[Path, ...]:
    result = []
    for item in _list(value):
        path = Path(item).expanduser()
        result.append(path if path.is_absolute() else (base_dir / path).resolve())
    return tuple(result)


def _load_config(path: Path | None) -> tuple[dict[str, Any], Path]:
    if path is None:
        return {}, Path.cwd()
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(payload, dict):
        raise ValueError("配置文件必须是 JSON 对象")
    return payload, path.parent


def _coalesce(cli_value: Any, config: dict[str, Any], key: str, default: Any = None) -> Any:
    return cli_value if cli_value is not None else config.get(key, default)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m WB_CLI",
        description="并发运行 WorkBuddy headless 案例并记录多轮链路与产物",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="运行批量数据集")
    run.add_argument("--dataset", type=Path, required=True)
    run.add_argument("--config", type=Path)
    run.add_argument("--prompt", help="案例未提供 prompt 时使用的模板文本")
    run.add_argument("--prompt-file", type=Path)
    run.add_argument("--output", type=Path)
    run.add_argument("--run-id")
    run.add_argument("--cli-path")
    run.add_argument("--workbuddy-home", type=Path)
    run.add_argument("--product-config", type=Path)
    run.add_argument("--model")
    run.add_argument("--effort")
    run.add_argument("--parallel", type=int)
    run.add_argument("--repetition", type=int)
    run.add_argument("--workers", type=int, help=argparse.SUPPRESS)
    run.add_argument(
        "--timeout",
        type=float,
        help="单个 case（包含全部对话轮次）的总超时秒数，默认 900",
    )
    run.add_argument(
        "--stall-timeout",
        type=float,
        help="WorkBuddy 连续无任何输出时的截断秒数，默认 180",
    )
    run.add_argument("--progress-interval", type=float, help=argparse.SUPPRESS)
    run.add_argument("--launch-interval", type=float)
    run.add_argument("--skill", action="append")
    run.add_argument("--skill-path", action="append")
    run.add_argument("--plugin-dir", action="append")
    run.add_argument("--allowed-tool", action="append")
    run.add_argument("--disallowed-tool", action="append")
    run.add_argument("--tools", help="逗号分隔；传空字符串可禁用全部工具")
    run.add_argument("--permission-mode", help=argparse.SUPPRESS)
    run.add_argument(
        "--dangerously-skip-permissions",
        action="store_true",
        default=None,
        help=argparse.SUPPRESS,
    )
    run.add_argument("--max-turns", type=int)
    run.add_argument("--artifact-glob", action="append")
    run.add_argument("--append-system-prompt")
    native_session = run.add_mutually_exclusive_group()
    native_session.add_argument(
        "--no-native-session",
        dest="capture_native_session",
        action="store_false",
        default=None,
        help="不复制 WorkBuddy 原生 session",
    )
    native_session.add_argument(
        "--capture-native-session",
        dest="capture_native_session",
        action="store_true",
        default=None,
        help=argparse.SUPPRESS,
    )
    run.add_argument("--no-partial-messages", action="store_true", default=None)
    run.add_argument(
        "--allow-no-skill",
        action="store_true",
        default=None,
        help="仅用于 CLI/模型连通测试；允许案例不指定 Skill",
    )
    doctor = subparsers.add_parser(
        "doctor", help="只检查 CLI 发现与版本，不发模型请求"
    )
    doctor.add_argument("--cli-path")
    doctor.add_argument("--workbuddy-home", type=Path)
    doctor.add_argument("--product-config", type=Path)
    return parser


def _run(args: argparse.Namespace) -> int:
    config_payload, config_base = _load_config(args.config)
    explicit_command = _coalesce(args.cli_path, config_payload, "cli_path")
    if config_payload.get("command") and args.cli_path is None:
        raw_command = config_payload["command"]
        command = tuple(raw_command) if isinstance(raw_command, list) else (str(raw_command),)
    else:
        command = discover_command(explicit_command)
    product_config_value = _coalesce(
        args.product_config, config_payload, "product_config"
    )
    if product_config_value:
        product_config = Path(product_config_value).expanduser()
        if not product_config.is_absolute():
            product_config = (
                (Path.cwd() if args.product_config else config_base) / product_config
            ).resolve()
    else:
        product_config = infer_product_config(command)
    workbuddy_home_value = _coalesce(
        args.workbuddy_home,
        config_payload,
        "workbuddy_home",
        os.environ.get("WORKBUDDY_HOME", "~/.workbuddy"),
    )
    workbuddy_home = Path(workbuddy_home_value).expanduser() if workbuddy_home_value else None
    output = Path(
        _coalesce(args.output, config_payload, "output_root", "workbuddy_batch_runs")
    ).expanduser()
    prompt_template = args.prompt
    if args.prompt_file:
        prompt_template = args.prompt_file.read_text(encoding="utf-8")
    cases = load_cases(args.dataset.resolve(), prompt_template)
    tools_value = _coalesce(args.tools, config_payload, "tools")
    tools = None if tools_value is None else _list(tools_value)
    environment = config_payload.get("environment", {})
    if not isinstance(environment, dict):
        raise ValueError("config.environment 必须是对象")
    config = BatchConfig(
        command=command,
        output_root=output.resolve(),
        workbuddy_home=workbuddy_home,
        product_config=product_config,
        model=_coalesce(args.model, config_payload, "model"),
        effort=_coalesce(args.effort, config_payload, "effort"),
        parallel=int(
            args.parallel
            if args.parallel is not None
            else (
                args.workers
                if args.workers is not None
                else config_payload.get("parallel", config_payload.get("workers", 2))
            )
        ),
        repetition=int(
            _coalesce(args.repetition, config_payload, "repetition", 1)
        ),
        timeout_seconds=float(
            _coalesce(args.timeout, config_payload, "timeout_seconds", 900)
        ),
        stall_timeout_seconds=float(
            _coalesce(
                args.stall_timeout,
                config_payload,
                "stall_timeout_seconds",
                180,
            )
        ),
        launch_interval_seconds=float(
            _coalesce(
                args.launch_interval,
                config_payload,
                "launch_interval_seconds",
                0,
            )
        ),
        tools=tools,
        allowed_tools=tuple(
            args.allowed_tool
            if args.allowed_tool is not None
            else _list(config_payload.get("allowed_tools"))
        ),
        disallowed_tools=tuple(
            args.disallowed_tool
            if args.disallowed_tool is not None
            else _list(config_payload.get("disallowed_tools"))
        ),
        max_turns=_coalesce(args.max_turns, config_payload, "max_turns"),
        setting_sources=config_payload.get("setting_sources", "user,project,local"),
        include_partial_messages=(
            False
            if args.no_partial_messages
            else bool(config_payload.get("include_partial_messages", True))
        ),
        verbose=bool(config_payload.get("verbose", True)),
        capture_native_session=bool(
            _coalesce(
                args.capture_native_session,
                config_payload,
                "capture_native_session",
                True,
            )
        ),
        require_skill=(
            False
            if args.allow_no_skill
            else bool(config_payload.get("require_skill", True))
        ),
        skills=tuple(
            args.skill if args.skill is not None else _list(config_payload.get("skills"))
        ),
        skill_paths=(
            tuple(Path(item).expanduser().resolve() for item in args.skill_path)
            if args.skill_path is not None
            else _paths(config_payload.get("skill_paths"), config_base)
        ),
        plugin_dirs=(
            tuple(Path(item).expanduser().resolve() for item in args.plugin_dir)
            if args.plugin_dir is not None
            else _paths(config_payload.get("plugin_dirs"), config_base)
        ),
        append_system_prompt=str(
            _coalesce(
                args.append_system_prompt,
                config_payload,
                "append_system_prompt",
                "",
            )
        ),
        artifact_globs=tuple(
            args.artifact_glob
            if args.artifact_glob is not None
            else _list(config_payload.get("artifact_globs"))
        ),
        environment={str(key): str(value) for key, value in environment.items()},
    )
    run_dir = BatchRunner(config).run(cases, args.run_id)
    print(f"Run complete: {run_dir}")
    return 0


def _doctor(args: argparse.Namespace) -> int:
    command = discover_command(args.cli_path)
    product_config = args.product_config or infer_product_config(command)
    completed = subprocess.run(
        [*command, "--version"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    payload = {
        "command": list(command),
        "version": completed.stdout.strip(),
        "return_code": completed.returncode,
        "workbuddy_home": str((args.workbuddy_home or Path("~/.workbuddy")).expanduser()),
        "product_config": str(product_config) if product_config else None,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if completed.returncode == 0 else 1


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args) if args.command == "run" else _doctor(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
