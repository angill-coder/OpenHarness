"""Read-only data helpers for the integrated realtime dashboard."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import threading


SESSION_FILES = {
    "meta.json",
    "state.json",
    "outputs.jsonl",
    "check_judgments.jsonl",
}

VIRTUAL_SESSIONS_ROOT = "app/sessions"

_SUMMARY_CACHE: dict[str, tuple[tuple[object, ...], dict[str, object]]] = {}
_OUTPUT_INDEX_CACHE: dict[str, dict[str, object]] = {}
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


def generation_trace_document(
    root: Path,
    sessions_root: Path,
    session_id: str,
    version: str,
    case_id: str,
    generation_id: str,
    generation_root: Path | None = None,
) -> dict[str, object]:
    """Load an exact generation_id/case trace directly from generation_runs."""
    session_id = _safe_generation_segment(session_id, "session id")
    version = _safe_generation_segment(version, "skill version")
    case_id = _safe_generation_segment(case_id, "case id")
    generation_id = _safe_generation_segment(generation_id, "generation id")
    generation_root = (
        generation_root or (root.resolve() / "generation_runs")
    ).expanduser().resolve()

    candidates = []
    direct = (generation_root / generation_id / "generation_result.json").resolve()
    try:
        direct.relative_to(generation_root)
        if direct.is_file():
            candidates.append(direct)
    except ValueError:
        pass
    for path in sorted(generation_root.glob("gen-*/generation_result.json")):
        if path not in candidates:
            candidates.append(path)

    matched = None
    for result_path in candidates:
        payload = _read_json_document(result_path)
        payload_generation = str(
            payload.get("generation_id") or result_path.parent.name
        )
        if payload_generation == generation_id:
            matched = (result_path, payload)
            break
    if matched is None:
        raise FileNotFoundError(
            "generation_runs has no generation_result for %s" % generation_id
        )

    result_path, payload = matched
    payload_session = str(payload.get("session_id") or "")
    payload_version = str(payload.get("skill_version") or "")
    jobs = _generation_jobs(
        sessions_root,
        session_id,
        version=version,
        generation_id=generation_id,
    )
    if payload_session and payload_session != session_id:
        raise FileNotFoundError("generation trace session mismatch")
    if payload_version and payload_version != version:
        raise FileNotFoundError("generation trace version mismatch")
    if not ((payload_session == session_id and payload_version == version) or jobs):
        raise FileNotFoundError(
            "generation trace cannot be linked to the requested session/version"
        )

    case_payload = next(
        (
            item for item in payload.get("cases", [])
            if isinstance(item, dict)
            and str(item.get("openharness_case_id") or item.get("case_id") or "") == case_id
        ),
        None,
    )
    if case_payload is None:
        raise FileNotFoundError("generation trace case mismatch")

    trace_dir = None
    chosen_attempt = None
    attempts = case_payload.get("attempts") or []
    for attempt in reversed(attempts if isinstance(attempts, list) else []):
        if not isinstance(attempt, dict):
            continue
        trace_dir = _attempt_trace_directory(result_path.parent, case_id, attempt)
        if trace_dir is not None:
            chosen_attempt = attempt
            break
    if trace_dir is None or chosen_attempt is None:
        raise FileNotFoundError("generation_runs has no exact trace directory")

    operations_payload = []
    operations_path = trace_dir / "1_operations.json"
    if operations_path.is_file():
        try:
            value = json.loads(operations_path.read_text(encoding="utf-8"))
            operations_payload = value if isinstance(value, list) else []
        except (OSError, UnicodeError, json.JSONDecodeError):
            operations_payload = []
    operations = [
        {
            "name": str(item.get("name") or "tool"),
            "status": str(item.get("status") or "unknown"),
            "round": item.get("round_index"),
            "durationMs": item.get("duration_ms"),
            "input": _trace_text(item.get("input")),
            "result": _trace_text(item.get("result")),
        }
        for item in operations_payload
        if isinstance(item, dict)
    ]

    rounds = []
    rounds_root = trace_dir / "rounds"
    if rounds_root.is_dir():
        for round_path in sorted(rounds_root.glob("*/result.json")):
            try:
                item = json.loads(round_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(item, dict):
                continue
            rounds.append({
                "name": round_path.parent.name,
                "status": str(item.get("status") or "unknown"),
                "durationMs": item.get("duration_ms"),
                "output": _trace_text(item.get("final_output")),
            })

    conversation_path = trace_dir.parent / "conversation.md"
    observed = chosen_attempt.get("observed_models") or []
    return {
        "status": str(
            chosen_attempt.get("wb_status")
            or chosen_attempt.get("status")
            or "unknown"
        ),
        "model": observed[0] if observed else chosen_attempt.get("configured_model"),
        "durationMs": chosen_attempt.get("duration_ms"),
        "attempt": chosen_attempt.get("attempt"),
        "sessionId": chosen_attempt.get("wb_session_id"),
        "operations": operations,
        "rounds": rounds,
        "conversation": [],
        "conversationText": (
            conversation_path.read_text(encoding="utf-8")
            if conversation_path.is_file()
            else ""
        ),
        "conversationAvailable": conversation_path.is_file(),
        "source": (
            "runtime:generation_runs/"
            + trace_dir.relative_to(generation_root).as_posix()
        ),
        "generationId": generation_id,
        "caseId": case_id,
        "version": version,
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
        "experiment_data", "experiment_optimizer", "experiment_owner",
        "experiment_judge", "judge_version",
    )
    compact = {key: state.get(key) for key in scalar_keys if key in state}
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


def _summary_judgments(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        return []
    rows: list[dict[str, object]] = []
    allowed = {
        "version", "case_id", "checks", "invalidated", "reason", "scores",
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
        ],
        "skill_versions": session_ref + "/state.json",
        "version_cases": session_ref + "/state.json",
        "evaluation": session_ref + "/check_judgments.jsonl",
        "judge_trace": session_ref + "/check_judgments.jsonl",
        "reports": session_ref + "/outputs.jsonl",
        "generation_jobs": session_ref + "/generation_jobs/<job_id>.json",
        "skill_artifacts": (
            "runtime:generation_runs/_session_skills/"
            + session_id + "/<version>/<artifact_hash>/<skill_name>/"
        ),
        "generation_trace": (
            "runtime:generation_runs/<generation_id>/"
            "<wb_run_id>/cases/<case_id>/trace/"
        ),
        "structured_data": data_ref + "/data.json",
        "metadata": (
            data_ref
            + "/<case input_files source>/evidence_metadata.json"
        ),
        "raw_package": data_ref + "/<case input_files source>/",
    }

def session_summary_document(
    sessions_root: Path,
    session_id: str,
) -> dict[str, object]:
    """Return the dashboard's compact, cached Session bootstrap document."""
    session_id = _safe_generation_segment(session_id, "session id")
    sessions_root = sessions_root.resolve()
    session_root = (sessions_root / session_id).resolve()
    session_root.relative_to(sessions_root)
    state_path = session_root / "state.json"
    meta_path = session_root / "meta.json"
    judgment_path = session_root / "check_judgments.jsonl"
    if not state_path.is_file():
        raise FileNotFoundError("Session state.json does not exist")
    fingerprint = (
        _file_fingerprint(state_path),
        _file_fingerprint(meta_path),
        _file_fingerprint(judgment_path),
    )
    cache_key = str(session_root).lower()
    with _CACHE_LOCK:
        cached = _SUMMARY_CACHE.get(cache_key)
        if cached and cached[0] == fingerprint:
            return cached[1]

    state = _read_json_document(state_path)
    meta = _read_json_document(meta_path) if meta_path.is_file() else {}
    document = {
        "session_id": session_id,
        "meta": meta,
        "state": _compact_state(state),
        "judgments": _summary_judgments(judgment_path),
        "judgment_file": (
            "check_judgments.jsonl" if judgment_path.is_file() else None
        ),
        "runtime_sources": session_runtime_sources(session_id, state),
    }
    with _CACHE_LOCK:
        _SUMMARY_CACHE[cache_key] = (fingerprint, document)
    return document


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

def _scan_output_index(
    output_path: Path,
    cache: dict[str, object] | None,
) -> dict[str, object]:
    stat = output_path.stat()
    same_file = bool(
        cache
        and cache.get("file_id") == (stat.st_dev, stat.st_ino)
        and stat.st_size >= int(cache.get("indexed_size") or 0)
    )
    if same_file and stat.st_size == int(cache.get("size") or 0):
        if stat.st_mtime_ns == cache.get("mtime_ns"):
            return cache
        same_file = False
    index = dict(cache.get("index") or {}) if same_file else {}
    start = int(cache.get("indexed_size") or 0) if same_file else 0
    indexed_size = start
    with output_path.open("rb") as stream:
        stream.seek(start)
        while True:
            offset = stream.tell()
            raw = stream.readline()
            if not raw:
                indexed_size = stream.tell()
                break
            if not raw.endswith(b"\n"):
                indexed_size = offset
                break
            indexed_size = stream.tell()
            if not raw.strip():
                continue
            try:
                row = json.loads(raw.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if not isinstance(row, dict):
                continue
            version = str(row.get("version") or "")
            case_id = str(row.get("case_id") or "")
            if version and case_id:
                index[(version, case_id)] = (offset, len(raw))
    return {
        "file_id": (stat.st_dev, stat.st_ino),
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
        "indexed_size": indexed_size,
        "index": index,
    }


def case_output_document(
    output_path: Path,
    version: str,
    case_id: str,
) -> dict[str, object]:
    """Seek directly to one output row using an append-aware in-memory index."""
    output_path = output_path.resolve()
    if not output_path.is_file():
        raise FileNotFoundError("Session outputs.jsonl does not exist")
    cache_key = str(output_path).lower()
    with _CACHE_LOCK:
        cache = _scan_output_index(output_path, _OUTPUT_INDEX_CACHE.get(cache_key))
        _OUTPUT_INDEX_CACHE[cache_key] = cache
        location = cache["index"].get((version, case_id))
    if location is None:
        raise FileNotFoundError("No output exists for this Skill version and Case")
    offset, length = location
    with output_path.open("rb") as stream:
        stream.seek(offset)
        raw = stream.read(length)
    try:
        row = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        with _CACHE_LOCK:
            _OUTPUT_INDEX_CACHE.pop(cache_key, None)
        raise FileNotFoundError("Indexed output row is no longer readable") from exc
    if not isinstance(row, dict):
        raise FileNotFoundError("Indexed output row is invalid")
    return row

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
        if source.exists():
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
        raise FileNotFoundError("Case is not present in this session")

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


def case_structured_document(
    root: Path,
    sessions_root: Path,
    dataset_path: Path | None,
    session_id: str,
    case_id: str,
) -> dict[str, object]:
    """Load the exact structured Case entry from the experiment's Data file."""
    if dataset_path is None:
        raise FileNotFoundError("OpenHarness dataset is not configured")
    sessions_root = sessions_root.resolve()
    state_path = (sessions_root / session_id / "state.json").resolve()
    state_path.relative_to(sessions_root)
    if not state_path.is_file():
        raise FileNotFoundError("Session state.json does not exist")
    state = _read_json_document(state_path)
    if not any(
        isinstance(item, dict) and str(item.get("case_id") or "") == case_id
        for item in (state.get("cases") or [])
    ):
        raise FileNotFoundError("Case is not present in this session")
    selected = dataset_path.expanduser().resolve()
    case = _dataset_case(selected, case_id)
    if not isinstance(case, dict):
        raise FileNotFoundError(
            "Case is not present in the experiment's selected Data version"
        )
    dataset_root = selected.parent.resolve()
    return {
        "case": case,
        "source": (
            "runtime:data/"
            + (dataset_root.name.lower() if dataset_root.name.lower() in {"v1", "v2", "v3"} else "configured")
            + "/" + selected.relative_to(dataset_root).as_posix()
        ),
        "requested_case_id": case_id,
        "resolved_case_id": case_id,
        "source_data_version": (
            dataset_root.name
            if dataset_root.name.lower() in {"v1", "v2", "v3"}
            else None
        ),
    }

def case_metadata_document(
    root: Path,
    sessions_root: Path,
    dataset_path: Path | None,
    session_id: str,
    case_id: str,
) -> dict[str, object]:
    """Load the complete, original per-case metadata document."""
    dataset_root, roots = case_source_roots(
        root, sessions_root, dataset_path, session_id, case_id
    )
    evidence_candidates: list[Path] = []
    manifest_candidates: list[Path] = []
    for source_root in roots:
        case_root = source_root.parent if source_root.name.lower() == "source" else source_root
        if case_root.is_file():
            case_root = case_root.parent
        evidence_candidates.extend([
            case_root / "evidence_metadata.json",
            case_root / "00_evidence_metadata.json",
        ])
        manifest_candidates.extend(sorted(case_root.glob("*.case.json")))

    candidates = [
        (path, "evidence_metadata") for path in evidence_candidates
    ] + [
        (path, "case_manifest") for path in manifest_candidates
    ]
    seen: set[Path] = set()
    for path, document_type in candidates:
        path = path.resolve()
        if path in seen:
            continue
        seen.add(path)
        path.relative_to(dataset_root)
        if not path.is_file():
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            "metadata": payload,
            "source": (
                "runtime:data/"
                + (dataset_root.name.lower() if dataset_root.name.lower() in {"v1", "v2", "v3"} else "configured")
                + "/" + path.relative_to(dataset_root).as_posix()
            ),
            "document_type": document_type,
            "requested_case_id": case_id,
            "resolved_case_id": case_id,
            "source_data_version": (
                dataset_root.name
                if dataset_root.name.lower() in {"v1", "v2", "v3"}
                else None
            ),
            "evidence_count": (
                len(payload.get("items", []))
                if isinstance(payload, dict) and isinstance(payload.get("items"), list)
                else None
            ),
        }
    raise FileNotFoundError(
        "Case has no evidence_metadata.json or complete .case.json"
    )


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
