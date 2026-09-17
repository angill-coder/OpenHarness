"""Resolve the current WorkBuddy host model from trusted session traces."""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


SIDECAR_SUFFIX = ".session.json"
SESSION_ID_PATTERN = re.compile(r"^[A-Za-z0-9-]{8,128}$")
ACTIVE_SESSION_MAX_AGE_MS = 10 * 60 * 1000


class HostModelResolutionError(RuntimeError):
    """The WorkBuddy session or its exact request model could not be resolved."""


def _workbuddy_home() -> Path:
    configured = (
        os.environ.get("RESEARCH_REPORT_LOOP_WB_HOME")
        or os.environ.get("CODEBUDDY_CONFIG_DIR")
    )
    return Path(configured).expanduser() if configured else Path.home() / ".workbuddy"


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HostModelResolutionError(f"invalid host session metadata: {path}") from exc
    if not isinstance(value, dict):
        raise HostModelResolutionError(f"invalid host session metadata: {path}")
    return value


def _valid_session_id(value: Any) -> str | None:
    session_id = str(value or "").strip()
    return session_id if SESSION_ID_PATTERN.fullmatch(session_id) else None


def _path_is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _session_from_sidecar(job_path: Path) -> str | None:
    sidecar = Path(f"{job_path}{SIDECAR_SUFFIX}")
    if not sidecar.is_file():
        return None
    payload = _read_object(sidecar)
    if payload.get("version") != 1:
        raise HostModelResolutionError("invalid host session sidecar version")
    session_id = _valid_session_id(payload.get("sessionId"))
    if not session_id:
        raise HostModelResolutionError("invalid host session id in sidecar")
    return session_id


def _single_active_session(job_path: Path, home: Path) -> str:
    sessions_dir = home / "sessions"
    now_ms = int(time.time() * 1000)
    matches: list[tuple[int, str]] = []
    for path in sessions_dir.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("kind") != "interactive":
            continue
        session_id = _valid_session_id(payload.get("sessionId"))
        cwd_value = str(payload.get("cwd") or "").strip()
        heartbeat = payload.get("lastHeartbeat")
        if not session_id or not cwd_value or not isinstance(heartbeat, (int, float)):
            continue
        if now_ms - int(heartbeat) > ACTIVE_SESSION_MAX_AGE_MS:
            continue
        if _path_is_within(job_path.parent, Path(cwd_value)):
            matches.append((int(heartbeat), session_id))
    unique = {session_id for _, session_id in matches}
    if not unique:
        raise HostModelResolutionError("host_session_unresolved")
    if len(unique) > 1:
        raise HostModelResolutionError("host_session_ambiguous")
    return matches[0][1]


def _find_trace(home: Path, session_id: str) -> Path:
    matches = [
        path
        for path in (home / "projects").rglob(f"{session_id}.jsonl")
        if "subagents" not in path.parts
    ]
    if len(matches) != 1:
        code = "host_trace_unresolved" if not matches else "host_trace_ambiguous"
        raise HostModelResolutionError(code)
    return matches[0]


def _request_model_id(trace_path: Path, session_id: str) -> str:
    latest: tuple[int, str] | None = None
    try:
        with trace_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(event, dict) or event.get("sessionId") != session_id:
                    continue
                provider = event.get("providerData")
                if not isinstance(provider, dict):
                    continue
                model_id = str(provider.get("requestModelId") or "").strip()
                if not model_id or provider.get("agent") not in (None, "cli"):
                    continue
                timestamp = event.get("timestamp")
                order = int(timestamp) if isinstance(timestamp, (int, float)) else 0
                if latest is None or order >= latest[0]:
                    latest = (order, model_id)
    except OSError as exc:
        raise HostModelResolutionError("host_trace_unreadable") from exc
    if latest is None:
        raise HostModelResolutionError("host_model_unresolved")
    return latest[1]


def resolve_host_model_id(job_path: Path) -> str:
    """Return the exact main-agent request model for the Job's WorkBuddy session."""
    explicit = str(os.environ.get("RESEARCH_REPORT_LOOP_HOST_MODEL_ID") or "").strip()
    if explicit:
        return explicit
    resolved_job = job_path.expanduser().resolve()
    home = _workbuddy_home()
    session_id = _session_from_sidecar(resolved_job)
    if session_id is None:
        session_id = _single_active_session(resolved_job, home)
    return _request_model_id(_find_trace(home, session_id), session_id)
