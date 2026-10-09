#!/usr/bin/env python3
"""L2 capacity and usage metadata. Standard library only; never edits MEMORY.md."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile

LIMIT = 1000
MAX_MEMORY_BYTES = 1024 * 1024
ID = re.compile(r"MR-[A-Za-z0-9_-]+")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
        match = re.match(r"(MR-[A-Za-z0-9_-]+)(?=$|\s|[｜|:：—])", heading)
        if not match:
            raise ValueError("Each active rubric needs a '### MR-id title' heading")
        ids.append(match[1])
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate active rubric ID")
    preamble = body.split("### ", 1)[0].strip()
    if preamble not in ("", "无", "暂无", "暂无。", "None", "(empty)"):
        raise ValueError("Unrecognised active inventory; preserve content and normalise headings before writing")
    if re.search(r"^```|^~~~", body, re.M):
        raise ValueError("Code fences are not supported in the active inventory")
    return ids


def empty_entry():
    return {"createdAt": None, "updatedAt": None, "useCount": 0,
            "lastUsedAt": None, "runs": {}}


def valid_time(value):
    if value is not None:
        if not isinstance(value, str) or datetime.fromisoformat(value).tzinfo is None:
            raise ValueError("Statistics timestamps must include a timezone")


def load_stats(root):
    path = root / "memory-stats.json"
    if not path.exists():
        return {"schemaVersion": 1, "trackingSince": now(), "rubrics": {}, "appliedChanges": {}}
    data = read_json(path)
    if data.get("schemaVersion") != 1 or not isinstance(data.get("rubrics"), dict) or not isinstance(data.get("appliedChanges"), dict):
        raise ValueError("Invalid memory-stats.json; do not overwrite")
    valid_time(data["trackingSince"])
    for key, entry in data["rubrics"].items():
        if not ID.fullmatch(key) or not isinstance(entry.get("runs"), dict):
            raise ValueError("Invalid rubric statistics")
        for field in ("createdAt", "updatedAt", "lastUsedAt"):
            valid_time(entry[field])
        for run, timestamp in entry["runs"].items():
            if not run or timestamp is None:
                raise ValueError("Invalid usage event")
            valid_time(timestamp)
        if entry["useCount"] != len(entry["runs"]):
            raise ValueError("Usage count differs from deduplicated Loop events")
        if entry.get("mergedInto") and not ID.fullmatch(entry["mergedInto"]):
            raise ValueError("Invalid merge target")
    return data


@contextmanager
def locked(root):
    lock = root / ".memory-stats.lock"
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def save(root, data):
    fd, name = tempfile.mkstemp(prefix=".memory-stats-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, root / "memory-stats.json")
    finally:
        if os.path.exists(name):
            os.unlink(name)


def recount(entry):
    entry["useCount"] = len(entry["runs"])
    entry["lastUsedAt"] = max(entry["runs"].values(), key=datetime.fromisoformat, default=None)


def check(root, memory):
    ids = active_ids(memory)
    size = Path(memory).stat().st_size
    exists = (root / "memory-stats.json").exists()
    stats = load_stats(root)
    rows = []
    for key in ids:
        entry = stats["rubrics"].get(key)
        rows.append({"id": key, **{field: entry[field] if entry else None
                    for field in ("createdAt", "updatedAt", "useCount", "lastUsedAt")}})
    return {"marker": "MEMORY_CAPACITY_EXCEEDED" if len(ids) > LIMIT or size > MAX_MEMORY_BYTES else "MEMORY_CAPACITY_OK",
            "count": len(ids), "limit": LIMIT, "excess": max(0, len(ids) - LIMIT),
            "bytes": size, "byteLimit": MAX_MEMORY_BYTES, "excessBytes": max(0, size - MAX_MEMORY_BYTES),
            "trackingSince": stats["trackingSince"] if exists else None, "rubrics": rows}


def sync(root, changes):
    ids = set(active_ids(root / "MEMORY.md"))
    if len(ids) > LIMIT:
        raise ValueError("Active L2 exceeds 1000; compress before completing the write")
    if (root / "MEMORY.md").stat().st_size > MAX_MEMORY_BYTES:
        raise ValueError("MEMORY.md exceeds 1 MiB; compress before completing the write")
    revision = str(changes["revision"])
    memory = (root / "MEMORY.md").read_text(encoding="utf-8-sig")
    if not re.search(r"^revision:\s*" + re.escape(revision) + r"\s*$", memory, re.M):
        raise ValueError("Changes revision does not match saved MEMORY.md")
    new = changes.get("newIds", [])
    updated = changes.get("updatedIds", [])
    merges = changes.get("merges", {})
    if not isinstance(new, list) or not isinstance(updated, list) or not isinstance(merges, dict):
        raise ValueError("Invalid changes shape")
    if not set(new + updated + list(merges)).issubset(ids):
        raise ValueError("Changed rubric is not active")
    if set(new) & set(updated + list(merges)):
        raise ValueError("New rubrics cannot also be updates or merges")
    digest = hashlib.sha256(json.dumps(changes, sort_keys=True).encode()).hexdigest()
    with locked(root):
        data = load_stats(root)
        prior = data["appliedChanges"].get(revision)
        if prior:
            if prior != digest:
                raise ValueError("Different changes for an already recorded revision")
            return {"marker": "MEMORY_STATS_SYNCED", "idempotent": True}
        stamp = now()
        entries = data["rubrics"]
        if any(key in entries for key in new):
            raise ValueError("Cannot reuse an existing rubric ID as new")
        for key in ids:
            entries.setdefault(key, empty_entry())
        for key in new:
            entries[key]["createdAt"] = entries[key]["updatedAt"] = stamp
        for target, sources in merges.items():
            if not isinstance(sources, list) or not sources or target in sources or any(not isinstance(s, str) or not ID.fullmatch(s) or s in ids for s in sources):
                raise ValueError("Merge sources must be retired IDs, distinct from target")
            if entries[target].get("mergedInto"):
                raise ValueError("Retired merge target cannot become active again; use a new ID")
            group = [entries[target]] + [entries.get(s, empty_entry()) for s in sources]
            for field, choose in (("createdAt", min), ("updatedAt", max)):
                dates = [e[field] for e in group if e[field]]
                entries[target][field] = choose(dates, key=datetime.fromisoformat) if dates else None
            for source in sources:
                old_target = entries.get(source, {}).get("mergedInto")
                if old_target and old_target != target:
                    raise ValueError("Source already merged elsewhere; merge its current target instead")
                for run, time in entries.get(source, empty_entry())["runs"].items():
                    old = entries[target]["runs"].get(run)
                    entries[target]["runs"][run] = min([old, time], key=datetime.fromisoformat) if old else time
                entries.setdefault(source, empty_entry())["mergedInto"] = target
            recount(entries[target])
        for key in updated:
            entries[key]["updatedAt"] = stamp
        data["appliedChanges"][revision] = digest
        save(root, data)
    return {"marker": "MEMORY_STATS_SYNCED", "idempotent": False}


def used_ids(plan, results):
    dimensions = {d["id"]: d for d in plan["dimensions"]}
    if not dimensions or len(dimensions) != len(plan["dimensions"]):
        raise ValueError("Invalid frozen dimensions")
    decisions = {d["memoryId"]: d["mode"] for d in plan.get("memoryDecisions", [])}
    if len(decisions) != len(plan.get("memoryDecisions", [])):
        raise ValueError("Duplicate memory decisions")
    used = set()
    for result in results:
        dimension = dimensions[result["dimensionId"]]
        expected = {c["id"] for c in dimension["checks"]}
        checks = result["checks"]
        if len(expected) != len(dimension["checks"]) or not isinstance(checks, list) or not expected or len(checks) != len(expected) or {c["id"] for c in checks} != expected:
            raise ValueError("Judge result does not cover exactly the frozen checks")
        if any(c.get("status") not in ("met", "partial", "miss") for c in checks):
            raise ValueError("Invalid Judge check status")
        for key in dimension.get("memoryRubricIds", []):
            if not ID.fullmatch(key) or decisions.get(key) not in ("additional", "interpret", "new_dimension"):
                raise ValueError("Rubric is not activated in the frozen plan")
            used.add(key)
    return used


def record_use(root, run_id, plan, results):
    if not run_id.strip():
        raise ValueError("runId is required")
    ids = used_ids(plan, results)
    if not ids:
        return {"marker": "MEMORY_USAGE_RECORDED", "added": 0}
    with locked(root):
        data = load_stats(root)
        added = 0
        for key in ids:
            # A frozen Loop may finish after its rubric was merged. Retain provenance
            # and forward the event to the surviving rubric without double counting.
            visited = set()
            while key:
                if key in visited:
                    raise ValueError("Cyclic rubric merge lineage")
                visited.add(key)
                entry = data["rubrics"].setdefault(key, empty_entry())
                if run_id not in entry["runs"]:
                    entry["runs"][run_id] = now()
                    recount(entry)
                    added += 1
                key = entry.get("mergedInto")
        if added:
            save(root, data)
    return {"marker": "MEMORY_USAGE_RECORDED", "added": added}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("check")
    p.add_argument("--memory", type=Path, help="Optional proposed MEMORY.md; read-only")
    p = sub.add_parser("sync")
    p.add_argument("--changes", required=True, type=Path)
    p = sub.add_parser("record-use")
    p.add_argument("--run-id", required=True)
    p.add_argument("--plan", required=True, type=Path)
    p.add_argument("--result", required=True, action="append", type=Path)
    args = parser.parse_args()
    try:
        if not args.root.is_absolute() or not args.root.is_dir():
            raise ValueError("memoryRoot must be an existing absolute directory; no fallback")
        if args.command == "check":
            result = check(args.root, args.memory or args.root / "MEMORY.md")
        elif args.command == "sync":
            result = sync(args.root, read_json(args.changes))
        else:
            result = record_use(args.root, args.run_id, read_json(args.plan), [read_json(p) for p in args.result])
        print(json.dumps(result, ensure_ascii=False))
        return 2 if result["marker"] == "MEMORY_CAPACITY_EXCEEDED" else 0
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        print(json.dumps({"marker": "MEMORY_STATS_FAILED", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
