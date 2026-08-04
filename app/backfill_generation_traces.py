# -*- coding: utf-8 -*-
"""Backfill compact Dashboard traces from WB batch generation directories.

The Dashboard deliberately reads traces from the matching outputs.jsonl row.
This utility bridges historical/imported reports that only retained a
generation_id back to the WB ``generate``/``generation_runs`` artifacts.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return default


def _trace_text(value: Any, limit: int = 12000) -> str:
    if value in (None, ""):
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            text = str(value)
    return text if len(text) <= limit else text[:limit] + "\n... [truncated]"


def _attempt_trace_dir(run_dir: Path, case_id: str, attempt: Dict[str, Any]) -> Optional[Path]:
    run_id = str(attempt.get("wb_run_id") or "")
    if run_id:
        candidate = run_dir / run_id / "cases" / case_id / "trace"
        if (candidate / "1_operations.json").is_file():
            return candidate

    # Imported job JSON often contains an absolute path from the source host.
    # Resolve by the portable tail rather than trusting that absolute prefix.
    raw_path = str(attempt.get("trace_path") or "").replace("\\", "/")
    marker = "/cases/"
    if marker in raw_path:
        run_id = raw_path.rsplit(marker, 1)[0].rstrip("/").rsplit("/", 1)[-1]
        candidate = run_dir / run_id / "cases" / case_id / "trace"
        if (candidate / "1_operations.json").is_file():
            return candidate

    candidates = sorted(run_dir.glob("case-*/cases/%s/trace" % case_id))
    candidates = [path for path in candidates if (path / "1_operations.json").is_file()]
    return candidates[-1] if candidates else None


def compact_trace(trace_dir: Path, attempt: Dict[str, Any]) -> Dict[str, Any]:
    raw_operations = _read_json(trace_dir / "1_operations.json", [])
    operations = []
    for operation in raw_operations if isinstance(raw_operations, list) else []:
        if not isinstance(operation, dict):
            continue
        operations.append({
            "name": str(operation.get("name") or "tool"),
            "status": str(operation.get("status") or "unknown"),
            "round": operation.get("round_index"),
            "durationMs": operation.get("duration_ms"),
            "input": _trace_text(operation.get("input")),
            "result": _trace_text(operation.get("result")),
        })

    rounds = []
    rounds_root = trace_dir / "rounds"
    if rounds_root.is_dir():
        for result_path in sorted(rounds_root.glob("*/result.json")):
            item = _read_json(result_path, {})
            if not isinstance(item, dict):
                continue
            rounds.append({
                "name": result_path.parent.name,
                "status": str(item.get("status") or "unknown"),
                "durationMs": item.get("duration_ms"),
                "output": _trace_text(item.get("final_output")),
            })

    observed = attempt.get("observed_models") or []
    return {
        "status": str(attempt.get("wb_status") or attempt.get("status") or "unknown"),
        "model": observed[0] if observed else attempt.get("configured_model"),
        "durationMs": attempt.get("duration_ms"),
        "attempt": attempt.get("attempt"),
        "sessionId": attempt.get("wb_session_id"),
        "operations": operations,
        "rounds": rounds,
        "conversation": [],
        "conversationAvailable": (trace_dir.parent / "conversation.md").is_file(),
    }


def build_trace_index(runs_root: Path) -> Dict[Tuple[str, str], Dict[str, Any]]:
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for result_path in sorted(runs_root.glob("gen-*/generation_result.json")):
        payload = _read_json(result_path, {})
        generation_id = str(payload.get("generation_id") or result_path.parent.name)
        cases = payload.get("cases") if isinstance(payload, dict) else []
        for case in cases if isinstance(cases, list) else []:
            if not isinstance(case, dict):
                continue
            case_id = str(case.get("openharness_case_id") or case.get("case_id") or "")
            attempts = case.get("attempts") or []
            for attempt in reversed(attempts if isinstance(attempts, list) else []):
                if not isinstance(attempt, dict):
                    continue
                trace_dir = _attempt_trace_dir(result_path.parent, case_id, attempt)
                if trace_dir:
                    index[(generation_id, case_id)] = compact_trace(trace_dir, attempt)
                    break
    return index


def backfill_sessions(
    sessions_root: Path,
    trace_index: Dict[Tuple[str, str], Dict[str, Any]],
    *,
    dry_run: bool = False,
    backup_suffix: str = ".before-trace-backfill",
) -> Dict[str, int]:
    stats = {"files": 0, "rows": 0, "matched": 0, "missing": 0}
    for outputs_path in sorted(sessions_root.glob("*/outputs.jsonl")):
        rows = []
        changed = False
        for raw_line in outputs_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            row = json.loads(raw_line)
            stats["rows"] += 1
            if row.get("generation_trace"):
                rows.append(row)
                continue
            generation_id = str(row.get("generation_id") or "")
            case_id = str(row.get("case_id") or "")
            if not generation_id:
                rows.append(row)
                continue
            trace = trace_index.get((generation_id, case_id))
            if trace:
                row["generation_trace"] = trace
                stats["matched"] += 1
                changed = True
            else:
                stats["missing"] += 1
            rows.append(row)
        if not changed:
            continue
        stats["files"] += 1
        if dry_run:
            continue
        backup = outputs_path.with_name(outputs_path.name + backup_suffix)
        if backup_suffix and not backup.exists():
            shutil.copy2(outputs_path, backup)
        temp = outputs_path.with_name(outputs_path.name + ".tmp")
        temp.write_text(
            "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
            encoding="utf-8",
        )
        os.replace(temp, outputs_path)
    return stats


def main(argv: Optional[Iterable[str]] = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs-root", required=True, type=Path)
    parser.add_argument("--sessions-root", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args(argv)
    index = build_trace_index(args.runs_root)
    stats = backfill_sessions(
        args.sessions_root,
        index,
        dry_run=args.dry_run,
        backup_suffix="" if args.no_backup else ".before-trace-backfill",
    )
    print(json.dumps({"indexed": len(index), **stats}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
