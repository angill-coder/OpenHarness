"""Resolve the report produced by an external Runner result."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional


def _field(value: Any, name: str, default=None):
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _inside(path: Path, root: Path) -> Optional[Path]:
    try:
        resolved = path.expanduser().resolve()
        resolved.relative_to(root)
        return resolved
    except (OSError, RuntimeError, ValueError):
        return None


def report_text_from_result(case_result: Any, output_dir: str | Path) -> str:
    """Return embedded report text or load it from the Runner output safely.

    Single-case runs use ``attemptN/artifacts/report.md``. The legacy
    ``<run>/cases/<case>/artifacts/report.md`` layout remains readable.
    """
    report = _field(case_result, "report") or {}
    text = str(_field(report, "text", "") or "")
    if text.strip():
        return text

    root = Path(output_dir).expanduser().resolve()
    candidates: list[Path] = []
    captured_path = _field(report, "captured_path")
    if captured_path:
        candidates.append(Path(str(captured_path)))

    attempts = list(_field(case_result, "attempts", ()) or ())
    for attempt in reversed(attempts):
        attempt_report = _field(attempt, "report") or {}
        attempt_text = str(_field(attempt_report, "text", "") or "")
        if attempt_text.strip():
            return attempt_text
        attempt_captured = _field(attempt_report, "captured_path")
        if attempt_captured:
            candidates.append(Path(str(attempt_captured)))
        run_dir = _field(attempt, "run_dir")
        if not run_dir:
            continue
        run_root = Path(str(run_dir))
        candidates.append(run_root / "artifacts" / "report.md")
        wb_case_id = _field(attempt, "wb_case_id") or _field(
            case_result, "wb_case_id"
        )
        if wb_case_id:
            candidates.append(
                run_root
                / "cases"
                / str(wb_case_id)
                / "artifacts"
                / "report.md"
            )

    seen: set[Path] = set()
    for candidate in candidates:
        safe_path = _inside(candidate, root)
        if safe_path is None or safe_path in seen or not safe_path.is_file():
            continue
        seen.add(safe_path)
        try:
            loaded = safe_path.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeError):
            continue
        if loaded.strip():
            return loaded
    return ""
