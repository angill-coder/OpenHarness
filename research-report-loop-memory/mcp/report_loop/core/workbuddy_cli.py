"""Isolated WorkBuddy CLI client used only by the Report Loop Judge."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "deepseek-v4-flash-ioa"
DEFAULT_EFFORT = "medium"
MAC_WORKBUDDY_CLI = Path(
    "/Applications/WorkBuddy.app/Contents/Resources/"
    "app.asar.unpacked/cli/bin/codebuddy"
)


class WorkBuddyError(RuntimeError):
    """WorkBuddy CLI discovery, execution, or response error."""


def _windows_desktop_command(path: Path | None = None) -> tuple[str, ...] | None:
    if os.name != "nt":
        return None
    candidates: list[Path] = []
    if path:
        expanded = path.expanduser()
        if expanded.name.lower() == "workbuddy.exe":
            candidates.append(expanded.parent)
        elif expanded.name.lower() == "codebuddy":
            try:
                candidates.append(expanded.parents[4])
            except IndexError:
                pass
    local_app_data = os.environ.get("LOCALAPPDATA")
    candidates.append(Path.home() / "WorkBuddy")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "WorkBuddy")
    for root in candidates:
        executable = root / "WorkBuddy.exe"
        cli = root / "resources" / "app.asar.unpacked" / "cli" / "bin" / "codebuddy"
        if executable.is_file() and cli.is_file():
            return (str(executable), str(cli))
    return None


def discover_command(explicit: str | None = None) -> tuple[str, ...]:
    value = explicit or os.environ.get("RESEARCH_REPORT_LOOP_WB_CLI_PATH") or os.environ.get("WORKBUDDY_CLI")
    if value:
        path = Path(value).expanduser()
        return _windows_desktop_command(path) or (str(path),)
    workbuddy = shutil.which("workbuddy")
    if workbuddy:
        return (workbuddy,)
    if MAC_WORKBUDDY_CLI.exists():
        return (str(MAC_WORKBUDDY_CLI),)
    windows = _windows_desktop_command()
    if windows:
        return windows
    for name in ("codebuddy", "cbc"):
        candidate = shutil.which(name)
        if candidate:
            return (candidate,)
    raise WorkBuddyError(
        "找不到 WorkBuddy CLI；请设置 RESEARCH_REPORT_LOOP_WB_CLI_PATH 或 WORKBUDDY_CLI"
    )


def _environment(command: tuple[str, ...]) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(
        {
            "CODEBUDDY_DISABLE_AUTO_MEMORY": "1",
            "CODEBUDDY_MEMORY_RELEVANCE_DISABLED": "1",
            "CODEBUDDY_MEMORY_EXTRACTION_DISABLED": "1",
            "CODEBUDDY_TEAM_MEMORY_ENABLED": "0",
        }
    )
    if (
        len(command) > 1
        and Path(command[0]).name.lower() == "workbuddy.exe"
        and Path(command[1]).name.lower() == "codebuddy"
    ):
        environment["ELECTRON_RUN_AS_NODE"] = "1"
    configured_home = os.environ.get("RESEARCH_REPORT_LOOP_WB_HOME")
    if configured_home:
        environment["CODEBUDDY_CONFIG_DIR"] = str(Path(configured_home).expanduser())
    configured_product = os.environ.get("RESEARCH_REPORT_LOOP_WB_PRODUCT_CONFIG")
    if configured_product:
        environment["ACC_PRODUCT_CONFIG_PATH"] = str(Path(configured_product).expanduser())
    return environment


def parse_stream_output(raw: str) -> str:
    """Collect assistant text from WorkBuddy stream-json output."""
    parts: list[str] = []
    result_text = ""
    for line in (raw or "").splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "assistant":
            message = event.get("message") or {}
            content = message.get("content") if isinstance(message, dict) else []
            if isinstance(content, list):
                parts.extend(
                    str(item.get("text"))
                    for item in content
                    if isinstance(item, dict)
                    and item.get("type") == "text"
                    and item.get("text")
                )
        if event.get("type") == "result" and isinstance(event.get("result"), str):
            result_text = event["result"].strip()
    text = "\n".join(parts).strip() or result_text
    if text.startswith("Authentication required"):
        raise WorkBuddyError("WorkBuddy CLI 未登录；请先在 WorkBuddy 中完成登录")
    if not text:
        raise WorkBuddyError("WorkBuddy CLI 未返回可用的 Judge 文本")
    return text


def call_workbuddy(
    prompt: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    timeout_seconds: float | None = None,
) -> str:
    command = discover_command()
    selected_model = str(
        model
        or os.environ.get("RESEARCH_REPORT_LOOP_WB_MODEL")
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_MODEL")
        or DEFAULT_MODEL
    ).strip()
    selected_effort = str(
        effort
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_EFFORT")
        or DEFAULT_EFFORT
    ).strip()
    timeout = float(
        timeout_seconds
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_TIMEOUT", "900")
    )
    args = [
        *command,
        "-p",
        "--output-format",
        "stream-json",
        "--model",
        selected_model,
        "--permission-mode",
        "bypassPermissions",
        "--tools",
        "",
        "--setting-sources",
        "",
        "--no-session-persistence",
        "--max-turns",
        "1",
        "--effort",
        selected_effort,
        prompt,
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args,
            cwd=tempfile.gettempdir(),
            env=_environment(command),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise WorkBuddyError("WorkBuddy Judge 调用超时") from exc
    except OSError as exc:
        raise WorkBuddyError(f"无法启动 WorkBuddy CLI: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()[-1200:]
        raise WorkBuddyError(
            f"WorkBuddy CLI 返回退出码 {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    text = parse_stream_output(completed.stdout)
    if not text:
        raise WorkBuddyError(
            f"WorkBuddy Judge 在 {time.monotonic() - started:.1f}s 后返回空结果"
        )
    return text


def _balanced_json_span(text: str) -> str | None:
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_json(text: str) -> dict[str, Any] | None:
    candidates = [
        match.group(1).strip()
        for match in re.finditer(r"```(?:json)?\s*(.*?)```", text or "", re.S)
        if match.group(1).strip()
    ]
    balanced = _balanced_json_span(text or "")
    if balanced:
        candidates.append(balanced)
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return None
