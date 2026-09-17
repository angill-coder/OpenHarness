#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""素材指纹清单与数据版本登记。

本脚本只做确定性计算：遍历素材目录、算 SHA-256、与基线比对、读写
`数据版本说明.md`。**不解析文档内容，不调用模型**——事实层面的变化判断由
Agent 依据 cleaning-rules 完成，这里只回答"哪些文件的字节变了"。

放在脚本里而不是让模型眼看，是因为指纹比对是纯计算：同样的输入必须得到
同样的输出，且不能因为文件名、大小或修改时间相同就认定内容未变。

三个子命令（契约见 references/source-changes.md）::

    scan     --root <素材目录> --output <workDir>/素材扫描.json
             [--exclude <自定义报告总目录>]...
    confirm  --scan <素材扫描.json> --evidence-sha256 <SHA256> --summary <摘要>
    check    --root <素材目录> [--version D2] [--sha256 <dataSha256>]

`scan` 输出 added / modified / deleted / unchangedCount / fullReviewRequired。
`confirm` 在论据表发布并验证成功后登记数据版本（首次 D1，共享数据指纹变化
才递增）。`check` 在写作与 Judge 前后核验本轮绑定的数据未被替换。

Python 3.9+，只用标准库。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA = "report-agent-source-inventory/v1"
DATA_LEDGER_FILE = "数据版本说明.md"
FINGERPRINT_HEADING = "## 当前素材指纹清单"
VERSION_HEADING = "## 数据版本记录"

# 默认排除：本流程自身的产物、旧版清单、报告目录、运行记录与常见工具缓存。
# 素材扫描文件本身也在排除范围内，不当作原始素材。
EXCLUDED_NAMES = frozenset({
    "structured_data.json",
    DATA_LEDGER_FILE,
    "素材清单.json",
    "素材扫描.json",
    "报告",
    ".report-agent",
    ".report-loop",
    ".git",
    ".svn",
    "node_modules",
    "__pycache__",
    ".DS_Store",
    "Thumbs.db",
    ".venv",
    "venv",
    ".idea",
    ".vscode",
})
EXCLUDED_SUFFIXES = (".tmp", ".pyc", ".swp", ".crdownload", ".part")
EXCLUDED_PREFIXES = ("~$",)  # Office 锁文件

_VERSION_ROW = re.compile(r"^\|\s*(?P<version>D\d+)\s*\|")


class InventoryError(RuntimeError):
    """扫描或登记失败，需要明确报告而非猜测。"""


# --------------------------------------------------------------------------
# 指纹计算
# --------------------------------------------------------------------------

def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def is_excluded(relative: Path, extra_excludes: frozenset[str]) -> bool:
    """任一路径片段命中排除规则即排除，保证跨轮扫描范围一致。"""
    for part in relative.parts:
        if part in EXCLUDED_NAMES or part in extra_excludes:
            return True
        if part.startswith(EXCLUDED_PREFIXES):
            return True
    name = relative.name
    return name.endswith(EXCLUDED_SUFFIXES)


def build_inventory(root: Path, extra_excludes: frozenset[str]) -> dict[str, str]:
    """遍历素材目录，返回 {相对路径: sha256}。

    相对路径使用 POSIX 分隔符，使清单在不同平台间可比；中文与空格原样保留。
    """
    inventory: dict[str, str] = {}
    unreadable: list[str] = []
    for current, directories, files in os.walk(root):
        current_path = Path(current)
        relative_dir = current_path.relative_to(root)
        # 就地裁剪，避免进入被排除的子树
        directories[:] = [
            name for name in directories
            if not is_excluded(relative_dir / name, extra_excludes)
        ]
        for name in files:
            relative = relative_dir / name
            if is_excluded(relative, extra_excludes):
                continue
            absolute = current_path / name
            if absolute.is_symlink() and not absolute.exists():
                unreadable.append(str(relative.as_posix()))
                continue
            try:
                inventory[relative.as_posix()] = file_sha256(absolute)
            except OSError as exc:
                unreadable.append(f"{relative.as_posix()}（{exc.strerror or exc}）")
    if unreadable:
        raise InventoryError(
            "以下素材无法读取，请先处理后重新扫描：" + "；".join(sorted(unreadable))
        )
    return dict(sorted(inventory.items()))


# --------------------------------------------------------------------------
# 数据版本说明.md 读写
# --------------------------------------------------------------------------

def parse_ledger(path: Path) -> dict[str, Any]:
    """读取数据版本说明：上半部版本记录，下半部当前素材指纹 JSON。

    文件不存在时返回空基线（fullReviewRequired 由调用方判定）。格式损坏时
    明确报错，不猜测。
    """
    if not path.is_file():
        return {"versions": [], "inventory": {}, "present": False}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InventoryError(f"无法读取 {path.name}：{exc}") from exc

    versions = [
        match.group("version")
        for line in raw.splitlines()
        if (match := _VERSION_ROW.match(line.strip()))
    ]

    inventory: dict[str, str] = {}
    if FINGERPRINT_HEADING in raw:
        tail = raw.split(FINGERPRINT_HEADING, 1)[1]
        start = tail.find("```")
        if start >= 0:
            body = tail[start + 3:]
            body = body.split("\n", 1)[1] if "\n" in body else ""
            end = body.find("```")
            block = body[:end] if end >= 0 else body
            try:
                parsed = json.loads(block or "{}")
            except json.JSONDecodeError as exc:
                raise InventoryError(
                    f"{path.name} 的当前素材指纹清单格式损坏：{exc}。"
                    "请先修复或移除该段，再重新扫描建立基线。"
                ) from exc
            if not isinstance(parsed, dict):
                raise InventoryError(f"{path.name} 的指纹清单应为 JSON 对象")
            inventory = {str(k): str(v) for k, v in parsed.items()}
    return {"versions": versions, "inventory": inventory, "present": True}


def next_data_version(versions: list[str]) -> str:
    """首次登记 D1；共享数据指纹变化才递增到 D2、D3…"""
    highest = 0
    for value in versions:
        if value.startswith("D") and value[1:].isdigit():
            highest = max(highest, int(value[1:]))
    return f"D{highest + 1}"


def render_ledger(
    *,
    existing_raw: str | None,
    version_rows: list[dict[str, str]],
    inventory: dict[str, str],
) -> str:
    lines = [
        "# 数据版本说明",
        "",
        "本文件由 `source_inventory.py` 写入，记录共享论据表的数据版本与当前素材指纹。",
        "指纹清单只用于增量核验，不含论据原文，也不是数据快照。",
        "",
        VERSION_HEADING,
        "",
        "| 数据版本 | 更新时间 | 更新摘要 | 数据指纹 (structured_data.json SHA-256) |",
        "| --- | --- | --- | --- |",
    ]
    for row in version_rows:
        summary = row["summary"].replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {row['version']} | {row['updatedAt']} | {summary} | `{row['dataSha256']}` |"
        )
    lines.extend([
        "",
        FINGERPRINT_HEADING,
        "",
        f"共 {len(inventory)} 个素材文件。",
        "",
        "```json",
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True),
        "```",
        "",
    ])
    return "\n".join(lines)


def existing_version_rows(path: Path) -> list[dict[str, str]]:
    """解析已有版本行，便于追加新版本时保留历史。"""
    if not path.is_file():
        return []
    rows: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not _VERSION_ROW.match(stripped):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if len(cells) >= 4:
            rows.append({
                "version": cells[0],
                "updatedAt": cells[1],
                "summary": cells[2].replace("\\|", "|"),
                "dataSha256": cells[3].strip("`"),
            })
    return rows


def write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.inventory.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def current_data_version(path: Path) -> tuple[str | None, str | None]:
    """返回 (当前版本号, 当前数据指纹)。"""
    rows = existing_version_rows(path)
    if not rows:
        return None, None
    latest = rows[-1]
    return latest["version"], latest["dataSha256"]


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------

def command_scan(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise InventoryError(f"素材目录不存在：{root}")
    extra = frozenset(
        Path(value).name if os.sep in value else value
        for value in (args.exclude or [])
    )

    current = build_inventory(root, extra)
    ledger_path = root / DATA_LEDGER_FILE
    ledger = parse_ledger(ledger_path)
    baseline = ledger["inventory"]

    # 没有可信基线：首次使用，或清单缺失。扫描范围变化与论据表被外部改动
    # 由调用方结合 check 判断。
    full_review = not baseline

    added = sorted(set(current) - set(baseline))
    deleted = sorted(set(baseline) - set(current))
    modified = sorted(
        path for path in set(current) & set(baseline)
        if current[path] != baseline[path]
    )
    unchanged = [
        path for path in set(current) & set(baseline)
        if current[path] == baseline[path]
    ]

    result = {
        "schema": SCHEMA,
        "marker": "SOURCE_SCAN_COMPLETED",
        "root": str(root),
        "scannedAt": datetime.now().isoformat(timespec="seconds"),
        "fullReviewRequired": full_review,
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "unchangedCount": len(unchanged),
        "currentFileCount": len(current),
        "excluded": sorted(extra),
        "inventory": current,
        "baselineVersion": current_data_version(ledger_path)[0],
    }
    if args.output:
        output = Path(args.output).expanduser().resolve()
        write_atomic(output, json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        result = {**result, "outputPath": str(output)}
    # 清单可能很大，不回传到会话；已落盘则只返回摘要
    return {k: v for k, v in result.items() if k != "inventory"}


def command_confirm(args: argparse.Namespace) -> dict[str, Any]:
    scan_path = Path(args.scan).expanduser().resolve()
    if not scan_path.is_file():
        raise InventoryError(f"素材扫描文件不存在：{scan_path}")
    try:
        scan = json.loads(scan_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InventoryError(f"素材扫描文件无法解析：{exc}") from exc
    if scan.get("schema") != SCHEMA:
        raise InventoryError("素材扫描文件 schema 不匹配，请重新 scan")

    root = Path(scan["root"]).expanduser().resolve()
    if not root.is_dir():
        raise InventoryError(f"素材目录不存在：{root}")

    evidence_path = root / "structured_data.json"
    if not evidence_path.is_file():
        raise InventoryError(
            f"共享论据表不存在：{evidence_path}；数据版本未登记"
        )
    actual_evidence_sha = file_sha256(evidence_path)
    if actual_evidence_sha != args.evidence_sha256:
        raise InventoryError(
            "共享论据表指纹与传入值不一致，数据版本未登记。"
            f"传入 {args.evidence_sha256}，实际 {actual_evidence_sha}。"
            "可能在发布后被其他任务改动，请重新校验后再 confirm。"
        )

    # 重新核验素材：confirm 与 scan 之间文件不应再变化
    extra = frozenset(scan.get("excluded") or [])
    current = build_inventory(root, extra)
    scanned_inventory = scan.get("inventory") or {}
    if scanned_inventory and current != scanned_inventory:
        changed = sorted(
            set(current.items()) ^ set(scanned_inventory.items())
        )[:5]
        raise InventoryError(
            "扫描之后素材又发生变化，数据版本未登记。"
            f"受影响示例：{[name for name, _ in changed]}；请重新 scan 并处理变化。"
        )

    ledger_path = root / DATA_LEDGER_FILE
    ledger = parse_ledger(ledger_path)
    rows = existing_version_rows(ledger_path)
    _, previous_sha = current_data_version(ledger_path)

    if previous_sha == actual_evidence_sha:
        # 共享数据未变：只刷新当前素材指纹，不新增版本记录
        version = rows[-1]["version"] if rows else next_data_version(ledger["versions"])
        write_atomic(ledger_path, render_ledger(
            existing_raw=None, version_rows=rows, inventory=current,
        ))
        return {
            "marker": "DATA_VERSION_UNCHANGED",
            "dataVersion": version,
            "dataSha256": actual_evidence_sha,
            "ledgerPath": str(ledger_path),
            "currentFileCount": len(current),
            "note": "共享论据表未变化，仅更新当前素材指纹清单",
        }

    version = next_data_version(ledger["versions"])
    rows.append({
        "version": version,
        "updatedAt": datetime.now().isoformat(timespec="seconds"),
        "summary": args.summary,
        "dataSha256": actual_evidence_sha,
    })
    write_atomic(ledger_path, render_ledger(
        existing_raw=None, version_rows=rows, inventory=current,
    ))
    return {
        "marker": "DATA_VERSION_REGISTERED",
        "dataVersion": version,
        "dataSha256": actual_evidence_sha,
        "ledgerPath": str(ledger_path),
        "currentFileCount": len(current),
        "summary": args.summary,
    }


def command_check(args: argparse.Namespace) -> dict[str, Any]:
    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        raise InventoryError(f"素材目录不存在：{root}")
    ledger_path = root / DATA_LEDGER_FILE
    version, sha = current_data_version(ledger_path)

    if version is None:
        return {
            "marker": "DATA_VERSION_MISSING",
            "ledgerPath": str(ledger_path),
            "note": "尚未登记数据版本，需先核验并建立基线",
        }

    evidence_path = root / "structured_data.json"
    actual = file_sha256(evidence_path) if evidence_path.is_file() else None

    # 只查询当前合法版本
    if not args.version and not args.sha256:
        return {
            "marker": "DATA_VERSION_CURRENT",
            "dataVersion": version,
            "dataSha256": sha,
            "evidenceSha256": actual,
            "matchesLedger": actual == sha,
        }

    mismatched = (
        (args.version and args.version != version)
        or (args.sha256 and args.sha256 != sha)
        or (actual is not None and actual != sha)
    )
    if mismatched:
        return {
            "marker": "DATA_VERSION_CHANGED",
            "expectedVersion": args.version,
            "expectedSha256": args.sha256,
            "currentVersion": version,
            "currentSha256": sha,
            "evidenceSha256": actual,
            "note": "停止使用当前任务的旧数据绑定，按素材更新流程处理",
        }
    return {
        "marker": "DATA_VERSION_OK",
        "dataVersion": version,
        "dataSha256": sha,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="source_inventory.py",
        description="素材指纹清单与数据版本登记（不解析文档，不调用模型）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="扫描素材目录并与基线比对")
    scan.add_argument("--root", required=True, help="本项目完整素材目录")
    scan.add_argument("--output", help="素材扫描.json 输出路径（workDir 内）")
    scan.add_argument(
        "--exclude", action="append", default=[],
        help="额外排除的目录名（如自定义报告总目录），可重复",
    )
    scan.set_defaults(handler=command_scan)

    confirm = sub.add_parser("confirm", help="登记数据版本（论据表发布验证成功后）")
    confirm.add_argument("--scan", required=True, help="素材扫描.json 路径")
    confirm.add_argument(
        "--evidence-sha256", required=True, dest="evidence_sha256",
        help="校验候选时计算的共享论据表 SHA-256",
    )
    confirm.add_argument("--summary", required=True, help="本次论据更新摘要")
    confirm.set_defaults(handler=command_confirm)

    check = sub.add_parser("check", help="核验本轮绑定的数据版本未被替换")
    check.add_argument("--root", required=True, help="本项目完整素材目录")
    check.add_argument("--version", help="本轮绑定的数据版本，如 D2")
    check.add_argument("--sha256", help="本轮绑定的 dataSha256")
    check.set_defaults(handler=command_check)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = args.handler(args)
    except InventoryError as exc:
        json.dump(
            {"marker": "SOURCE_INVENTORY_FAILED", "error": str(exc)},
            sys.stdout, ensure_ascii=False, indent=2,
        )
        sys.stdout.write("\n")
        return 1
    json.dump(result, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
