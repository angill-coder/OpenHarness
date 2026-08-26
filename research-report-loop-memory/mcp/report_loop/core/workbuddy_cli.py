"""Isolated WorkBuddy CLI client used only by the Report Loop Judge."""

from __future__ import annotations

import json
import os
import re
import shutil
import string
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


DEFAULT_MODEL = "deepseek-v4-pro-ioa"
DEFAULT_EFFORT = "medium"
MAC_CLI_SUFFIX = Path("Contents/Resources/app.asar.unpacked/cli/bin/codebuddy")


class WorkBuddyError(RuntimeError):
    """WorkBuddy CLI discovery, execution, or response error."""


_SAFE_ENVIRONMENT_KEYS = {
    "HOME",
    "USER",
    "LOGNAME",
    "USERPROFILE",
    "PATH",
    "SHELL",
    "TMPDIR",
    "TEMP",
    "TMP",
    "LANG",
    "LANGUAGE",
    "LC_ALL",
    "LC_CTYPE",
    "SystemRoot",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "APPDATA",
    "LOCALAPPDATA",
    "WORKBUDDY_EXTRA_PATHS",
    "WORKBUDDY_CONFIG_DIR",
    "CODEBUDDY_CONFIG_DIR",
    "CODEBUDDY_CODE_PATH",
    "CODEBUDDY_CODE_NODE_PATH",
    "CODEBUDDY_NODE_BIN",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "NODE_EXTRA_CA_CERTS",
    "REQUESTS_CA_BUNDLE",
}


def _windows_node_command(cli: Path) -> tuple[str, ...] | None:
    for key in ("CODEBUDDY_CODE_NODE_PATH", "CODEBUDDY_NODE_BIN", "WORKBUDDY_NODE"):
        value = os.environ.get(key)
        if value and Path(value).expanduser().is_file():
            return (str(Path(value).expanduser()), str(cli))
    for directory in os.environ.get("WORKBUDDY_EXTRA_PATHS", "").split(os.pathsep):
        candidate = Path(directory) / "node.exe" if directory else None
        if candidate and candidate.is_file():
            return (str(candidate), str(cli))
    node = shutil.which("node.exe") or shutil.which("node")
    return (node, str(cli)) if node else None


def _windows_desktop_command(path: Path | None = None) -> tuple[str, ...] | None:
    if os.name != "nt":
        return None
    candidates: list[Path] = []
    if path:
        expanded = path.expanduser()
        if expanded.name.lower() == "workbuddy.exe":
            candidates.append(expanded.parent)
        elif expanded.name.lower() in {"codebuddy", "codebuddy.cmd", "codebuddy.exe"}:
            try:
                candidates.append(expanded.parents[4])
            except IndexError:
                pass
    local_app_data = os.environ.get("LOCALAPPDATA")
    candidates.append(Path.home() / "WorkBuddy")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "WorkBuddy")
    for key in ("ProgramFiles", "ProgramFiles(x86)"):
        value = os.environ.get(key)
        if value:
            candidates.append(Path(value) / "WorkBuddy")
    # Enterprise installs commonly keep the standard Program Files layout on a
    # non-system drive. This is a bounded 26-path probe, not a filesystem scan.
    for drive in string.ascii_uppercase:
        candidates.append(Path(f"{drive}:\\Program Files\\WorkBuddy"))
    for root in candidates:
        executable = root / "WorkBuddy.exe"
        cli = root / "resources" / "app.asar.unpacked" / "cli" / "bin" / "codebuddy"
        if executable.is_file() and cli.is_file():
            return (str(executable), str(cli))
    if path and path.expanduser().is_file() and path.name.lower().startswith("codebuddy"):
        return _windows_node_command(path.expanduser())
    return None


def _mac_workbuddy_command(app_roots: list[Path] | None = None) -> tuple[str, ...] | None:
    if app_roots is None:
        if sys.platform != "darwin":
            return None
        app_roots = [Path("/Applications/WorkBuddy.app"), Path.home() / "Applications/WorkBuddy.app"]
        volumes = Path("/Volumes")
        if volumes.is_dir():
            app_roots.extend(volumes.glob("*/Applications/WorkBuddy.app"))
    for app_root in app_roots:
        cli = app_root / MAC_CLI_SUFFIX
        if cli.is_file() and os.access(cli, os.X_OK):
            return (str(cli),)
    return None


def discover_command(explicit: str | None = None) -> tuple[str, ...]:
    values = [
        explicit,
        os.environ.get("RESEARCH_REPORT_LOOP_WB_CLI_PATH"),
        os.environ.get("WORKBUDDY_CLI"),
        os.environ.get("WORKBUDDY_CODEBUDDY"),
        os.environ.get("CODEBUDDY_CODE_PATH"),
        os.environ.get("WORKBUDDY_DESKTOP_EXE") if os.name == "nt" else None,
    ]
    for value in values:
        if value:
            path = Path(value).expanduser()
            return _windows_desktop_command(path) or (str(path),)
    workbuddy = shutil.which("workbuddy")
    if workbuddy:
        return (workbuddy,)
    # Prefer the standalone Windows CLI over launching the desktop executable
    # as a nested Node host. Concurrent desktop-hosted CLI processes may start
    # the same local ACP listener before they ever submit a model request.
    if os.name == "nt":
        for name in ("codebuddy", "cbc"):
            candidate = shutil.which(name)
            if candidate:
                return (candidate,)
    windows = _windows_desktop_command()
    if windows:
        return windows
    mac = _mac_workbuddy_command()
    if mac:
        return mac
    if os.name != "nt":
        for name in ("codebuddy", "cbc"):
            candidate = shutil.which(name)
            if candidate:
                return (candidate,)
    raise WorkBuddyError(
        "已启动 Report Loop，但未能定位 WorkBuddy 模型调用入口；"
        "请检查宿主是否提供 CODEBUDDY_CODE_PATH，或设置 "
        "RESEARCH_REPORT_LOOP_WB_CLI_PATH"
    )


def build_environment(command: tuple[str, ...]) -> dict[str, str]:
    # The Runner is started by a WorkBuddy host Hook. Inheriting the full App
    # environment would leak its internal gateway, service-proxy, PAC and daemon
    # context into a nested CLI. Such a CLI can start and open sockets but never
    # submit a model request. Build a normal user CLI environment instead.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key in _SAFE_ENVIRONMENT_KEYS or key.startswith("RESEARCH_REPORT_LOOP_")
    }
    config_dir = str(
        Path(
            os.environ.get("RESEARCH_REPORT_LOOP_WB_HOME")
            or os.environ.get("WORKBUDDY_CONFIG_DIR")
            or os.environ.get("CODEBUDDY_CONFIG_DIR")
            or (Path.home() / ".workbuddy")
        ).expanduser()
    )
    environment.update(
        {
            "CODEBUDDY_CONFIG_DIR": config_dir,
            "WORKBUDDY_CONFIG_DIR": config_dir,
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
    configured_product = os.environ.get("RESEARCH_REPORT_LOOP_WB_PRODUCT_CONFIG")
    if not configured_product and command:
        cli = Path(command[-1])
        candidate = cli.parent.parent / "product.json"
        if cli.name.lower().startswith("codebuddy") and candidate.is_file():
            configured_product = str(candidate)
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
    configured_timeout = float(
        os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_TIMEOUT", "900")
    )
    requested_timeout = (
        float(timeout_seconds)
        if timeout_seconds is not None
        else configured_timeout
    )
    # Keep the Report Loop budget as an outer deadline, but never let one
    # Judge consume more than the configured per-call timeout before fallback.
    timeout = min(requested_timeout, configured_timeout)
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
    ]
    started = time.monotonic()
    try:
        completed = subprocess.run(
            args,
            cwd=tempfile.gettempdir(),
            env=build_environment(command),
            input=prompt,
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
