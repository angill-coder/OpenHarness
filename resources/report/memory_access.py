#!/usr/bin/env python3
"""记忆目录可写性探针与授权落地。

为什么需要它
------------
写作记忆存在用户主目录下的 `ReportAgentMemory/`，**刻意不在报告工作区内**
——它跨项目、跨宿主共用。代价是它可能落在宿主沙箱的可写范围之外。

如果等到 capture 时才发现写不了，那一轮的用户反馈就已经丢了：用户给了明确的
写作要求，系统却静默失败。所以可写性必须在**真正用到记忆之前**确认。

流程：

1. `probe` —— 主理人在 Phase 0 调用。真实写一个探针文件再删除（`os.access`
   在某些沙箱下会误报可写），返回 `writable` / `needs_authorization`。
2. 不可写 —— 主理人用 `AskUserQuestion` 让用户授权该目录，或指定一个可写位置。
3. `set-root` —— 把用户选定的位置记下来。后续会话 `probe` 会先读它，
   不再重复询问。

位置记录写在 `~/.report-agent-memory-location`（单行绝对路径）。它只是个指针，
不含任何记忆内容——记忆本体仍是纯 Markdown，全部在 memoryRoot 里。主目录本身
不可写时指针退到临时目录；`REPORT_AGENT_MEMORY_DIR` 可直接指定位置并优先于指针。

用法::

    python3 memory_access.py probe
    python3 memory_access.py set-root --path "<用户选定的绝对路径>"
    python3 memory_access.py status
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

DEFAULT_DIR_NAME = "ReportAgentMemory"
LOCATION_POINTER = ".report-agent-memory-location"
MEMORY_FILE = "MEMORY.md"


class MemoryAccessError(RuntimeError):
    """探针无法完成；调用方应如实报告而不是假装记忆可用。"""


def home() -> Path:
    """用户主目录。不从工作区推算。"""
    for variable in ("HOME", "USERPROFILE"):
        value = os.environ.get(variable)
        if value and Path(value).is_dir():
            return Path(value)
    return Path.home()


def pointer_path() -> Path:
    """位置指针优先放主目录；主目录不可写时退到临时目录。

    指针只是个路径记录，不含记忆内容。但如果沙箱把整个主目录挡住，指针也写
    不进去——那样用户每个会话都会被重复询问。退到临时目录至少能在同一台机器
    的后续会话里复用；彻底不可写时由 `REPORT_AGENT_MEMORY_DIR` 兜底。
    """
    primary = home() / LOCATION_POINTER
    if primary.is_file() or os.access(primary.parent, os.W_OK):
        return primary
    import tempfile

    return Path(tempfile.gettempdir()) / LOCATION_POINTER


def default_root() -> Path:
    return home() / DEFAULT_DIR_NAME


def recorded_root() -> Path | None:
    """读取用户此前选定的位置；没有记录或内容非法时返回 None。"""
    path = pointer_path()
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not raw:
        return None
    candidate = Path(raw).expanduser()
    return candidate if candidate.is_absolute() else None


def resolve_root(explicit: str | None = None) -> tuple[Path, str]:
    """返回 (memoryRoot, 来源)。显式 > 已记录 > 主目录默认。"""
    if str(explicit or "").strip():
        candidate = Path(str(explicit).strip()).expanduser()
        if not candidate.is_absolute():
            raise MemoryAccessError(f"memoryRoot 必须是绝对路径：{explicit}")
        return candidate, "explicit"
    configured = os.environ.get("REPORT_AGENT_MEMORY_DIR", "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_absolute():
            return candidate, "environment"
    recorded = recorded_root()
    if recorded is not None:
        return recorded, "recorded"
    return default_root(), "default"


def probe_writable(root: Path) -> tuple[bool, str]:
    """真实写入一个探针文件再删除。

    只查 `os.access` 不够——部分沙箱会报告可写但实际拦截写入，那种情况下
    错误要等到真正 capture 时才暴露。
    """
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"无法创建目录：{exc.strerror or exc}"
    probe = root / ".report-agent-write-probe"
    try:
        probe.write_text("probe", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return False, f"目录存在但不可写：{exc.strerror or exc}"
    return True, ""


def command_probe(args: argparse.Namespace) -> dict[str, Any]:
    root, source = resolve_root(args.root)
    writable, reason = probe_writable(root)
    initialized = (root / MEMORY_FILE).is_file()
    payload: dict[str, Any] = {
        "memoryRoot": str(root),
        "source": source,
        "writable": writable,
        "initialized": initialized,
    }
    if writable:
        payload["marker"] = "MEMORY_ACCESS_OK"
        payload["note"] = (
            "记忆目录可读写；首次使用时由记忆管理员创建 MEMORY.md"
            if not initialized else "记忆目录可读写，已初始化"
        )
        return payload
    payload["marker"] = "MEMORY_ACCESS_NEEDS_AUTHORIZATION"
    payload["reason"] = reason
    payload["suggestedPrompt"] = (
        f"写作记忆需要读写 {root}（在报告工作区之外，跨项目共用）。"
        "请授权该目录，或指定一个可写位置；也可以本轮先不启用记忆。"
    )
    payload["options"] = [
        {"action": "authorize", "detail": f"授权读写 {root}"},
        {"action": "relocate", "detail": "改用其他可写目录，需提供绝对路径"},
        {"action": "disable", "detail": "本轮不使用记忆，仅用 Base Rubrics 评测"},
    ]
    return payload


def command_set_root(args: argparse.Namespace) -> dict[str, Any]:
    candidate = Path(args.path).expanduser()
    if not candidate.is_absolute():
        raise MemoryAccessError(f"必须是绝对路径：{args.path}")
    writable, reason = probe_writable(candidate)
    if not writable:
        raise MemoryAccessError(f"该位置仍不可写：{candidate}（{reason}）")
    pointer = pointer_path()
    temporary = pointer.with_name(pointer.name + ".tmp")
    try:
        temporary.write_text(str(candidate.resolve()) + "\n", encoding="utf-8")
        temporary.replace(pointer)
    except OSError as exc:
        raise MemoryAccessError(f"无法记录记忆位置：{exc.strerror or exc}") from exc
    return {
        "marker": "MEMORY_ACCESS_RECORDED",
        "memoryRoot": str(candidate.resolve()),
        "pointerPath": str(pointer),
        "note": "后续会话直接沿用该位置，不再重复询问",
    }


def command_status(args: argparse.Namespace) -> dict[str, Any]:
    root, source = resolve_root(args.root)
    return {
        "marker": "MEMORY_ACCESS_STATUS",
        "memoryRoot": str(root),
        "source": source,
        "recordedRoot": str(recorded_root()) if recorded_root() else None,
        "defaultRoot": str(default_root()),
        "initialized": (root / MEMORY_FILE).is_file(),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory_access.py",
        description="记忆目录可写性探针与授权落地（记忆在报告工作区之外）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    probe = sub.add_parser("probe", help="Phase 0 探测可写性")
    probe.add_argument("--root", help="显式指定 memoryRoot（可选）")
    probe.set_defaults(handler=command_probe)

    record = sub.add_parser("set-root", help="记录用户选定的记忆位置")
    record.add_argument("--path", required=True, help="绝对路径")
    record.set_defaults(handler=command_set_root)

    status = sub.add_parser("status", help="查询当前记忆位置")
    status.add_argument("--root", help="显式指定 memoryRoot（可选）")
    status.set_defaults(handler=command_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.handler(args)
    except MemoryAccessError as exc:
        json.dump({"marker": "MEMORY_ACCESS_FAILED", "error": str(exc)},
                  sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    # 需要授权不算脚本失败：主理人据此询问用户，而不是把它当异常
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
