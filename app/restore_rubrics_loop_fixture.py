# -*- coding: utf-8 -*-
"""Restore a saved Rubrics Loop pre-validation fixture safely.

The WebUI server must be stopped before applying. Existing validation
experiments and their Session/compiled-Skill folders are moved to a timestamped
archive; generation_runs payloads are retained because they can be large and
are no longer reachable after the validation Session is archived.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import time
from typing import Any, Dict


class FixtureError(ValueError):
    pass


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise FixtureError("无法读取 JSON: %s" % path) from exc
    if not isinstance(value, dict):
        raise FixtureError("JSON 必须是对象: %s" % path)
    return value


def plan_restore(runtime_root: Path, fixture_root: Path) -> Dict[str, Any]:
    runtime_root = runtime_root.expanduser().resolve()
    fixture_root = fixture_root.expanduser().resolve()
    manifest = _read_json(fixture_root / "manifest.json")
    session_id = str(manifest.get("source_session_id") or "")
    if not session_id or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for ch in session_id):
        raise FixtureError("manifest 缺少安全的 source_session_id")
    fixture_loop = fixture_root / "rubrics_loop"
    live_session = runtime_root / "sessions" / session_id
    live_loop = live_session / "rubrics_loop"
    if not fixture_loop.is_dir():
        raise FixtureError("fixture 缺少 rubrics_loop: %s" % fixture_loop)
    if not (live_session / "state.json").is_file():
        raise FixtureError("live source Session 不存在: %s" % live_session)

    experiments = []
    experiments_root = live_loop / "experiments"
    for path in sorted(experiments_root.glob("*.json")):
        value = _read_json(path)
        experiments.append({
            "experiment_id": value.get("experiment_id"),
            "experiment_session_id": value.get("experiment_session_id"),
            "path": str(path),
        })
    return {
        "runtime_root": str(runtime_root),
        "fixture_root": str(fixture_root),
        "source_session_id": session_id,
        "live_loop": str(live_loop),
        "candidate_id": manifest.get("candidate_id"),
        "draft_id": manifest.get("draft_id"),
        "experiments_to_archive": experiments,
    }


def apply_restore(runtime_root: Path, fixture_root: Path) -> Dict[str, Any]:
    plan = plan_restore(runtime_root, fixture_root)
    runtime_root = Path(plan["runtime_root"])
    fixture_root = Path(plan["fixture_root"])
    live_loop = Path(plan["live_loop"])
    stamp = time.strftime("%Y%m%dT%H%M%S")
    archive = (
        runtime_root / "state_snapshots" / "_restore_archives"
        / plan["source_session_id"] / stamp
    )
    if archive.exists():
        raise FixtureError("恢复存档目录已存在: %s" % archive)
    archive.mkdir(parents=True)

    archived_sessions = []
    for item in plan["experiments_to_archive"]:
        experiment_session_id = str(item.get("experiment_session_id") or "")
        if not experiment_session_id:
            continue
        for source, group in (
            (runtime_root / "sessions" / experiment_session_id, "sessions"),
            (
                runtime_root / "generation_runs" / "_session_skills"
                / experiment_session_id,
                "session-skills",
            ),
        ):
            if not source.exists():
                continue
            target_root = archive / group
            target_root.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target_root / source.name))
        archived_sessions.append(experiment_session_id)

    old_loop = archive / "source-session" / "rubrics_loop"
    old_loop.parent.mkdir(parents=True, exist_ok=True)
    if live_loop.exists():
        shutil.move(str(live_loop), str(old_loop))
    try:
        shutil.copytree(fixture_root / "rubrics_loop", live_loop)
    except Exception:
        if live_loop.exists():
            shutil.rmtree(live_loop)
        if old_loop.exists():
            shutil.move(str(old_loop), str(live_loop))
        raise

    result = {
        **plan,
        "status": "restored",
        "archive_root": str(archive),
        "archived_validation_sessions": archived_sessions,
        "restored_at": round(time.time(), 3),
    }
    (archive / "restore_result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="恢复 Rubrics Loop 的验证前测试起点"
    )
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument(
        "--apply", action="store_true",
        help="实际恢复；不传时只打印计划",
    )
    args = parser.parse_args()
    result = (
        apply_restore(args.runtime_root, args.fixture)
        if args.apply
        else plan_restore(args.runtime_root, args.fixture)
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
