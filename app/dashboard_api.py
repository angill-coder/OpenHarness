"""Read-only data helpers for the integrated realtime dashboard."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import sys
import threading


HARNESS = Path(__file__).resolve().parents[1] / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

import judge as judge_mod  # noqa: E402


SESSION_FILES = {
    "meta.json",
    "state.json",
    "check_judgments.jsonl",
}

VIRTUAL_SESSIONS_ROOT = "app/sessions"

RUBRIC_GUIDE_FILES = {
    "research_insight": (
        "".join(chr(code) for code in (0x8C03, 0x7814, 0x6D1E, 0x5BDF, 0x6C47, 0x62A5, 0x52A9, 0x624B))
        + "_Rubric"
        + "".join(chr(code) for code in (0x843D, 0x5730, 0x6587, 0x6863))
        + ".md"
    ),
}

_SUMMARY_CACHE: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}
_JUDGMENT_CACHE: dict[str, tuple[tuple[object, ...], dict[tuple[str, str], dict[str, object]]]] = {}
_CACHE_LOCK = threading.RLock()



def _safe_generation_segment(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not value or value in {".", ".."} or Path(value).name != value:
        raise ValueError("invalid %s" % label)
    return value


def _read_json_document(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FileNotFoundError("invalid generation artifact: %s" % path) from exc
    if not isinstance(payload, dict):
        raise FileNotFoundError("generation artifact is not an object: %s" % path)
    return payload


def _portable_generation_path(generation_root: Path, raw_path: str) -> Path:
    """Resolve a stored skill_ref after the repository was moved or cloned."""
    raw = str(raw_path or "").replace("\\", "/").strip()
    if not raw:
        raise ValueError("empty generation artifact path")
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    lowered = [part.lower() for part in parts]
    if "generation_runs" in lowered:
        tail = parts[lowered.index("generation_runs") + 1:]
        candidate = generation_root.joinpath(*tail).resolve()
    else:
        path = Path(raw).expanduser()
        candidate = (path if path.is_absolute() else generation_root / path).resolve()
    candidate.relative_to(generation_root)
    return candidate


def _generation_jobs(
    sessions_root: Path,
    session_id: str,
    *,
    version: str | None = None,
    generation_id: str | None = None,
) -> list[dict]:
    jobs_root = (sessions_root.resolve() / session_id / "generation_jobs").resolve()
    jobs_root.relative_to(sessions_root.resolve())
    if not jobs_root.is_dir():
        return []
    matches = []
    for path in sorted(jobs_root.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if version is not None and str(payload.get("skill_version") or "") != version:
            continue
        if generation_id is not None and str(payload.get("generation_id") or "") != generation_id:
            continue
        matches.append(payload)
    return sorted(
        matches,
        key=lambda item: float(item.get("created_at") or item.get("updated_at") or 0),
        reverse=True,
    )


def generation_skill_document(
    root: Path,
    sessions_root: Path,
    session_id: str,
    version: str,
    generation_root: Path | None = None,
) -> dict[str, object]:
    """Load the exact frozen Skill for a session/version from generation_runs."""
    session_id = _safe_generation_segment(session_id, "session id")
    version = _safe_generation_segment(version, "skill version")
    generation_root = (
        generation_root or (root.resolve() / "generation_runs")
    ).expanduser().resolve()
    version_root = (
        generation_root / "_session_skills" / session_id / version
    ).resolve()
    version_root.relative_to(generation_root)

    jobs = _generation_jobs(
        sessions_root, session_id, version=version
    )
    job_refs = []
    for job in jobs:
        if str(job.get("skill_mode") or "") != "session_artifact":
            continue
        try:
            job_refs.append(
                _portable_generation_path(generation_root, job.get("skill_ref"))
            )
            break
        except (TypeError, ValueError, OSError):
            continue

    candidates = job_refs
    if not candidates and version_root.is_dir():
        candidates = sorted(
            path.parent
            for path in version_root.glob("*/*/SKILL.md")
        )

    valid = []
    seen = set()
    for source_root in candidates:
        try:
            source_root = source_root.resolve()
            source_root.relative_to(generation_root)
        except (ValueError, OSError):
            continue
        skill_path = source_root / "SKILL.md"
        instruction_path = source_root / "references" / "instructions.md"
        if not skill_path.is_file() or not instruction_path.is_file():
            continue
        key = str(source_root).lower()
        if key not in seen:
            seen.add(key)
            valid.append((source_root, skill_path, instruction_path))

    if not valid:
        raise FileNotFoundError(
            "generation_runs has no exact Skill artifact for %s/%s"
            % (session_id, version)
        )
    if len(valid) != 1:
        raise FileNotFoundError(
            "generation_runs has ambiguous Skill artifacts for %s/%s"
            % (session_id, version)
        )

    source_root, skill_path, instruction_path = valid[0]
    return {
        "skill_md": skill_path.read_text(encoding="utf-8"),
        "instruction_md": instruction_path.read_text(encoding="utf-8"),
        "source": (
            "runtime:generation_runs/"
            + source_root.relative_to(generation_root).as_posix()
        ),
        "session_id": session_id,
        "version": version,
    }


def _trace_text(value, limit: int = 12000) -> str:
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


def _attempt_trace_directory(
    run_root: Path,
    case_id: str,
    attempt: dict,
) -> Path | None:
    run_id = str(attempt.get("wb_run_id") or "")
    candidates = []
    if run_id and Path(run_id).name == run_id:
        candidates.append(run_root / run_id / "cases" / case_id / "trace")
    raw_path = str(attempt.get("trace_path") or "").replace("\\", "/")
    marker = "/cases/"
    if marker in raw_path:
        portable_run_id = raw_path.rsplit(marker, 1)[0].rstrip("/").rsplit("/", 1)[-1]
        if portable_run_id and Path(portable_run_id).name == portable_run_id:
            candidates.append(
                run_root / portable_run_id / "cases" / case_id / "trace"
            )
    candidates.extend(sorted(run_root.glob("case-*/cases/%s/trace" % case_id)))
    for candidate in candidates:
        try:
            candidate = candidate.resolve()
            candidate.relative_to(run_root.resolve())
        except (ValueError, OSError):
            continue
        if candidate.is_dir() and (
            (candidate / "1_operations.json").is_file()
            or (candidate / "rounds").is_dir()
            or (candidate.parent / "conversation.md").is_file()
        ):
            return candidate
    return None


def _generation_result_for(generation_root: Path, generation_id: str) -> tuple[Path, dict]:
    result_path = (generation_root / generation_id / "generation_result.json").resolve()
    result_path.relative_to(generation_root.resolve())
    if not result_path.is_file():
        raise FileNotFoundError("generation_runs has no generation_result for %s" % generation_id)
    payload = _read_json_document(result_path)
    if str(payload.get("generation_id") or result_path.parent.name) != generation_id:
        raise FileNotFoundError("generation_result generation id mismatch")
    return result_path, payload


def _generation_case_directories(generation_root: Path, generation_id: str):
    """Discover live Case directories for one linked generation run."""
    result_path, payload = _generation_result_for(generation_root, generation_id)
    generation_dir = result_path.parent.resolve()
    directories = {}
    for case_root in sorted(generation_dir.glob("*/cases/*")):
        if not case_root.is_dir():
            continue
        case_root = case_root.resolve()
        try:
            case_root.relative_to(generation_dir)
        except ValueError:
            continue
        run_root = case_root.parent.parent
        if run_root.parent != generation_dir:
            continue
        directories.setdefault(case_root.name, []).append((run_root, case_root))
    return result_path, payload, directories
def _generation_case_context(generation_root: Path, generation_id: str, case_id: str):
    result_path, payload, directories = _generation_case_directories(generation_root, generation_id)
    case_payload = next((item for item in payload.get("cases", []) if isinstance(item, dict)
                         and str(item.get("openharness_case_id") or item.get("case_id") or "") == case_id), None)
    attempts = (case_payload or {}).get("attempts") or []
    for attempt in reversed(attempts if isinstance(attempts, list) else []):
        if not isinstance(attempt, dict):
            continue
        run_id = str(attempt.get("wb_run_id") or "")
        if not run_id or Path(run_id).name != run_id:
            continue
        run_root = (result_path.parent / run_id).resolve()
        case_root = (run_root / "cases" / case_id).resolve()
        try:
            case_root.relative_to(result_path.parent.resolve())
        except ValueError:
            continue
        if case_root.is_dir():
            return result_path, payload, case_payload, attempt, run_root, case_root
    for run_root, case_root in reversed(directories.get(case_id, [])):
        return result_path, payload, case_payload or {"case_id": case_id}, {}, run_root, case_root
    raise FileNotFoundError("generation_runs has no exact Case directory")


def _usage_numbers(value: object) -> dict[str, int]:
    value = value if isinstance(value, dict) else {}
    keys = ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")
    result = {key: int(value.get(key) or 0) for key in keys}
    result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def _case_results_metrics(case_root: Path, run_root: Path, case_id: str, attempt: dict):
    # Prefer the requested per-Case file. Current runner output keeps the same
    # authoritative results document at the WorkBuddy run root.
    for results_path in (case_root / "results.json", run_root / "results.json"):
        if not results_path.is_file():
            continue
        payload = _read_json_document(results_path)
        summary = next((item for item in (payload.get("summaries") or [])
                        if isinstance(item, dict) and str(item.get("case_id") or "") == case_id), None)
        if summary is None and results_path.parent == case_root:
            summary = payload
        if not isinstance(summary, dict):
            continue
        steps = []
        aggregate = {key: 0 for key in ("input_tokens", "output_tokens",
                    "cache_read_input_tokens", "cache_creation_input_tokens", "total_tokens")}
        for index, item in enumerate(summary.get("rounds") or [], start=1):
            if not isinstance(item, dict):
                continue
            usage = _usage_numbers(item.get("usage"))
            for key, value in usage.items():
                aggregate[key] += value
            event = item.get("result_event") if isinstance(item.get("result_event"), dict) else {}
            steps.append({"step": index, "durationMs": item.get("duration_ms"),
                          "apiDurationMs": event.get("duration_api_ms"), "usage": usage})
        if not steps:
            aggregate = _usage_numbers(attempt.get("usage"))
        return ({"durationMs": summary.get("duration_ms", attempt.get("duration_ms")),
                 "usage": aggregate, "steps": steps}, results_path)
    return ({"durationMs": attempt.get("duration_ms"),
             "usage": _usage_numbers(attempt.get("usage")), "steps": []}, None)

def _process_summary(kind: str, name: str, payload: object) -> str:
    if isinstance(payload, dict):
        for key in ("description", "subject", "file_path", "path", "command", "query"):
            value = str(payload.get(key) or "").strip()
            if value:
                return value[:180]
    if kind == "thinking":
        lines = str(payload or "").strip().splitlines()
        return (lines[0] if lines else "模型正在分析任务并确定下一步")[:180]
    return name


def _portable_trace_string(value: object, case_root: Path) -> str:
    text = _trace_text(value)
    generation_root = next(
        (path for path in (case_root, *case_root.parents) if path.name == "generation_runs"),
        None,
    )
    if generation_root is None:
        return text
    for source in {str(generation_root), generation_root.as_posix()}:
        text = text.replace(source, "runtime:generation_runs")
        text = text.replace(source.replace("\\", "\\\\"), "runtime:generation_runs")
    text = re.sub(
        r'(?i)[a-z]:[^"\r\n]*?generation_runs',
        "runtime:generation_runs",
        text,
    )
    return text

def _generation_conversation_hierarchy(case_root: Path) -> list[dict[str, object]]:
    trace_root = case_root / "trace"
    operation_path = trace_root / "1_operations.json"
    try:
        operations = json.loads(operation_path.read_text(encoding="utf-8"))
        operations = operations if isinstance(operations, list) else []
    except (OSError, UnicodeError, json.JSONDecodeError):
        operations = []

    processes: dict[int, list[dict[str, object]]] = {}
    seen_tool_ids: set[str] = set()
    for item in operations:
        if not isinstance(item, dict):
            continue
        round_index = int(item.get("round_index") or 0)
        name = str(item.get("name") or "Tool")
        tool_id = str(item.get("tool_use_id") or "")
        seen_tool_ids.add(tool_id)
        kind = "subagent" if name.lower() in {"agent", "task", "teamcreate", "sendmessage"} else "tool"
        processes.setdefault(round_index, []).append({
            "id": tool_id or "tool-%s-%s" % (round_index, len(processes.get(round_index, []))),
            "kind": kind, "name": name,
            "summary": _portable_trace_string(_process_summary(kind, name, item.get("input")), case_root),
            "status": str(item.get("status") or "unknown"),
            "durationMs": item.get("duration_ms"),
            "sequenceMs": item.get("started_elapsed_ms"),
            "detail": {
                "input": _portable_trace_string(item.get("input"), case_root),
                "result": _portable_trace_string(item.get("result"), case_root),
            },
        })

    event_path = trace_root / "2_events.jsonl"
    if event_path.is_file():
        for line_number, raw in enumerate(event_path.read_text(encoding="utf-8").splitlines()):
            try:
                wrapper = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if not isinstance(wrapper, dict):
                continue
            event = wrapper.get("event")
            if not isinstance(event, dict) or event.get("type") != "assistant":
                continue
            message = event.get("message") if isinstance(event.get("message"), dict) else {}
            round_index = int(wrapper.get("round_index") or 0)
            parent_id = str(event.get("parent_tool_use_id") or "")
            for content_index, content in enumerate(message.get("content") or []):
                if not isinstance(content, dict):
                    continue
                content_type = str(content.get("type") or "")
                if content_type == "tool_use":
                    tool_id = str(content.get("id") or "")
                    if tool_id in seen_tool_ids:
                        continue
                    name = str(content.get("name") or "Tool")
                    kind = "subagent" if name.lower() in {"agent", "task", "teamcreate", "sendmessage"} else "tool"
                    processes.setdefault(round_index, []).append({
                        "id": tool_id or "event-tool-%s-%s" % (line_number, content_index),
                        "kind": kind, "name": name,
                        "summary": _portable_trace_string(_process_summary(kind, name, content.get("input")), case_root),
                        "status": "recorded", "durationMs": None,
                        "sequenceMs": wrapper.get("round_elapsed_ms"),
                        "detail": {"input": _portable_trace_string(content.get("input"), case_root), "result": ""},
                    })
                    continue
                detail_text = content.get("thinking") if content_type == "thinking" else content.get("text")
                if not detail_text or (content_type != "thinking" and not parent_id):
                    continue
                kind = "subagent" if parent_id else "thinking"
                name = "Sub-agent" if parent_id else "深度思考"
                processes.setdefault(round_index, []).append({
                    "id": "event-%s-%s" % (line_number, content_index),
                    "kind": kind, "name": name,
                    "summary": _portable_trace_string(_process_summary(kind, name, detail_text), case_root),
                    "status": "recorded", "durationMs": None,
                    "sequenceMs": wrapper.get("round_elapsed_ms"),
                    "parentToolUseId": parent_id or None,
                    "detail": {"text": _portable_trace_string(detail_text, case_root)},
                })

    turns: list[dict[str, object]] = []
    rounds_root = trace_root / "rounds"
    if not rounds_root.is_dir():
        return turns
    for round_root in sorted(path for path in rounds_root.iterdir() if path.is_dir()):
        try:
            request = _read_json_document(round_root / "request.json")
            result = _read_json_document(round_root / "result.json")
        except FileNotFoundError:
            continue
        round_index = int(request.get("round_index") or 0)
        round_processes = sorted(
            processes.get(round_index, []),
            key=lambda item: (item.get("sequenceMs") is None, item.get("sequenceMs") or 0),
        )
        turns.append({
            "role": "user", "round": round_index + 1,
            "label": str(request.get("label") or round_root.name),
            "content": str(request.get("prompt") or ""),
        })
        turns.append({
            "role": "agent", "round": round_index + 1,
            "label": str(result.get("label") or round_root.name),
            "content": str(result.get("final_output") or ""),
            "status": str(result.get("status") or "unknown"),
            "durationMs": result.get("duration_ms"),
            "processes": round_processes,
        })
    return turns

def _linked_generation(
    sessions_root: Path, session_id: str, version: str, generation_id: str,
    generation_root: Path,
):
    result_path, payload = _generation_result_for(generation_root, generation_id)
    jobs = _generation_jobs(sessions_root, session_id, version=version, generation_id=generation_id)
    if str(payload.get("session_id") or "") not in ("", session_id):
        raise FileNotFoundError("generation artifact session mismatch")
    if str(payload.get("skill_version") or "") not in ("", version):
        raise FileNotFoundError("generation artifact version mismatch")
    if not jobs and not (payload.get("session_id") == session_id and payload.get("skill_version") == version):
        raise FileNotFoundError("generation artifact cannot be linked to the requested session/version")
    return result_path, payload


def generation_case_report_document(
    root: Path, sessions_root: Path, session_id: str, version: str, case_id: str,
    generation_root: Path | None = None,
) -> dict[str, object]:
    session_id = _safe_generation_segment(session_id, "session id")
    version = _safe_generation_segment(version, "skill version")
    case_id = _safe_generation_segment(case_id, "case id")
    generation_root = (generation_root or root.resolve() / "generation_runs").resolve()
    jobs = _generation_jobs(sessions_root, session_id, version=version)
    for job in jobs:
        generation_id = str(job.get("generation_id") or "")
        if not generation_id:
            continue
        try:
            _linked_generation(sessions_root, session_id, version, generation_id, generation_root)
            _, _, _, _, _, case_root = _generation_case_context(
                generation_root, generation_id, case_id
            )
        except (FileNotFoundError, ValueError, OSError):
            continue
        report_path = case_root / "artifacts" / "report.md"
        if not report_path.is_file():
            continue
        return {
            "version": version, "case_id": case_id, "generation_id": generation_id,
            "report_text": report_path.read_text(encoding="utf-8"),
            "source": "runtime:generation_runs/" + report_path.relative_to(generation_root).as_posix(),
        }
    raise FileNotFoundError("generation_runs has no exact report.md for this Skill version and Case")


def generation_trace_document(
    root: Path, sessions_root: Path, session_id: str, version: str, case_id: str,
    generation_id: str, generation_root: Path | None = None,
) -> dict[str, object]:
    session_id = _safe_generation_segment(session_id, "session id")
    version = _safe_generation_segment(version, "skill version")
    case_id = _safe_generation_segment(case_id, "case id")
    generation_id = _safe_generation_segment(generation_id, "generation id")
    generation_root = (generation_root or root.resolve() / "generation_runs").resolve()
    _linked_generation(sessions_root, session_id, version, generation_id, generation_root)
    _, _, _, attempt, run_root, case_root = _generation_case_context(
        generation_root, generation_id, case_id
    )
    conversation_path = case_root / "conversation.md"
    if not conversation_path.is_file():
        raise FileNotFoundError("generation_runs has no exact conversation.md for this Case")
    metrics, results_path = _case_results_metrics(case_root, run_root, case_id, attempt)
    hierarchy = _generation_conversation_hierarchy(case_root)
    observed = attempt.get("observed_models") or []
    return {
        "status": str(attempt.get("wb_status") or attempt.get("status") or "unknown"),
        "model": observed[0] if observed else attempt.get("configured_model"),
        "durationMs": metrics.get("durationMs"),
        "metrics": metrics,
        "attempt": attempt.get("attempt"),
        "sessionId": attempt.get("wb_session_id"),
        "operations": [], "rounds": [], "conversation": hierarchy,
        "conversationText": conversation_path.read_text(encoding="utf-8"),
        "conversationAvailable": True,
        "source": "runtime:generation_runs/" + conversation_path.relative_to(generation_root).as_posix(),
        "resultsSource": (
            "runtime:generation_runs/" + results_path.relative_to(generation_root).as_posix()
            if results_path else None
        ),
        "generationId": generation_id, "caseId": case_id, "version": version,
    }

def _file_fingerprint(path: Path) -> tuple[object, ...]:
    try:
        stat = path.stat()
    except OSError:
        return (False, 0, 0)
    return (True, stat.st_mtime_ns, stat.st_size)


def _compact_version(entry: object) -> dict[str, object] | None:
    if not isinstance(entry, dict):
        return None
    skill = entry.get("skill") if isinstance(entry.get("skill"), dict) else entry
    version = str(skill.get("version") or "")
    if not version:
        return None
    return {
        key: skill.get(key)
        for key in ("version", "parent_version", "id", "name", "changelog")
        if key in skill
    }


def _compact_case(item: object) -> dict[str, object] | None:
    if not isinstance(item, dict) or not item.get("case_id"):
        return None
    compact = {
        key: item.get(key)
        for key in (
            "case_id", "topic", "audience", "key_questions",
            "required_sections",
        )
        if key in item
    }
    raw_input = item.get("input")
    if isinstance(raw_input, dict) and raw_input.get("brief"):
        compact["input"] = {"brief": raw_input.get("brief")}
    raw_metadata = item.get("metadata")
    if isinstance(raw_metadata, dict):
        metadata_keys = (
            "source_file", "display_name", "range", "experiment_input_mode",
        )
        compact["metadata"] = {
            key: raw_metadata.get(key)
            for key in metadata_keys
            if key in raw_metadata
        }
    if not compact.get("key_questions"):
        turns = item.get("turns") or []
        compact["turns"] = [
            {"role": "user", "content": turn.get("content")}
            for turn in turns
            if isinstance(turn, dict)
            and turn.get("role") == "user"
            and turn.get("content")
        ]
    return compact

def _compact_state(state: dict[str, object]) -> dict[str, object]:
    scalar_keys = (
        "id", "product_id", "optimizer_mode", "_saved_at", "session_label",
        "experiment_data", "experiment_optimizer", "experiment_user", "experiment_owner",
        "experiment_judge", "judge_version",
    )
    compact = {key: state.get(key) for key in scalar_keys if key in state}
    optimizer_history = state.get("opt_history") or []
    if isinstance(optimizer_history, list):
        latest_optimizer_run = next((
            item for item in reversed(optimizer_history)
            if isinstance(item, dict)
            and (item.get("model") or item.get("llm_backend"))
        ), None)
        if latest_optimizer_run:
            compact["optimizer_runtime"] = {
                "model": latest_optimizer_run.get("model"),
                "llm_backend": latest_optimizer_run.get("llm_backend"),
                "reasoning_effort": latest_optimizer_run.get(
                    "reasoning_effort"
                ),
            }
    compact["rubric"] = state.get("rubric") or {}
    compact["versions"] = [
        value for value in (
            _compact_version(item) for item in (state.get("versions") or [])
        ) if value is not None
    ]
    compact["cases"] = [
        value for value in (
            _compact_case(item) for item in (state.get("cases") or [])
        ) if value is not None
    ]
    return compact


def _event_runtime_models(
    path: Path,
    version_cases: dict[str, list[str]] | None = None,
) -> dict[str, object]:
    """Read compatibility model records from events.jsonl."""
    result: dict[str, object] = {
        "optimizer": None,
        "judge": None,
        "versions": {},
    }
    if not path.is_file():
        return result
    optimizer_events = {
        "version_proposed", "optimizer_run", "optimizer_started",
        "optimizer_completed",
    }
    judge_events = {"run_judge", "run_judge_batch"}
    judge_by_version: dict[str, dict[str, dict[str, object]]] = {}
    with path.open("rb") as stream:
        for raw in stream:
            if not raw.endswith(b"\n") or not raw.strip():
                continue
            try:
                item = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict):
                continue
            event_type = str(item.get("type") or "")
            payload = item.get("payload")
            if not isinstance(payload, dict):
                continue
            stage = None
            if event_type in optimizer_events or event_type.startswith("optimizer_"):
                stage = "optimizer"
            elif event_type in judge_events:
                stage = "judge"
            if stage is None:
                continue
            model = payload.get("model")
            backend = payload.get("llm_backend")
            if not model and not backend:
                continue
            record: dict[str, object] = {
                "model": model,
                "llm_backend": backend,
                "reasoning_effort": payload.get("reasoning_effort"),
                "event_type": event_type,
                "ts": item.get("ts"),
                "source": "events.jsonl",
            }
            result[stage] = record
            version = str(
                payload.get("version") or payload.get("skill_version") or ""
            ).strip()
            if not version:
                continue
            versions = result["versions"]
            assert isinstance(versions, dict)
            bucket = versions.setdefault(version, {
                "optimizer": None,
                "judge": None,
            })
            if stage == "optimizer":
                bucket["optimizer"] = dict(record)
                continue
            raw_case_ids = payload.get("case_ids")
            if isinstance(raw_case_ids, list):
                case_ids = [str(value) for value in raw_case_ids if value]
            else:
                case_id = payload.get("case_id")
                case_ids = [str(case_id)] if case_id else []
            by_case = judge_by_version.setdefault(version, {})
            for case_id in case_ids:
                by_case.setdefault(case_id, dict(record))

    ordered_versions = set(judge_by_version)
    if isinstance(version_cases, dict):
        ordered_versions.update(str(version) for version in version_cases)
    versions = result["versions"]
    assert isinstance(versions, dict)
    for version in ordered_versions:
        bucket = versions.setdefault(version, {
            "optimizer": None,
            "judge": None,
        })
        by_case = judge_by_version.get(version, {})
        ordered_cases = (
            version_cases.get(version, [])
            if isinstance(version_cases, dict) else []
        )

        if ordered_cases:
            requested_case = str(ordered_cases[0])
            first_case = requested_case if requested_case in by_case else None
        else:
            first_case = next(iter(by_case), None)
        if first_case is not None:
            bucket["judge"] = {
                **by_case[first_case],
                "case_id": first_case,
            }
    return result
def _summary_runtime_models(
    events_path: Path,
    version_cases: dict[str, list[str]] | None = None,
    state: dict[str, object] | None = None,
    judgments: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    """Resolve authoritative per-version models, with events as fallback."""
    result = _event_runtime_models(events_path, version_cases)
    versions = result["versions"]
    assert isinstance(versions, dict)

    state = state if isinstance(state, dict) else {}
    history = state.get("opt_history")
    if isinstance(history, list):
        for item in history:
            if not isinstance(item, dict):
                continue
            version = str(item.get("candidate") or "").strip()
            model = item.get("model")
            backend = item.get("llm_backend")
            if not version or (not model and not backend):
                continue
            record: dict[str, object] = {
                "model": model,
                "llm_backend": backend,
                "reasoning_effort": item.get("reasoning_effort"),
                "status": "recorded",
                "source": "state.json",
                "source_field": "opt_history",
            }
            bucket = versions.setdefault(version, {
                "optimizer": None,
                "judge": None,
            })
            bucket["optimizer"] = record
            result["optimizer"] = record

    if str(state.get("v0_strategy") or "") == "base_skill":
        bucket = versions.setdefault("v0", {
            "optimizer": None,
            "judge": None,
        })
        bucket["optimizer"] = {
            "model": None,
            "llm_backend": None,
            "status": "not_called",
            "reason": "base_skill",
            "source": "state.json",
            "source_field": "v0_strategy",
        }

    active_judgments: dict[tuple[str, str], dict[str, object]] = {}
    for item in judgments or []:
        if not isinstance(item, dict):
            continue
        version = str(item.get("version") or "").strip()
        case_id = str(item.get("case_id") or "").strip()
        if not version or not case_id:
            continue
        key = (version, case_id)
        if item.get("invalidated"):
            active_judgments.pop(key, None)
            continue
        if not item.get("model") and not item.get("llm_backend"):
            continue
        active_judgments[key] = {
            "model": item.get("model"),
            "llm_backend": item.get("llm_backend"),
            "reasoning_effort": item.get("reasoning_effort"),
            "ts": item.get("ts"),
            "status": "completed",
            "source": "check_judgments.jsonl",
            "case_id": case_id,
        }

    latest_judge: dict[str, object] | None = None
    if isinstance(version_cases, dict):
        for raw_version, raw_case_ids in version_cases.items():
            version = str(raw_version)
            case_ids = raw_case_ids if isinstance(raw_case_ids, list) else []
            first_case = str(case_ids[0]) if case_ids else ""
            record = active_judgments.get((version, first_case))
            if record is None:
                continue
            bucket = versions.setdefault(version, {
                "optimizer": None,
                "judge": None,
            })
            bucket["judge"] = record
            if latest_judge is None or float(record.get("ts") or 0) >= float(latest_judge.get("ts") or 0):
                latest_judge = record
    if latest_judge is not None:
        result["judge"] = latest_judge
    return result


def _json_sha256(value: object) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _score_summary_judgments(
    judgments: list[dict[str, object]],
    rubric: dict[str, object],
) -> list[dict[str, object]]:
    """Add score fields derived by the authoritative harness Judge rules."""
    current_rubric_sha256 = _json_sha256(rubric)
    scored_rows = []
    for source in judgments:
        row = dict(source)
        if row.get("invalidated"):
            scored_rows.append(row)
            continue

        checks = row.get("checks")
        if not isinstance(checks, dict) or not checks:
            row["scoring_status"] = "missing_checks"
            scored_rows.append(row)
            continue

        recorded_rubric_sha256 = str(
            row.get("rubric_sha256") or ""
        ).strip()
        if (
            recorded_rubric_sha256
            and recorded_rubric_sha256 != current_rubric_sha256
        ):
            row.update({
                "scoring_status": "stale_rubric",
                "current_rubric_sha256": current_rubric_sha256,
            })
            scored_rows.append(row)
            continue

        try:
            row.update(
                judge_mod.score_check_judgment(checks, rubric)
            )
        except (KeyError, TypeError, ValueError) as exc:
            row.update({
                "scoring_status": "invalid_checks",
                "scoring_error": str(exc),
            })
        else:
            row.update({
                "scoring_status": "scored",
                "score_source": "harness/judge.py",
                "current_rubric_sha256": current_rubric_sha256,
            })
        scored_rows.append(row)
    return scored_rows


def _summary_judgments(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    rows: list[dict[str, object]] = []
    allowed = {
        "version", "case_id", "checks", "invalidated", "reason",
        "ts", "llm_backend", "model", "reasoning_effort",
        "report_sha256", "rubric_sha256",
    }
    with path.open("rb") as stream:
        for raw in stream:
            if not raw.endswith(b"\n") or not raw.strip():
                continue
            try:
                item = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(item, dict):
                rows.append({key: item.get(key) for key in allowed if key in item})
    return rows


def _skill_version_sort_key(path: Path) -> tuple[str, int, str]:
    name = path.name
    digits = "".join(char for char in name if char.isdigit())
    prefix = name[:name.find(digits)] if digits else name
    return prefix.lower(), int(digits or -1), name.lower()

def _generation_session_index(
    root: Path, sessions_root: Path, session_id: str, state: dict[str, object]
) -> dict[str, object]:
    generation_root = (root.resolve() / "generation_runs").resolve()
    skills_root = (generation_root / "_session_skills" / session_id).resolve()
    skills_root.relative_to(generation_root)
    state_versions = {
        item["version"]: item for item in (
            _compact_version(value) for value in (state.get("versions") or [])
        ) if item is not None
    }
    state_cases = {
        str(item.get("case_id")): item for item in (state.get("cases") or [])
        if isinstance(item, dict) and item.get("case_id")
    }
    versions = []
    version_cases: dict[str, list[str]] = {}
    version_generations: dict[str, str] = {}
    cases: dict[str, dict[str, object]] = {}
    if not skills_root.is_dir():
        return {"versions": versions, "cases": [], "version_cases": version_cases,
                "version_generations": version_generations}
    for version_root in sorted((path for path in skills_root.iterdir() if path.is_dir()), key=_skill_version_sort_key):
        version = version_root.name
        try:
            generation_skill_document(root, sessions_root, session_id, version, generation_root)
        except (FileNotFoundError, ValueError, OSError):
            continue
        version_entry = dict(state_versions.get(version) or {"version": version})
        versions.append(version_entry)
        merged_ids = []
        seen_ids = set()
        latest_generation_id = ""
        for job in _generation_jobs(sessions_root, session_id, version=version):
            generation_id = str(job.get("generation_id") or "")
            if not generation_id:
                continue
            try:
                _linked_generation(
                    sessions_root, session_id, version, generation_id, generation_root
                )
                _, _, directories = _generation_case_directories(generation_root, generation_id)
            except (FileNotFoundError, ValueError, OSError):
                continue
            actual_ids = list(directories)
            for case_id in actual_ids:
                if case_id not in seen_ids:
                    seen_ids.add(case_id)
                    merged_ids.append(case_id)
                cases[case_id] = _compact_case(state_cases.get(case_id) or {"case_id": case_id}) or {"case_id": case_id}
            if actual_ids and not latest_generation_id:
                latest_generation_id = generation_id
        if merged_ids:
            ordered_ids = [case_id for case_id in state_cases if case_id in seen_ids]
            ordered_set = set(ordered_ids)
            ordered_ids.extend(case_id for case_id in merged_ids if case_id not in ordered_set)
            version_cases[version] = ordered_ids
            version_generations[version] = latest_generation_id
    return {"versions": versions, "cases": list(cases.values()),
            "version_cases": version_cases, "version_generations": version_generations}

def session_runtime_sources(
    session_id: str,
    state: dict[str, object],
) -> dict[str, object]:
    """Describe all Dashboard inputs with portable runtime references."""
    marker = state.get("experiment_data") or state.get("product_id") or "v1"
    if isinstance(marker, dict):
        marker = marker.get("id") or marker.get("label") or "v1"
    data_version = next(
        (
            version for version in ("v1", "v2", "v3")
            if version in str(marker).lower()
        ),
        "v1",
    )
    session_ref = "runtime:sessions/" + session_id
    data_ref = "runtime:data/" + data_version
    return {
        "experiment_group": [
            session_ref + "/state.json",
            session_ref + "/meta.json",
        ],
        "experiment_dimensions": [
            session_ref + "/state.json",
            session_ref + "/meta.json",
            session_ref + "/events.jsonl",
        ],
        "runtime_models": {
            "optimizer": session_ref + "/state.json#opt_history",
            "judge": session_ref + "/check_judgments.jsonl",
            "compatibility_fallback": session_ref + "/events.jsonl",
        },
        "skill_versions": "runtime:generation_runs/_session_skills/" + session_id + "/",
        "version_cases": "runtime:generation_runs/<generation_id>/<wb_run_id>/cases/",
        "evaluation": session_ref + "/check_judgments.jsonl",
        "judge_trace": session_ref + "/check_judgments.jsonl",
        "reports": "runtime:generation_runs/<generation_id>/<wb_run_id>/cases/<case_id>/artifacts/report.md",
        "generation_jobs": session_ref + "/generation_jobs/<job_id>.json",
        "skill_artifacts": (
            "runtime:generation_runs/_session_skills/"
            + session_id + "/<version>/<artifact_hash>/<skill_name>/"
        ),
        "generation_trace": "runtime:generation_runs/<generation_id>/<wb_run_id>/cases/<case_id>/conversation.md",
        "generation_execution": [
            "runtime:generation_runs/<generation_id>/<wb_run_id>/cases/<case_id>/trace/1_operations.json",
            "runtime:generation_runs/<generation_id>/<wb_run_id>/cases/<case_id>/trace/2_events.jsonl",
            "runtime:generation_runs/<generation_id>/<wb_run_id>/cases/<case_id>/trace/rounds/<round>/{request,result}.json",
        ],
        "generation_metrics": "runtime:generation_runs/<generation_id>/<wb_run_id>/cases/<case_id>/results.json",
        "structured_data": data_ref + "/<training data>/<case_id>/structured_data.json",
        "metadata": data_ref + "/<training data>/<case_id>/structured_data.json",
        "raw_package": data_ref + "/<training data>/<case_id>/source/",
    }

def session_summary_document(
    sessions_root: Path, session_id: str, root: Path | None = None,
) -> dict[str, object]:
    """Return the dashboard's compact, cached Session bootstrap document."""
    session_id = _safe_generation_segment(session_id, "session id")
    sessions_root = sessions_root.resolve()
    session_root = (sessions_root / session_id).resolve()
    session_root.relative_to(sessions_root)
    state_path = session_root / "state.json"
    meta_path = session_root / "meta.json"
    judgment_path = session_root / "check_judgments.jsonl"
    events_path = session_root / "events.jsonl"
    if not state_path.is_file():
        raise FileNotFoundError("Session state.json does not exist")
    fingerprint = (_file_fingerprint(state_path), _file_fingerprint(meta_path),
                   _file_fingerprint(judgment_path),
                   _file_fingerprint(events_path))
    cache_key = str(session_root).lower() + ("|generation" if root is not None else "")
    if root is None:
        with _CACHE_LOCK:
            cached = _SUMMARY_CACHE.get(cache_key)
            if cached and cached[0] == fingerprint:
                return cached[1]
    state = _read_json_document(state_path)
    compact_state = _compact_state(state)
    if root is not None:
        generation_index = _generation_session_index(root, sessions_root, session_id, state)
        compact_state["versions"] = generation_index["versions"]
        compact_state["cases"] = generation_index["cases"]
        compact_state["generation_version_cases"] = generation_index["version_cases"]
        compact_state["generation_version_ids"] = generation_index["version_generations"]
    version_case_ids = compact_state.get("generation_version_cases")
    if not isinstance(version_case_ids, dict):
        all_case_ids = [
            str(item.get("case_id")) for item in compact_state.get("cases", [])
            if isinstance(item, dict) and item.get("case_id")
        ]
        version_case_ids = {
            str(item.get("version")): all_case_ids
            for item in compact_state.get("versions", [])
            if isinstance(item, dict) and item.get("version")
        }
    judgments = _score_summary_judgments(
        _summary_judgments(judgment_path),
        state.get("rubric") if isinstance(state.get("rubric"), dict) else {},
    )
    runtime_models = _summary_runtime_models(
        events_path, version_case_ids, state=state, judgments=judgments,
    )
    document = {
        "session_id": session_id,
        "meta": _read_json_document(meta_path) if meta_path.is_file() else {},
        "state": compact_state,
        "judgments": judgments,
        "runtime_models": runtime_models,
        "judgment_file": "check_judgments.jsonl" if judgment_path.is_file() else None,
        "runtime_sources": session_runtime_sources(session_id, state),
    }
    if root is None:
        with _CACHE_LOCK:
            _SUMMARY_CACHE[cache_key] = (fingerprint, document)
    return document

def rubric_guide_document(
    root: Path, sessions_root: Path, session_id: str,
) -> dict[str, object]:
    """Return the human-readable rubric guide matching a dashboard session."""
    session_id = _safe_generation_segment(session_id, "session id")
    root = root.resolve()
    sessions_root = sessions_root.resolve()
    session_root = (sessions_root / session_id).resolve()
    session_root.relative_to(sessions_root)
    state_path = session_root / "state.json"
    if not state_path.is_file():
        raise FileNotFoundError("Session state.json does not exist")
    state = _read_json_document(state_path)
    rubric = state.get("rubric")
    rubric = rubric if isinstance(rubric, dict) else {}
    product = str(rubric.get("product") or state.get("product_id") or "").strip()
    filename = RUBRIC_GUIDE_FILES.get(product)
    if not filename:
        raise FileNotFoundError("Rubric guide is not configured for product: %s" % product)
    target = (root / filename).resolve()
    target.relative_to(root)
    if not target.is_file():
        raise FileNotFoundError("Rubric guide does not exist: %s" % filename)
    return {
        "session_id": session_id,
        "product": product,
        "source": filename,
        "markdown": target.read_text(encoding="utf-8"),
    }

def _case_judgment_row(
    sessions_root: Path,
    session_id: str,
    version: str,
    case_id: str,
) -> dict[str, object]:
    """Load one exact Judge record from check_judgments.jsonl only."""
    session_id = _safe_generation_segment(session_id, "session id")
    sessions_root = sessions_root.resolve()
    session_root = (sessions_root / session_id).resolve()
    session_root.relative_to(sessions_root)
    path = session_root / "check_judgments.jsonl"
    if not path.is_file():
        raise FileNotFoundError("Session check_judgments.jsonl does not exist")
    fingerprint = _file_fingerprint(path)
    cache_key = str(path).lower()
    with _CACHE_LOCK:
        cached = _JUDGMENT_CACHE.get(cache_key)
    if not cached or cached[0] != fingerprint:
        index: dict[tuple[str, str], dict[str, object]] = {}
        with path.open("rb") as stream:
            for raw in stream:
                if not raw.endswith(bytes((10,))) or not raw.strip():
                    continue
                try:
                    row = json.loads(raw.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError):
                    continue
                if not isinstance(row, dict):
                    continue
                key = (
                    str(row.get("version") or ""),
                    str(row.get("case_id") or ""),
                )
                if not all(key):
                    continue
                if row.get("invalidated"):
                    index.pop(key, None)
                else:
                    index[key] = row
        cached = (fingerprint, index)
        with _CACHE_LOCK:
            _JUDGMENT_CACHE[cache_key] = cached
    row = cached[1].get((version, case_id))
    if row is None:
        raise FileNotFoundError(
            "No check judgment exists for this Skill version and Case"
        )
    return row

def case_judgment_document(
    sessions_root: Path,
    session_id: str,
    version: str,
    case_id: str,
) -> dict[str, object]:
    """Load Check scores and reasoning without loading Judge Trace."""
    row = _case_judgment_row(sessions_root, session_id, version, case_id)
    allowed = {
        "version", "case_id", "checks", "reasoning",
        "report_sha256", "rubric_sha256",
    }
    return {key: row.get(key) for key in allowed if key in row}


def case_judge_trace_document(
    sessions_root: Path,
    session_id: str,
    version: str,
    case_id: str,
) -> dict[str, object]:
    """Load Judge Trace independently from the report generation trace."""
    row = _case_judgment_row(sessions_root, session_id, version, case_id)
    trace = row.get("judge_trace")
    if trace in (None, "", [], {}):
        raise FileNotFoundError(
            "check_judgments.jsonl has no Judge Trace for this Case"
        )
    return {
        "session_id": session_id,
        "version": version,
        "case_id": case_id,
        "judge_trace": trace,
        "source": (
            "runtime:sessions/" + session_id
            + "/check_judgments.jsonl"
        ),
    }

def _dataset_candidates(root: Path, preferred: Path) -> list[Path]:
    """Use only the Data version selected by the experiment runtime."""
    del root
    candidate = preferred.expanduser().resolve()
    return [candidate] if candidate.is_file() else []

def _dataset_case(dataset_path: Path, case_id: str) -> dict | None:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    return next(
        (
            item
            for item in dataset.get("cases", [])
            if str(item.get("case_id")) == case_id
        ),
        None,
    )


def _existing_source_roots(
    dataset_root: Path,
    input_files: object,
) -> list[Path]:
    roots: list[Path] = []
    for item in input_files or []:
        if not isinstance(item, dict) or not item.get("source"):
            continue
        source = Path(str(item["source"])).expanduser()
        source = (
            source.resolve()
            if source.is_absolute()
            else (dataset_root / source).resolve()
        )
        source.relative_to(dataset_root)
        if source.is_dir() and source.name.lower() == "source":
            roots.append(source)
    return roots

def resolve_dataset_path(
    root: Path,
    configured: object = None,
    data_version: str | None = None,
) -> Path | None:
    normalized = str(data_version or "v1").strip().lower()
    version = next(
        (item for item in ("v1", "v2", "v3") if item in normalized),
        "v1",
    )
    suffix = version.upper()
    candidates = [
        configured,
        os.environ.get("OPENHARNESS_DATASET_PATH_" + suffix),
        os.environ.get("OPENHARNESS_WB_DATASET_" + suffix),
        os.environ.get("OPENHARNESS_DATASET_PATH"),
        os.environ.get("OPENHARNESS_WB_DATASET"),
        root / "data" / "v3_20260804_real_project_package" / "data.json",
        root / "data" / "research-report" / version / "data.json",
        root / "data.json",
        root / "data" / "data.json",
    ]
    for candidate in candidates:
        if not candidate:
            continue
        path = Path(candidate).expanduser().resolve()
        if path.is_file():
            return path
    return None


def case_source_roots(
    root: Path,
    sessions_root: Path,
    dataset_path: Path | None,
    session_id: str,
    case_id: str,
) -> tuple[Path, list[Path]]:
    if dataset_path is None:
        raise FileNotFoundError("OpenHarness dataset is not configured")
    sessions_root = sessions_root.resolve()
    state_path = (sessions_root / session_id / "state.json").resolve()
    state_path.relative_to(sessions_root)
    state = json.loads(state_path.read_text(encoding="utf-8"))
    case = next(
        (
            item
            for item in state.get("cases", [])
            if str(item.get("case_id")) == case_id
        ),
        None,
    )
    if case is None:
        case = {"case_id": case_id}

    preferred_root = dataset_path.parent.resolve()
    fallback: tuple[Path, list[Path]] | None = None
    for candidate in _dataset_candidates(root, dataset_path):
        dataset_case = _dataset_case(candidate, case_id)
        if not isinstance(dataset_case, dict):
            continue
        dataset_root = candidate.parent.resolve()
        input_files = dataset_case.get("input_files") or case.get("input_files")
        roots = _existing_source_roots(dataset_root, input_files)
        if roots:
            return dataset_root, roots
        if fallback is None:
            fallback = (dataset_root, roots)

    state_roots = _existing_source_roots(
        preferred_root, case.get("input_files")
    )
    if state_roots:
        return preferred_root, state_roots
    return fallback or (preferred_root, [])


def _case_structured_path(
    root: Path, sessions_root: Path, dataset_path: Path | None,
    session_id: str, case_id: str,
) -> tuple[Path, Path]:
    dataset_root, roots = case_source_roots(
        root, sessions_root, dataset_path, session_id, case_id
    )
    for source_root in roots:
        if not source_root.is_dir() or source_root.name.lower() != "source":
            continue
        case_root = source_root.parent.resolve()
        case_root.relative_to(dataset_root)
        path = case_root / "structured_data.json"
        if path.is_file():
            return dataset_root, path
    raise FileNotFoundError(
        "Case has no structured_data.json beside its source folder in the selected Data version"
    )


def case_structured_document(
    root: Path, sessions_root: Path, dataset_path: Path | None,
    session_id: str, case_id: str,
) -> dict[str, object]:
    """Load only the complete per-Case structured_data.json document."""
    dataset_root, path = _case_structured_path(
        root, sessions_root, dataset_path, session_id, case_id
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    version = dataset_root.name if dataset_root.name.lower() in {"v1", "v2", "v3"} else None
    return {
        "case": payload,
        "source": "runtime:data/" + (version or "configured") + "/" + path.relative_to(dataset_root).as_posix(),
        "requested_case_id": case_id, "resolved_case_id": case_id,
        "source_data_version": version,
    }


def case_metadata_document(
    root: Path, sessions_root: Path, dataset_path: Path | None,
    session_id: str, case_id: str,
) -> dict[str, object]:
    """Load the complete, original structured_data.json as Case metadata."""
    structured = case_structured_document(
        root, sessions_root, dataset_path, session_id, case_id
    )
    payload = structured["case"]
    items = payload.get("items") if isinstance(payload, dict) else None
    return {
        "metadata": payload, "source": structured["source"],
        "document_type": "structured_data", "requested_case_id": case_id,
        "resolved_case_id": case_id,
        "source_data_version": structured.get("source_data_version"),
        "evidence_count": len(items) if isinstance(items, list) else None,
    }

def case_quality_document(
    root: Path, sessions_root: Path, dataset_path: Path | None,
    session_id: str, case_id: str,
) -> dict[str, object]:
    """Load and parse the data-quality score table beside a Case source folder."""
    dataset_root, roots = case_source_roots(
        root, sessions_root, dataset_path, session_id, case_id
    )
    reports: list[Path] = []
    for source_root in roots:
        if not source_root.is_dir() or source_root.name.lower() != "source":
            continue
        case_root = source_root.parent.resolve()
        case_root.relative_to(dataset_root)
        reports.extend(sorted(case_root.glob("*\u8d28\u68c0\u62a5\u544a*.md")))
    if not reports:
        raise FileNotFoundError(
            "Case has no data quality report beside its source folder"
        )
    path = reports[0]
    markdown = path.read_text(encoding="utf-8")
    rows: list[dict[str, str]] = []
    for line in markdown.splitlines():
        match = re.match(
            r"^\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$", line
        )
        if not match:
            continue
        label, result = (part.strip() for part in match.groups())
        if label == "\u6307\u6807" or set(label) <= {"-", ":"}:
            continue
        rows.append({"label": label, "result": result})
    score_labels = {"\u7efc\u5408\u8d28\u91cf\u5206", "\u9057\u6f0f\u8986\u76d6\u5206", "\u51b2\u7a81\u4e00\u81f4\u6027\u5206", "\u4fe1\u566a\u5206"}
    scores: dict[str, float] = {}
    details: list[dict[str, str]] = []
    for row in rows:
        if row["label"] in score_labels:
            number = re.search(r"-?\d+(?:\.\d+)?", row["result"])
            if number:
                scores[row["label"]] = float(number.group(0))
        else:
            details.append(row)
    formula_match = re.search(r"^\s*\u7efc\u5408\u5206\s*=.*$", markdown, re.MULTILINE)
    relative = path.relative_to(dataset_root).as_posix()
    return {
        "available": True,
        "overall_score": scores.get("\u7efc\u5408\u8d28\u91cf\u5206"),
        "scores": scores,
        "details": details,
        "formula": formula_match.group(0).strip() if formula_match else "",
        "report_markdown": markdown,
        "source": "runtime:data/configured/" + relative,
        "requested_case_id": case_id,
        "resolved_case_id": case_id,
    }


def raw_file_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return "PDF"
    if suffix in {".xls", ".xlsx", ".xlsm", ".xlsb"}:
        return "Excel"
    if suffix in {".doc", ".docx", ".rtf"}:
        return "Word"
    if suffix in {".ppt", ".pptx"}:
        return "PowerPoint"
    if suffix in {".csv", ".tsv"}:
        return "CSV"
    if suffix in {".txt", ".md", ".json", ".jsonl"}:
        return "Text"
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}:
        return "Image"
    return suffix.lstrip(".").upper() or "File"


def session_tree(
    root: Path,
    sessions_root: Path | None = None,
) -> tuple[str, list[dict[str, object]]]:
    sessions = (sessions_root or (root / "app" / "sessions")).resolve()
    digest = hashlib.sha256()
    tree: list[dict[str, object]] = []
    for path in sorted(sessions.rglob("*")):
        if not path.is_file() or path.name not in SESSION_FILES:
            continue
        stat = path.stat()
        session_relative = path.relative_to(sessions).as_posix()
        relative = f"{VIRTUAL_SESSIONS_ROOT}/{session_relative}"
        digest.update(
            f"{relative}:{stat.st_mtime_ns}:{stat.st_size}\n".encode()
        )
        tree.append(
            {
                "path": relative,
                "type": "blob",
                "size": stat.st_size,
                "revision": f"{stat.st_mtime_ns}:{stat.st_size}",
            }
        )
    return digest.hexdigest(), tree
