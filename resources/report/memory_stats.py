#!/usr/bin/env python3
"""Read-only L2 format and capacity checks. Never writes memory or statistics."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys

LIMIT = 1000
MAX_MEMORY_BYTES = 300_000
ID_PATTERN = r"(?:M-[0-9]{8}-[0-9]{3,}-\[(?:0|[1-9][0-9]*)\]|MR-[A-Za-z0-9_-]+|M-[0-9]{8}-[0-9]{2,})"


def rubric_key(identifier):
    """Counting changes the displayed ID, not the date/sequence identity."""
    return re.sub(r"-\[[0-9]+\]$", "", identifier)


def active_ids(path):
    """One explicit active section; never count mentions or silently guess legacy tables."""
    text = Path(path).read_text(encoding="utf-8-sig")
    sections = list(re.finditer(r"^## (?:Active L2B?(?: Index| Memory Rubrics)?|生效 L2B?(?: Rubrics)?)\s*$", text, re.M))
    if len(sections) != 1:
        raise ValueError("Expected one '## Active L2' section (legacy Active L2B accepted)")
    body = text[sections[0].end():]
    body = re.split(r"^#{1,2} ", body, maxsplit=1, flags=re.M)[0]
    # Heading IDs, not source references in rule bodies, define the inventory.
    headings = re.findall(r"^### (.+)$", body, re.M)
    ids = []
    for heading in headings:
        match = re.match(rf"({ID_PATTERN})(?=$|\s|[｜|:：—])", heading)
        if not match:
            raise ValueError("Each active rubric needs an M-YYYYMMDD-NNN-[x] or legacy ID heading")
        ids.append(match[1])
    if len(ids) != len({rubric_key(key) for key in ids}):
        raise ValueError("Duplicate active rubric ID")
    preamble = body.split("### ", 1)[0].strip()
    if preamble not in ("", "无", "暂无", "暂无。", "None", "(empty)"):
        raise ValueError("Unrecognised active inventory; preserve content and normalise headings before writing")
    if re.search(r"^```|^~~~", body, re.M):
        raise ValueError("Code fences are not supported in the active inventory")
    return ids


def validate(memory):
    """Read-only shape validation, not a semantic admission or source-existence judge."""
    text = Path(memory).read_text(encoding="utf-8-sig")
    issues = []
    parts = text.split("\n## Active L2\n")
    if len(parts) != 2:
        return {"marker": "MEMORY_FORMAT_NEEDS_REVIEW", "issues": ["Use one canonical ## Active L2 section; preserve legacy contents when migrating"]}
    header, body = parts
    lines = [line.strip() for line in header.splitlines() if line.strip()]
    if not lines or not re.fullmatch(r"# [^#].*", lines[0]):
        issues.append("One document title is required")
    metadata = {}
    for line in lines[1:]:
        match = re.fullmatch(r"(revision|enabled|lastReflectionAt):\s*(.+)", line)
        if not match or match[1] in metadata:
            issues.append("Only revision, enabled and lastReflectionAt belong above Active L2")
        else:
            metadata[match[1]] = match[2]
    if not re.fullmatch(r"\d+", metadata.get("revision", "")):
        issues.append("revision must be a non-negative integer; new libraries start at 1")
    if metadata.get("enabled") not in ("true", "false"):
        issues.append("enabled must be true or false")
    stamp = metadata.get("lastReflectionAt", "")
    if stamp != "null":
        try:
            datetime.fromisoformat(stamp)
            if "T" not in stamp and " " not in stamp:
                raise ValueError("time missing")
        except ValueError:
            issues.append("lastReflectionAt must be null or an ISO date-time")
    if re.search(r"^#{1,2} |^#{4,} |^```|^~~~", body, re.M):
        issues.append("No other sections, nested headings or code fences in MEMORY.md")
    blocks = re.split(r"^### ", body, flags=re.M)
    if blocks[0].strip():
        issues.append("No indexes, summaries or counts before active rubric entries")
    ids = []
    for block in blocks[1:]:
        heading, _, content = block.partition("\n")
        match = re.fullmatch(rf"({ID_PATTERN})\s+\S.*", heading)
        if not match:
            issues.append("Rubric heading must contain M-YYYYMMDD-NNN-[x] or legacy ID and a title")
            continue
        key = match[1]
        ids.append(key)
        canonical = re.fullmatch(r"M-([0-9]{8})-([0-9]{3,})-\[([0-9]+)\]", key)
        if canonical:
            try:
                datetime.strptime(canonical[1], "%Y%m%d")
                if int(canonical[2]) < 1:
                    raise ValueError("sequence starts at 001")
            except ValueError:
                issues.append(f"{key}: invalid date or sequence")
        fields, prose = {}, []
        for line in content.splitlines():
            field = re.match(r"^- ([A-Za-z][A-Za-z0-9]*):\s*(.*)$", line)
            if field:
                if field[1] not in ("scope", "scopeValue", "sourceL1Ids", "dimensionCandidate", "updatedAt", "useCount") or field[1] in fields:
                    issues.append(f"{key}: unknown or duplicate metadata field {field[1]}")
                fields[field[1]] = field[2]
            elif line.strip():
                prose.append(line)
        scope = fields.get("scope")
        if scope not in ("core", "audience", "project"):
            issues.append(f"{key}: invalid scope")
        if scope in ("audience", "project") and not fields.get("scopeValue", "").strip():
            issues.append(f"{key}: scopeValue is required")
        if scope == "core" and "scopeValue" in fields:
            issues.append(f"{key}: core does not use scopeValue")
        sources = fields.get("sourceL1Ids", "")
        if not re.fullmatch(r"\[\s*L1-[A-Za-z0-9_-]+(?:\s*,\s*L1-[A-Za-z0-9_-]+)*\s*\]", sources):
            issues.append(f"{key}: sourceL1Ids must be a non-empty list of L1 IDs")
        if canonical and {"updatedAt", "useCount"}.intersection(fields):
            issues.append(f"{key}: date and usage belong in the ID, not separate fields")
        stamp = fields.get("updatedAt", "unknown")
        if stamp != "unknown":
            try:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", stamp):
                    raise ValueError("date format")
                datetime.strptime(stamp, "%Y-%m-%d")
            except ValueError:
                issues.append(f"{key}: updatedAt must be YYYY-MM-DD or unknown")
        if "useCount" in fields and not re.fullmatch(r"\d+|unknown", fields["useCount"]):
            issues.append(f"{key}: useCount must be a non-negative integer or unknown")
        candidate = fields.get("dimensionCandidate")
        if candidate is not None:
            try:
                obj = json.loads(candidate)
                if not isinstance(obj, dict) or set(obj) != {"name", "label", "reason"} or not all(isinstance(v, str) and v.strip() for v in obj.values()):
                    raise ValueError("invalid candidate")
            except ValueError:
                issues.append(f"{key}: invalid dimensionCandidate")
        if not prose:
            issues.append(f"{key}: rule statement is empty")
    if len(ids) != len({rubric_key(key) for key in ids}):
        issues.append("Duplicate rubric IDs")
    return {"marker": "MEMORY_FORMAT_NEEDS_REVIEW" if issues else "MEMORY_FORMAT_OK",
            "issues": issues, "count": len(ids),
            "note": "Structure only; Curator must verify sources and durable applicability"}


def check(root, memory):
    structure = validate(memory)
    if structure["issues"]:
        return structure
    ids = active_ids(memory)
    size = Path(memory).stat().st_size
    return {"marker": "MEMORY_CAPACITY_EXCEEDED" if len(ids) > LIMIT or size > MAX_MEMORY_BYTES else "MEMORY_CAPACITY_OK",
            "count": len(ids), "limit": LIMIT, "excess": max(0, len(ids) - LIMIT),
            "bytes": size, "byteLimit": MAX_MEMORY_BYTES, "excessBytes": max(0, size - MAX_MEMORY_BYTES)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check")
    p.add_argument("--memory", type=Path, help="Optional proposed MEMORY.md; read-only")
    p = sub.add_parser("validate")
    p.add_argument("--memory", type=Path, help="Optional proposed MEMORY.md; read-only")
    args = parser.parse_args()
    try:
        if not args.root.is_absolute() or not args.root.is_dir():
            raise ValueError("memoryRoot must be an existing absolute directory; no fallback")
        if args.command == "validate":
            result = validate(args.memory or args.root / "MEMORY.md")
        elif args.command == "check":
            result = check(args.root, args.memory or args.root / "MEMORY.md")
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result["marker"] in ("MEMORY_CAPACITY_EXCEEDED", "MEMORY_FORMAT_NEEDS_REVIEW") else 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"marker": "MEMORY_STATS_FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
