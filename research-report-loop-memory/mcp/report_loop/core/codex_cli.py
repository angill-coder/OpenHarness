"""Isolated Codex CLI client used only by the Report Loop Judge."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path


DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_EFFORT = "medium"
SUPPORTED_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")
MAC_CODEX_CLI = Path("/Applications/ChatGPT.app/Contents/Resources/codex")


class CodexError(RuntimeError):
    """Codex CLI discovery, execution, or response error."""


def discover_command(explicit: str | None = None) -> tuple[str, ...]:
    value = (
        explicit
        or os.environ.get("RESEARCH_REPORT_LOOP_CODEX_CLI_PATH")
        or os.environ.get("OPENHARNESS_CODEX_CLI_PATH")
    )
    if value:
        path = Path(value).expanduser()
        if not path.is_file():
            raise CodexError(f"Codex CLI 不存在: {path}")
        return (str(path),)
    executable = shutil.which("codex")
    if executable:
        return (executable,)
    if MAC_CODEX_CLI.is_file():
        return (str(MAC_CODEX_CLI),)
    raise CodexError(
        "找不到 Codex CLI；请安装 Codex，或设置 RESEARCH_REPORT_LOOP_CODEX_CLI_PATH"
    )


def call_codex(
    prompt: str,
    *,
    model: str | None = None,
    effort: str | None = None,
    timeout_seconds: float | None = None,
) -> str:
    """Run a stateless, rule-free Codex Judge and return its final text."""
    command = discover_command()
    selected_model = str(
        model
        or os.environ.get("RESEARCH_REPORT_LOOP_CODEX_MODEL")
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_MODEL")
        or DEFAULT_MODEL
    ).strip()
    selected_effort = str(
        effort
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_EFFORT")
        or DEFAULT_EFFORT
    ).strip().lower()
    if selected_effort not in SUPPORTED_EFFORTS:
        raise CodexError(
            "不支持的 Codex 推理力度: "
            f"{selected_effort}；可选值为 {', '.join(SUPPORTED_EFFORTS)}"
        )
    timeout = float(
        timeout_seconds
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_TIMEOUT", "900")
    )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="report-loop-codex-") as temporary:
        output_path = Path(temporary) / "last-message.txt"
        args = [
            *command,
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
            "--model",
            selected_model,
            "--config",
            f'model_reasoning_effort="{selected_effort}"',
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--skip-git-repo-check",
            "--color",
            "never",
            "--output-last-message",
            str(output_path),
            "-",
        ]
        try:
            completed = subprocess.run(
                args,
                cwd=temporary,
                env=dict(os.environ),
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodexError("Codex Judge 调用超时") from exc
        except OSError as exc:
            raise CodexError(f"无法启动 Codex CLI: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[-1200:]
            raise CodexError(
                f"Codex CLI 返回退出码 {completed.returncode}"
                + (f": {detail}" if detail else "")
            )
        text = (
            output_path.read_text(encoding="utf-8").strip()
            if output_path.is_file()
            else ""
        )
        if not text:
            text = (completed.stdout or "").strip()
        if not text:
            raise CodexError(
                f"Codex Judge 在 {time.monotonic() - started:.1f}s 后返回空结果"
            )
        return text
