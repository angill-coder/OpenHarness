# -*- coding: utf-8 -*-
"""Rubrics Loop domain service.

Keeps feedback/candidates/experiments outside Session state until explicit
adoption. All persisted references are bound to report and rubric hashes.
"""
from __future__ import annotations

import ast
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import re
import time
import unicodedata
import uuid
from typing import Any, Callable, Dict, Iterable, Optional

import dashboard_api
import feedback_router
import feedback_acceptance
import llm_client
import memory_pipeline
from memory_client import (
    MemoryClientError,
    ResearchReportMemoryClient,
    SUPPORTED_MEMORY_USERS,
    normalize_memory_user,
)


class RubricsLoopError(ValueError):
    pass


def _memory_user(value):
    try:
        return normalize_memory_user(value)
    except MemoryClientError as exc:
        raise RubricsLoopError(str(exc)) from exc


def json_sha256(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def text_sha256(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def normalize_report_text(value: str) -> str:
    """Normalize rendered-text differences without weakening report hashes."""
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return re.sub(r"\s+", " ", normalized).strip()


def _inline_markdown_projection(
    text: str, raw_offset: int
) -> tuple[list[str], list[int]]:
    """Project the inline Markdown supported by the WebUI to visible text."""
    visible: list[str] = []
    positions: list[int] = []
    index = 0
    while index < len(text):
        if text.startswith("**", index):
            index += 2
            continue
        if text[index] == "`":
            index += 1
            continue
        link = re.match(r"\[([^\]]+)\]\(([^)]+)\)", text[index:])
        if link:
            label = link.group(1)
            label_start = index + 1
            visible.extend(label)
            positions.extend(
                raw_offset + label_start + offset
                for offset in range(len(label))
            )
            index += len(link.group(0))
            continue
        visible.append(text[index])
        positions.append(raw_offset + index)
        index += 1
    return visible, positions


def _markdown_visible_projection(markdown: str) -> tuple[str, list[int]]:
    """Return normalized rendered text plus a source offset for every char."""
    projected: list[str] = []
    raw_positions: list[int] = []
    raw_offset = 0
    in_fence = False
    for raw_line in str(markdown or "").splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        stripped = line.strip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            raw_offset += len(raw_line)
            continue
        if not in_fence and re.fullmatch(
            r"\s*\|?\s*:?-+[-:|\s]*\|?\s*", line
        ):
            raw_offset += len(raw_line)
            continue

        segments: list[tuple[str, int]] = []
        if not in_fence and re.fullmatch(r"\s*\|.*\|\s*", line):
            first_pipe = line.find("|")
            last_pipe = line.rfind("|")
            inner = line[first_pipe + 1:last_pipe]
            cursor = 0
            for cell in inner.split("|"):
                leading = len(cell) - len(cell.lstrip())
                value = cell.strip()
                if value:
                    segments.append((
                        value,
                        raw_offset + first_pipe + 1 + cursor + leading,
                    ))
                cursor += len(cell) + 1
        else:
            content = line
            prefix = re.match(
                r"\s*(?:#{1,4}\s+|>\s?|[-*+]\s+|\d+[.)]\s+)",
                content,
            )
            start = prefix.end() if prefix else len(content) - len(content.lstrip())
            content = content[start:].strip()
            if content:
                leading_after_prefix = len(line[start:]) - len(line[start:].lstrip())
                segments.append((
                    content,
                    raw_offset + start + leading_after_prefix,
                ))

        for segment_index, (segment, segment_offset) in enumerate(segments):
            chars, positions = _inline_markdown_projection(
                segment, segment_offset
            )
            if segment_index and projected:
                projected.append(" ")
                raw_positions.append(max(segment_offset - 1, raw_offset))
            projected.extend(chars)
            raw_positions.extend(positions)
        if segments:
            projected.append(" ")
            raw_positions.append(raw_offset + max(len(line) - 1, 0))
        raw_offset += len(raw_line)

    normalized: list[str] = []
    normalized_positions: list[int] = []
    for char, position in zip(projected, raw_positions):
        expanded = unicodedata.normalize("NFKC", char)
        for value in expanded:
            if value.isspace():
                if normalized and normalized[-1] != " ":
                    normalized.append(" ")
                    normalized_positions.append(position)
            else:
                normalized.append(value)
                normalized_positions.append(position)
    while normalized and normalized[-1] == " ":
        normalized.pop()
        normalized_positions.pop()
    return "".join(normalized), normalized_positions


def resolve_markdown_selection(markdown: str, rendered_text: str) -> str:
    """Locate a browser-rendered selection and return its raw Markdown slice."""
    needle = normalize_report_text(rendered_text)
    if not needle:
        return ""
    visible, positions = _markdown_visible_projection(markdown)
    start = visible.find(needle)
    if start < 0:
        return ""
    end = start + len(needle) - 1
    raw_start = positions[start]
    raw_end = positions[end] + 1
    source = str(markdown or "")
    line_start = source.rfind("\n", 0, raw_start) + 1
    prefix = source[line_start:raw_start]
    if re.fullmatch(
        r"\s*(?:#{1,4}\s+|>\s?|[-*+]\s+|\d+[.)]\s+|\|\s*)",
        prefix,
    ):
        raw_start = line_start
    elif raw_start >= 2 and source[raw_start - 2:raw_start] == "**":
        raw_start -= 2
    line_end = source.find("\n", raw_end)
    if line_end < 0:
        line_end = len(source)
    suffix = source[raw_end:line_end]
    if source[line_start:line_end].lstrip().startswith("|"):
        raw_end = line_end
    elif suffix.startswith("**"):
        raw_end += 2
    return source[raw_start:raw_end].strip()


def _summary_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("result") or value.get("summary") or "")
    text = str(value or "").strip()
    if text.startswith("{") and text.endswith("}"):
        try:
            parsed = ast.literal_eval(text)
        except (SyntaxError, ValueError):
            return text
        if isinstance(parsed, dict):
            return str(parsed.get("result") or parsed.get("summary") or text)
    return text


def _now() -> float:
    return round(time.time(), 3)


def _safe_segment(value: str, label: str) -> str:
    value = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value):
        raise RubricsLoopError("非法%s: %s" % (label, value))
    return value


def _atomic_write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    os.replace(str(temporary), str(path))


def _check_count(rubric: Dict[str, Any]) -> int:
    return sum(
        len(dimension.get("checks") or [])
        for dimension in rubric.get("dimensions") or []
    )


def _judge_text_length(rubric: Dict[str, Any]) -> int:
    fields = []
    for dimension in rubric.get("dimensions") or []:
        fields.extend(
            str(dimension.get(key) or "")
            for key in (
                "name_zh", "criteria", "positive_example", "negative_example"
            )
        )
        anchors = dimension.get("anchors") or {}
        fields.extend(str(anchors.get(str(level)) or "") for level in range(1, 6))
        for check in dimension.get("checks") or []:
            fields.extend(
                str(check.get(key) or "")
                for key in ("label", "desc", "effect")
            )
    return sum(len(value) for value in fields)


def _redline_ids(rubric: Dict[str, Any]) -> set[str]:
    return {
        str(check.get("id"))
        for dimension in rubric.get("dimensions") or []
        for check in dimension.get("checks") or []
        if check.get("id") and check.get("redline")
    }


def _judgment_score_summary(
    judgment: Dict[str, Any], rubric: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Derive the same 1-5 dimension and weighted overall scores as Judge."""
    check_scores = judgment.get("checks") or {}
    if not isinstance(check_scores, dict) or not check_scores:
        return None
    dimensions = []
    weighted = 0.0
    for dimension in rubric.get("dimensions") or []:
        values = []
        redline_hit = False
        for check in dimension.get("checks") or []:
            check_id = str(check.get("id") or "")
            if check_id not in check_scores or check_scores[check_id] is None:
                continue
            value = float(check_scores[check_id])
            values.append(value)
            if check.get("redline") and value <= 0:
                redline_hit = True
        if not values:
            continue
        score = 1.0 + 4.0 * sum(values) / len(values)
        if redline_hit:
            score = min(score, 2.0)
        score = round(max(1.0, min(5.0, score)), 3)
        weighted += score * float(dimension.get("weight") or 0)
        dimensions.append({
            "name": dimension.get("name"),
            "label": dimension.get("name_zh") or dimension.get("name"),
            "score": score,
        })
    if not dimensions:
        return None
    return {"overall": round(weighted, 3), "dimensions": dimensions}


def validate_candidate_rubric(
    parent: Dict[str, Any], candidate: Dict[str, Any]
) -> Dict[str, Any]:
    errors = []
    if not isinstance(candidate, dict):
        return {"ok": False, "errors": ["candidate_rubric 必须是对象"]}
    if candidate.get("product") != parent.get("product"):
        errors.append("product 不得变化")
    parent_dims = [item.get("name") for item in parent.get("dimensions") or []]
    candidate_dims = [
        item.get("name") for item in candidate.get("dimensions") or []
    ]
    if candidate_dims != parent_dims:
        errors.append("自动迭代不得新增、删除或重排维度")

    ids = []
    missing_optimizer = []
    invalid_optimizer = []
    for dimension in candidate.get("dimensions") or []:
        for check in dimension.get("checks") or []:
            check_id = str(check.get("id") or "")
            if not check_id:
                errors.append("存在缺少 id 的 Check")
            ids.append(check_id)
            if "optimizer" not in check:
                missing_optimizer.append(check_id or "<missing-id>")
            elif check.get("optimizer") is not None and (
                not isinstance(check.get("optimizer"), dict)
                or not check["optimizer"].get("pattern_id")
            ):
                invalid_optimizer.append(check_id or "<missing-id>")
    if len(ids) != len(set(ids)):
        errors.append("Check ID 不得重复")
    if missing_optimizer:
        errors.append(
            "Check 缺少 optimizer 映射: " + ", ".join(missing_optimizer)
        )
    if invalid_optimizer:
        errors.append(
            "Check optimizer 映射无 pattern_id: "
            + ", ".join(invalid_optimizer)
        )
    if not isinstance(candidate.get("target"), dict):
        errors.append("candidate_rubric 缺少 target")

    parent_count = _check_count(parent)
    candidate_count = _check_count(candidate)
    parent_length = _judge_text_length(parent)
    candidate_length = _judge_text_length(candidate)
    if candidate_count > parent_count:
        errors.append("Check 总数不得超过父版本")
    if candidate_length > parent_length:
        errors.append("Rubrics 判定文本不得超过父版本")

    parent_redlines = _redline_ids(parent)
    candidate_redlines = _redline_ids(candidate)
    redline_changes = sorted(parent_redlines ^ candidate_redlines)
    return {
        "ok": not errors,
        "errors": errors,
        "parent_check_count": parent_count,
        "candidate_check_count": candidate_count,
        "parent_text_length": parent_length,
        "candidate_text_length": candidate_length,
        "redline_changes": redline_changes,
        "requires_redline_confirmation": bool(redline_changes),
    }


class RubricsLoopService:
    def __init__(
        self,
        root: Path,
        sessions_root: Path,
        registry_root: Optional[Path] = None,
        generation_root: Optional[Path] = None,
    ):
        self.root = root.expanduser().resolve()
        self.sessions_root = sessions_root.expanduser().resolve()
        self.registry_root = (
            registry_root
            or self.root / "harness" / "artifacts" / "rubrics"
        ).expanduser().resolve()
        self.generation_root = (
            generation_root or self.root / "generation_runs"
        ).expanduser().resolve()

    def _session_root(self, session_id: str) -> Path:
        session_id = _safe_segment(session_id, " Session ID")
        path = (self.sessions_root / session_id).resolve()
        path.relative_to(self.sessions_root)
        if not (path / "state.json").is_file():
            raise RubricsLoopError("Session 不存在: %s" % session_id)
        return path

    def _loop_root(self, session_id: str) -> Path:
        return self._session_root(session_id) / "rubrics_loop"

    def _state(self, session_id: str) -> Dict[str, Any]:
        return json.loads(
            (self._session_root(session_id) / "state.json").read_text(
                encoding="utf-8"
            )
        )

    def context(self) -> Dict[str, Any]:
        sessions = []
        if not self.sessions_root.is_dir():
            return {
                "sessions": [],
                "memory_users": list(SUPPORTED_MEMORY_USERS),
                "default_memory_user": "local",
            }
        for path in sorted(self.sessions_root.iterdir()):
            if not (path / "state.json").is_file():
                continue
            try:
                summary = dashboard_api.session_summary_document(
                    self.sessions_root, path.name, self.generation_root.parent
                )
            except (OSError, ValueError, FileNotFoundError):
                continue
            rubric = (summary.get("state") or {}).get("rubric") or {}
            sessions.append(
                {
                    "session_id": path.name,
                    "product_id": (summary.get("state") or {}).get("product_id"),
                    "rubric_version": rubric.get("version"),
                    "rubric_sha256": json_sha256(rubric),
                    "rubric_source": copy.deepcopy(
                        (summary.get("state") or {}).get("rubric_source") or {}
                    ),
                    "versions": (summary.get("state") or {}).get("versions") or [],
                    "cases": (summary.get("state") or {}).get("cases") or [],
                    "version_cases": (
                        (summary.get("state") or {}).get(
                            "generation_version_cases"
                        )
                        or {}
                    ),
                }
            )
        return {
            "sessions": sessions,
            "memory_users": list(SUPPORTED_MEMORY_USERS),
            "default_memory_user": "local",
        }

    def report(
        self,
        session_id: str,
        skill_version: str,
        case_id: str,
        expected_report_sha256: str = "",
        expected_rubric_sha256: str = "",
    ) -> Dict[str, Any]:
        session_id = _safe_segment(session_id, " Session ID")
        skill_version = _safe_segment(skill_version, " Skill 版本")
        case_id = _safe_segment(case_id, " Case ID")
        try:
            output = dashboard_api.generation_case_report_document(
                self.root, self.sessions_root, session_id, skill_version, case_id,
                generation_root=self.generation_root,
            )
        except (OSError, ValueError, FileNotFoundError) as exc:
            raise RubricsLoopError(str(exc)) from exc
        report_text = str(output.get("report_text") or "")
        report_hash = text_sha256(report_text)
        state = self._state(session_id)
        current_rubric = state.get("rubric") or {}
        current_rubric_hash = json_sha256(current_rubric)
        judgment = {}
        try:
            judgment = dashboard_api.case_judgment_document(
                self.sessions_root, session_id, skill_version, case_id
            )
        except (OSError, ValueError, FileNotFoundError):
            pass
        judged_report_hash = str(judgment.get("report_sha256") or "")
        judged_rubric_hash = str(judgment.get("rubric_sha256") or "")
        judgment_matches_report = (
            not judged_report_hash or judged_report_hash == report_hash
        )
        judgment_matches_rubric = (
            not judged_rubric_hash or judged_rubric_hash == current_rubric_hash
        )
        judge_scores = (
            _judgment_score_summary(judgment, current_rubric)
            if judgment_matches_rubric else None
        )
        rubric_hash = (
            judged_rubric_hash
            if judgment_matches_report and judged_rubric_hash
            else current_rubric_hash
        )
        snapshot_available = rubric_hash == current_rubric_hash
        if expected_report_sha256 and expected_report_sha256 != report_hash:
            raise RubricsLoopError("报告已变化，请从 Dashboard 重新打开")
        if expected_rubric_sha256 and expected_rubric_sha256 != rubric_hash:
            raise RubricsLoopError("Rubrics 已变化，请重新选择报告")
        return {
            "session_id": session_id,
            "skill_version": skill_version,
            "case_id": case_id,
            "report_text": report_text,
            "report_sha256": report_hash,
            "rubric_version": current_rubric.get("version") if snapshot_available else None,
            "rubric_sha256": rubric_hash,
            "rubric_snapshot_available": snapshot_available,
            "annotatable": snapshot_available,
            "judgment_matches_report": judgment_matches_report,
            "judge": ({
                **judge_scores,
                "valid": judgment_matches_report and judgment_matches_rubric,
                "report_sha256": judged_report_hash,
                "rubric_sha256": judged_rubric_hash,
            } if judge_scores else None),
            "warning": None,
            "source": output.get("source"),
        }

    def resolve_selection(
        self,
        session_id: str,
        skill_version: str,
        case_id: str,
        rendered_text: str,
        expected_report_sha256: str = "",
        expected_rubric_sha256: str = "",
    ) -> Dict[str, Any]:
        report = self.report(
            session_id,
            skill_version,
            case_id,
            expected_report_sha256,
            expected_rubric_sha256,
        )
        markdown_quote = resolve_markdown_selection(
            report["report_text"], rendered_text
        )
        if not markdown_quote:
            raise RubricsLoopError(
                "无法将当前选区定位到 Markdown 原文，请缩小选区后重试"
            )
        return {
            "session_id": session_id,
            "skill_version": skill_version,
            "case_id": case_id,
            "report_sha256": report["report_sha256"],
            "rubric_sha256": report["rubric_sha256"],
            "rendered_quote": str(rendered_text or "").strip(),
            "markdown_quote": markdown_quote,
        }

    def _batch_path(self, session_id: str, batch_id: str) -> Path:
        return (
            self._loop_root(session_id)
            / "batches"
            / (_safe_segment(batch_id, " Batch ID") + ".json")
        )

    def _candidate_path(self, session_id: str, candidate_id: str) -> Path:
        return (
            self._loop_root(session_id)
            / "candidates"
            / (_safe_segment(candidate_id, " Candidate ID") + ".json")
        )

    def _experiment_path(self, session_id: str, experiment_id: str) -> Path:
        return (
            self._loop_root(session_id)
            / "experiments"
            / (_safe_segment(experiment_id, " Experiment ID") + ".json")
        )

    def _acceptance_path(self, session_id: str, experiment_id: str) -> Path:
        return (
            self._loop_root(session_id)
            / "acceptance"
            / (_safe_segment(experiment_id, " Experiment ID") + ".json")
        )

    def _draft_path(self, session_id: str, draft_id: str) -> Path:
        return (
            self._loop_root(session_id)
            / "drafts"
            / (_safe_segment(draft_id, " Draft ID") + ".json")
        )

    def get_draft(self, session_id: str, draft_id: str) -> Dict[str, Any]:
        path = self._draft_path(session_id, draft_id)
        if not path.is_file():
            raise RubricsLoopError("待验证 Rubrics 草案不存在: %s" % draft_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def update_draft(
        self, session_id: str, draft_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        value = self.get_draft(session_id, draft_id)
        value.update(copy.deepcopy(updates))
        value["updated_at"] = _now()
        _atomic_write(self._draft_path(session_id, draft_id), value)
        return value

    def active_draft(self, session_id: str) -> Optional[Dict[str, Any]]:
        current_hash = json_sha256(self._state(session_id).get("rubric") or {})
        return next((
            item for item in self._list_loop_documents(session_id, "drafts")
            if item.get("status") in {"collecting", "validating", "awaiting_review"}
            and item.get("base_rubric_sha256") == current_hash
        ), None)

    @staticmethod
    def _operation_check_ids(operation: Dict[str, Any]) -> set[str]:
        return {
            str(operation.get(key))
            for key in ("check_id", "target_id", "new_id", "merged_id")
            if operation.get(key)
        }

    def _draft_history_context(
        self, draft: Optional[Dict[str, Any]]
    ) -> list[Dict[str, Any]]:
        if not draft:
            return []
        context = []
        for index, revision in enumerate(draft.get("revisions") or [], start=1):
            context.append({
                "revision": index,
                "batch_id": revision.get("batch_id"),
                "candidate_id": revision.get("candidate_id"),
                "summary": revision.get("summary"),
                "feedback": copy.deepcopy(revision.get("feedback") or []),
                "operations": copy.deepcopy(revision.get("operations") or []),
                "touched_check_ids": copy.deepcopy(
                    revision.get("touched_check_ids") or []
                ),
            })
        return context

    def get_batch(self, session_id: str, batch_id: str) -> Dict[str, Any]:
        path = self._batch_path(session_id, batch_id)
        if not path.is_file():
            raise RubricsLoopError("Feedback Batch 不存在: %s" % batch_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def list_batches(self, session_id: str) -> list[Dict[str, Any]]:
        root = self._loop_root(session_id) / "batches"
        if not root.is_dir():
            return []
        values = []
        for path in root.glob("*.json"):
            try:
                values.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                continue
        return sorted(values, key=lambda item: item.get("updated_at") or 0, reverse=True)

    def _list_loop_documents(
        self, session_id: str, directory: str
    ) -> list[Dict[str, Any]]:
        root = self._loop_root(session_id) / directory
        if not root.is_dir():
            return []
        values = []
        for path in root.glob("*.json"):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(value, dict):
                values.append(value)
        return sorted(
            values, key=lambda item: item.get("updated_at") or 0, reverse=True
        )

    def list_iterations(self, session_id: str) -> Dict[str, Any]:
        """Aggregate persisted Batch, Candidate and Experiment records."""
        session_id = _safe_segment(session_id, " Session ID")
        batches = self.list_batches(session_id)
        candidates = self._list_loop_documents(session_id, "candidates")
        experiments = self._list_loop_documents(session_id, "experiments")
        rubric_draft = self.active_draft(session_id)
        candidates_by_batch: Dict[str, list[Dict[str, Any]]] = {}
        for candidate in candidates:
            candidates_by_batch.setdefault(
                str(candidate.get("source_batch_id") or ""), []
            ).append(candidate)
        experiments_by_candidate: Dict[str, list[Dict[str, Any]]] = {}
        for experiment in experiments:
            experiments_by_candidate.setdefault(
                str(experiment.get("candidate_id") or ""), []
            ).append(experiment)
        draft_revision_candidate_ids = {
            str(revision.get("candidate_id") or "")
            for revision in (rubric_draft or {}).get("revisions") or []
            if revision.get("candidate_id")
        }
        draft_latest_candidate_id = str(
            (rubric_draft or {}).get("latest_candidate_id") or ""
        )
        draft_experiment_id = str(
            (rubric_draft or {}).get("experiment_id") or ""
        )
        draft_experiment = next((
            item for item in experiments
            if str(item.get("experiment_id") or "") == draft_experiment_id
        ), None)

        iterations = []
        for batch in batches:
            batch_id = str(batch.get("batch_id") or "")
            linked_candidates = sorted(
                candidates_by_batch.get(batch_id) or [],
                key=lambda item: item.get("updated_at") or 0,
                reverse=True,
            )
            feedback = batch.get("feedback") or []
            if not feedback and not linked_candidates:
                continue
            candidate_summaries = []
            experiment_summaries = []
            for candidate in linked_candidates:
                candidate_id = str(candidate.get("candidate_id") or "")
                linked_experiments = sorted(
                    experiments_by_candidate.get(candidate_id) or [],
                    key=lambda item: item.get("updated_at") or 0,
                    reverse=True,
                )
                candidate_summaries.append({
                    "candidate_id": candidate_id,
                    "status": candidate.get("status"),
                    "created_at": candidate.get("created_at"),
                    "updated_at": candidate.get("updated_at"),
                    "previous_candidate_id": candidate.get(
                        "previous_candidate_id"
                    ),
                    "model_config": copy.deepcopy(
                        candidate.get("model_config") or {}
                    ),
                    "operation_count": len(candidate.get("operations") or []),
                    "modified_check_ids": sorted({
                        str(operation.get(key))
                        for operation in candidate.get("operations") or []
                        for key in (
                            "check_id", "target_id", "new_id", "merged_id"
                        )
                        if operation.get(key)
                    }),
                    "summary": _summary_text(candidate.get("summary")),
                    "validation_ok": bool(
                        (candidate.get("validation") or {}).get("ok")
                    ),
                    "experiment_id": candidate.get("experiment_id"),
                    "adopted_version": candidate.get("adopted_version"),
                    "feedback_batch_ids": copy.deepcopy(
                        candidate.get("feedback_batch_ids")
                        or [candidate.get("source_batch_id")]
                    ),
                    "cumulative_validation": (
                        {
                            "included": True,
                            "is_latest_revision": (
                                candidate_id == draft_latest_candidate_id
                            ),
                            "experiment_id": draft_experiment_id,
                            "experiment_status": (
                                (draft_experiment or {}).get("status")
                            ),
                            "draft_revision_count": len(
                                (rubric_draft or {}).get("revisions") or []
                            ),
                        }
                        if candidate_id in draft_revision_candidate_ids
                        and draft_experiment_id
                        else None
                    ),
                })
                experiment_summaries.extend({
                    "experiment_id": item.get("experiment_id"),
                    "candidate_id": candidate_id,
                    "status": item.get("status"),
                    "phase": item.get("phase"),
                    "acceptance_status": (
                        (item.get("acceptance") or {}).get("overall_status")
                        or (item.get("acceptance") or {}).get("status")
                    ),
                    "experiment_session_id": item.get("experiment_session_id"),
                    # A validation experiment runs against the cumulative
                    # Rubrics draft frozen on this Candidate.  Keep that
                    # membership in the history payload so earlier rounds in
                    # the same draft are also shown as validated.
                    "included_batch_ids": copy.deepcopy(
                        item.get("included_batch_ids")
                        or candidate.get("feedback_batch_ids")
                        or [candidate.get("source_batch_id")]
                    ),
                    "created_at": item.get("created_at"),
                    "finished_at": item.get("finished_at"),
                    "updated_at": item.get("updated_at"),
                } for item in linked_experiments)
            latest_candidate = candidate_summaries[0] if candidate_summaries else None
            updated_at = max(
                [batch.get("updated_at") or 0]
                + [item.get("updated_at") or 0 for item in linked_candidates]
                + [item.get("updated_at") or 0 for item in experiment_summaries]
            )
            iterations.append({
                "batch_id": batch_id,
                "batch_status": batch.get("status"),
                "rubric_version": batch.get("rubric_version"),
                "rubric_sha256": batch.get("rubric_sha256"),
                "created_at": batch.get("created_at"),
                "updated_at": updated_at,
                "report_count": len(batch.get("report_refs") or []),
                "feedback_count": len(feedback),
                "report_refs": copy.deepcopy(batch.get("report_refs") or []),
                "feedback": copy.deepcopy(feedback),
                "routing_summary": {
                    "status": (batch.get("routing") or {}).get("status"),
                    "memory_user": batch.get("memory_user") or "local",
                    "rubric_count": sum(
                        1 for route in (batch.get("routing") or {}).get("routes") or []
                        if route.get("destination") == "rubric"
                    ),
                    "memory_count": sum(
                        1 for route in (batch.get("routing") or {}).get("routes") or []
                        if route.get("destination") == "memory"
                    ),
                    "memory_saved_count": sum(
                        1 for route in (batch.get("routing") or {}).get("routes") or []
                        if route.get("destination") == "memory"
                        and (route.get("memory_result") or {}).get("status")
                        in {"pending", "stored", "unchanged"}
                    ),
                },
                "candidates": candidate_summaries,
                "experiments": experiment_summaries,
                "latest_candidate_id": (
                    latest_candidate.get("candidate_id")
                    if latest_candidate else None
                ),
                "latest_experiment_id": (
                    latest_candidate.get("experiment_id")
                    if latest_candidate else None
                ),
            })
        iterations.sort(key=lambda item: item.get("updated_at") or 0, reverse=True)

        groups_by_key: Dict[tuple[str, str], Dict[str, Any]] = {}
        for iteration in iterations:
            key = (
                str(iteration.get("rubric_version") or "未知版本"),
                str(iteration.get("rubric_sha256") or ""),
            )
            group = groups_by_key.setdefault(key, {
                "rubric_version": key[0],
                "rubric_sha256": key[1],
                "updated_at": 0,
                "iterations": [],
            })
            group["iterations"].append(iteration)
            group["updated_at"] = max(
                group["updated_at"], iteration.get("updated_at") or 0
            )
        groups = sorted(
            groups_by_key.values(),
            key=lambda item: item.get("updated_at") or 0,
            reverse=True,
        )

        actionable = {
            "draft", "validated", "staged", "running", "awaiting_review"
        }
        current_rubric_sha256 = json_sha256(
            self._state(session_id).get("rubric") or {}
        )
        superseded_candidate_ids = {
            str(item.get("previous_candidate_id"))
            for item in candidates if item.get("previous_candidate_id")
        }
        active_candidate = next(
            (
                item for item in candidates
                if item.get("status") in actionable
                and item.get("parent_rubric_sha256") == current_rubric_sha256
                and str(item.get("candidate_id") or "")
                not in superseded_candidate_ids
            ),
            None,
        )
        newest_feedback_batch = next((
            item for item in batches
            if item.get("status") in {"draft", "submitted", "optimizing"}
            and item.get("rubric_sha256") == current_rubric_sha256
            and item.get("feedback")
        ), None)
        if (
            active_candidate
            and newest_feedback_batch
            and (newest_feedback_batch.get("updated_at") or 0)
            > (active_candidate.get("updated_at") or 0)
        ):
            active_candidate = None
        active_batch = None
        active_experiment_id = None
        if active_candidate:
            active_batch = str(active_candidate.get("source_batch_id") or "")
            active_experiment_id = active_candidate.get("experiment_id")
        else:
            active_draft = newest_feedback_batch
            active_batch = (
                str(active_draft.get("batch_id") or "")
                if active_draft else None
            )
        active_draft_summary = None
        if rubric_draft:
            active_draft_summary = {
                "draft_id": rubric_draft.get("draft_id"),
                "status": rubric_draft.get("status"),
                "base_rubric_version": rubric_draft.get("base_rubric_version"),
                "base_rubric_sha256": rubric_draft.get("base_rubric_sha256"),
                "current_rubric_sha256": rubric_draft.get("current_rubric_sha256"),
                "revision_count": len(rubric_draft.get("revisions") or []),
                "touched_check_ids": sorted({
                    str(check_id)
                    for revision in rubric_draft.get("revisions") or []
                    for check_id in revision.get("touched_check_ids") or []
                }),
                "latest_candidate_id": rubric_draft.get("latest_candidate_id"),
                "updated_at": rubric_draft.get("updated_at"),
            }
        return {
            "session_id": session_id,
            "groups": groups,
            "active_draft": active_draft_summary,
            "active": {
                "batch_id": active_batch,
                "candidate_id": (
                    active_candidate.get("candidate_id")
                    if active_candidate else None
                ),
                "experiment_id": active_experiment_id,
                "draft_id": (
                    rubric_draft.get("draft_id") if rubric_draft else None
                ),
            },
        }

    def list_all_iterations(self) -> Dict[str, Any]:
        """Return persisted Rubrics Loop history grouped by source Session."""
        sessions = []
        for summary in self.context().get("sessions") or []:
            session_id = str(summary.get("session_id") or "")
            if not session_id:
                continue
            try:
                history = self.list_iterations(session_id)
            except (OSError, ValueError, RubricsLoopError):
                continue
            groups = history.get("groups") or []
            draft = history.get("active_draft")
            if not groups and not draft:
                continue
            updated_at = max(
                [group.get("updated_at") or 0 for group in groups]
                + [draft.get("updated_at") or 0 if draft else 0]
            )
            experiments = [
                experiment
                for group in groups
                for iteration in group.get("iterations") or []
                for experiment in iteration.get("experiments") or []
            ]
            active_experiments = [
                experiment for experiment in experiments
                if experiment.get("status") in {"queued", "running"}
            ]
            sessions.append({
                "session_id": session_id,
                "rubric_version": summary.get("rubric_version"),
                "updated_at": updated_at,
                "iteration_count": sum(
                    len(group.get("iterations") or []) for group in groups
                ),
                "active_experiment_count": len(active_experiments),
                "groups": copy.deepcopy(groups),
                "active_draft": copy.deepcopy(draft),
            })
        sessions.sort(
            key=lambda item: item.get("updated_at") or 0, reverse=True
        )
        return {"sessions": sessions}

    @staticmethod
    def inspect_memory(user="local") -> Dict[str, Any]:
        """Expose the canonical Memory Runtime's read-only L0/L1/L2 view."""
        return ResearchReportMemoryClient(user=user).inspect()

    def create_batch(
        self,
        session_id: str,
        report_ref: Optional[Dict[str, Any]] = None,
        account: str = "",
        memory_user: str = "local",
    ) -> Dict[str, Any]:
        state = self._state(session_id)
        rubric = copy.deepcopy(state.get("rubric") or {})
        rubric_draft = self.active_draft(session_id)
        working_rubric = copy.deepcopy(
            (rubric_draft or {}).get("current_rubric") or rubric
        )
        batch_id = "fb-" + uuid.uuid4().hex[:10]
        batch = {
            "batch_id": batch_id,
            "session_id": session_id,
            "rubric_version": rubric.get("version"),
            "rubric_sha256": json_sha256(rubric),
            "parent_rubric": rubric,
            "draft_id": (rubric_draft or {}).get("draft_id"),
            "working_rubric": working_rubric,
            "working_rubric_sha256": json_sha256(working_rubric),
            "draft_revision_count": len(
                (rubric_draft or {}).get("revisions") or []
            ),
            "report_refs": [],
            "feedback": [],
            "memory_user": _memory_user(memory_user),
            "status": "draft",
            "created_by": account,
            "created_at": _now(),
            "updated_at": _now(),
        }
        if report_ref:
            batch = self._add_report_to_value(batch, report_ref)
        _atomic_write(self._batch_path(session_id, batch_id), batch)
        return batch

    def _add_report_to_value(
        self, batch: Dict[str, Any], report_ref: Dict[str, Any]
    ) -> Dict[str, Any]:
        if batch.get("status") != "draft":
            raise RubricsLoopError("只有 draft Batch 可以添加报告")
        report = self.report(
            batch["session_id"],
            report_ref.get("skill_version"),
            report_ref.get("case_id"),
            report_ref.get("report_sha256", ""),
            report_ref.get("rubric_sha256", ""),
        )
        if not report.get("annotatable"):
            raise RubricsLoopError("该历史 Rubrics Snapshot 不可用，不能添加批注")
        if report["rubric_sha256"] != batch["rubric_sha256"]:
            raise RubricsLoopError("不同 Rubrics 版本的报告不能加入同一 Batch")
        compact = {
            key: report[key]
            for key in (
                "session_id", "skill_version", "case_id", "report_sha256",
                "rubric_version", "rubric_sha256", "source",
            )
        }
        key = (compact["skill_version"], compact["case_id"], compact["report_sha256"])
        existing = {
            (item["skill_version"], item["case_id"], item["report_sha256"])
            for item in batch.get("report_refs") or []
        }
        if key not in existing:
            batch.setdefault("report_refs", []).append(compact)
        batch["updated_at"] = _now()
        return batch

    def add_report(
        self, session_id: str, batch_id: str, report_ref: Dict[str, Any]
    ) -> Dict[str, Any]:
        batch = self._add_report_to_value(
            self.get_batch(session_id, batch_id), report_ref
        )
        _atomic_write(self._batch_path(session_id, batch_id), batch)
        return batch

    def add_feedback(
        self,
        session_id: str,
        batch_id: str,
        scope: str,
        content: str,
        report_ref: Optional[Dict[str, Any]] = None,
        quote: str = "",
        rendered_quote: str = "",
        before_context: str = "",
        after_context: str = "",
        account: str = "",
    ) -> Dict[str, Any]:
        batch = self.get_batch(session_id, batch_id)
        if batch.get("status") != "draft":
            raise RubricsLoopError("已提交的 Batch 不能继续编辑")
        if scope not in {"inline", "report", "batch"}:
            raise RubricsLoopError("Feedback scope 必须为 inline/report/batch")
        content = str(content or "").strip()
        if not content:
            raise RubricsLoopError("Feedback 内容不能为空")
        bound_ref = None
        if scope != "batch":
            if not report_ref:
                raise RubricsLoopError("原文级和报告级 Feedback 必须绑定报告")
            batch = self._add_report_to_value(batch, report_ref)
            bound_ref = next(
                item for item in batch["report_refs"]
                if item["skill_version"] == report_ref.get("skill_version")
                and item["case_id"] == report_ref.get("case_id")
            )
        if scope == "inline":
            quote = str(quote or "").strip()
            if not quote:
                raise RubricsLoopError("原文批注必须包含选中的文字")
            report = self.report(
                session_id, bound_ref["skill_version"], bound_ref["case_id"]
            )
            report_markdown = report["report_text"]
            if quote not in report_markdown:
                resolved_quote = resolve_markdown_selection(
                    report_markdown, rendered_quote or quote
                )
                if resolved_quote:
                    quote = resolved_quote
            if quote not in report_markdown:
                raise RubricsLoopError("选中的文字已不在当前报告中")
        feedback = {
            "feedback_id": "f-" + uuid.uuid4().hex[:12],
            "batch_id": batch_id,
            "scope": scope,
            "content": content,
            "report_ref": bound_ref,
            "quote": quote,
            "rendered_quote": str(rendered_quote or "").strip(),
            "before_context": str(before_context or ""),
            "after_context": str(after_context or ""),
            "created_by": account,
            "created_at": _now(),
        }
        batch.setdefault("feedback", []).append(feedback)
        batch.pop("routing", None)
        batch["updated_at"] = _now()
        _atomic_write(self._batch_path(session_id, batch_id), batch)
        feedback_log = self._loop_root(session_id) / "feedback.jsonl"
        feedback_log.parent.mkdir(parents=True, exist_ok=True)
        with feedback_log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(feedback, ensure_ascii=False) + "\n")
        return batch

    def delete_feedback(
        self, session_id: str, batch_id: str, feedback_id: str
    ) -> Dict[str, Any]:
        batch = self.get_batch(session_id, batch_id)
        if batch.get("status") != "draft":
            raise RubricsLoopError("已提交的 Batch 不能继续编辑")
        batch["feedback"] = [
            item for item in batch.get("feedback") or []
            if item.get("feedback_id") != feedback_id
        ]
        batch.pop("routing", None)
        batch["updated_at"] = _now()
        _atomic_write(self._batch_path(session_id, batch_id), batch)
        return batch

    def update_feedback(
        self,
        session_id: str,
        batch_id: str,
        feedback_id: str,
        content: str,
        account: str = "",
    ) -> Dict[str, Any]:
        batch = self.get_batch(session_id, batch_id)
        if batch.get("status") != "draft":
            raise RubricsLoopError("已提交的 Batch 不能继续编辑")
        content = str(content or "").strip()
        if not content:
            raise RubricsLoopError("Feedback 内容不能为空")
        target = next(
            (
                item for item in batch.get("feedback") or []
                if item.get("feedback_id") == feedback_id
            ),
            None,
        )
        if target is None:
            raise RubricsLoopError("Feedback 不存在")
        target["content"] = content
        target["updated_by"] = account
        target["updated_at"] = _now()
        batch.pop("routing", None)
        batch["updated_at"] = _now()
        _atomic_write(self._batch_path(session_id, batch_id), batch)
        return batch

    def route_batch_feedback(
        self,
        session_id: str,
        batch_id: str,
        model_config: Dict[str, Any],
        account: str = "",
        call_model: Optional[Callable[..., str]] = None,
    ) -> Dict[str, Any]:
        batch = self.get_batch(session_id, batch_id)
        if batch.get("status") != "draft":
            raise RubricsLoopError("只有 draft Batch 可以重新分类 Feedback")
        feedback = batch.get("feedback") or []
        if not feedback:
            raise RubricsLoopError("请先添加 Feedback")
        reports = []
        for ref in batch.get("report_refs") or []:
            report = self.report(
                session_id, ref["skill_version"], ref["case_id"],
                ref["report_sha256"], ref["rubric_sha256"],
            )
            reports.append({
                "skill_version": ref["skill_version"],
                "case_id": ref["case_id"],
                "report_text": report["report_text"],
            })
        try:
            routes = feedback_router.route_feedback(
                feedback, batch["working_rubric"], reports,
                model_config, call_model=call_model,
            )
        except ValueError as exc:
            raise RubricsLoopError(str(exc)) from exc
        batch["routing"] = {
            "status": "review",
            "routes": routes,
            "model_config": copy.deepcopy(model_config),
            "routed_by": account,
            "routed_at": _now(),
        }
        batch["updated_at"] = _now()
        _atomic_write(self._batch_path(session_id, batch_id), batch)
        return batch

    def confirm_feedback_routing(
        self,
        session_id: str,
        batch_id: str,
        destinations: Dict[str, str],
        model_config: Dict[str, Any],
        account: str = "",
        process_memory=None,
        memory_actions: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        batch = self._confirm_routing_choices(
            session_id, batch_id, destinations, model_config, account,
            memory_actions,
        )
        routes, memory_user, memory_error = self._process_memory_routes(
            session_id, batch, model_config, process_memory=process_memory
        )
        batch = self._finalize_routing(
            session_id, batch_id, routes, memory_user, account,
            error=memory_error,
        )
        if memory_error:
            raise RubricsLoopError("Memory 处理失败: %s" % memory_error)
        return batch

    def _confirm_routing_choices(
        self,
        session_id: str,
        batch_id: str,
        destinations: Dict[str, str],
        model_config: Dict[str, Any],
        account: str = "",
        memory_actions: Optional[Dict[str, str]] = None,
    ) -> Dict[str, Any]:
        batch = self.get_batch(session_id, batch_id)
        routing = batch.get("routing") or {}
        if routing.get("status") not in {
            "review", "processing", "memory_failed", "completed"
        }:
            raise RubricsLoopError("请先运行 Feedback 分类")
        routes = routing.get("routes") or []
        valid_ids = {str(item.get("feedback_id") or "") for item in batch.get("feedback") or []}
        by_feedback = {
            str(item.get("feedback_id") or ""): item
            for item in batch.get("feedback") or []
        }
        for route in routes:
            feedback_id = str(route.get("feedback_id") or "")
            selected = str((destinations or {}).get(feedback_id) or route.get("destination") or "")
            if feedback_id not in valid_ids or selected not in feedback_router.DESTINATIONS:
                raise RubricsLoopError("Feedback 分类确认包含非法值")
            route["destination"] = selected
            route["confirmed_by_user"] = feedback_id in (destinations or {})
            if selected == "memory":
                memory_action = str(
                    (memory_actions or {}).get(feedback_id)
                    or route.get("memory_action")
                    or "pending"
                )
                if memory_action not in {"pending", "store"}:
                    raise RubricsLoopError("Memory 保存方式包含非法值")
                route["memory_action"] = memory_action
        routing["status"] = "processing"
        routing["memory_model_config"] = copy.deepcopy(model_config)
        routing["destinations_confirmed_by"] = account
        routing["destinations_confirmed_at"] = _now()
        batch["updated_at"] = _now()
        _atomic_write(self._batch_path(session_id, batch_id), batch)
        return batch

    def _process_memory_routes(
        self,
        session_id: str,
        batch: Dict[str, Any],
        model_config: Dict[str, Any],
        process_memory=None,
        call_model: Optional[Callable[..., str]] = None,
    ) -> tuple[list[Dict[str, Any]], str, str]:
        routes = copy.deepcopy((batch.get("routing") or {}).get("routes") or [])
        by_feedback = {
            str(item.get("feedback_id") or ""): item
            for item in batch.get("feedback") or []
        }
        memory_user = _memory_user(
            batch.get("memory_user") or batch.get("created_by") or "local"
        )
        pipeline = memory_pipeline.MemoryPipeline(user=memory_user)
        state = self._state(session_id)
        memory_error = ""
        for route in routes:
            memory_result = route.get("memory_result") or {}
            if (
                route.get("destination") != "memory"
                or (
                    memory_result
                    and memory_result.get("status") != "error"
                )
            ):
                continue
            feedback = by_feedback[route["feedback_id"]]
            ref = feedback.get("report_ref") or {}
            context = {
                "external_source_id": "openharness:%s:%s" % (
                    session_id, route["feedback_id"]
                ),
                "session_id": session_id,
                "task": state.get("requirement") or "报告写作反馈",
                "topic": ref.get("case_id") or "",
                "audience": "总裁",
                "report_type": "研究报告",
                "context_before": feedback.get("quote") or "",
                "context_after": feedback.get("content") or "",
                "final_artifact": "skill=%s case=%s report_sha256=%s" % (
                    ref.get("skill_version") or "",
                    ref.get("case_id") or "",
                    ref.get("report_sha256") or "",
                ),
            }
            try:
                if process_memory:
                    route["memory_result"] = process_memory(
                        feedback, context, model_config
                    )
                elif route.get("memory_action") == "store":
                    route["memory_result"] = pipeline.process(
                        feedback, context, model_config,
                        call_model=call_model,
                        forced_decision="store",
                    )
                else:
                    route["memory_result"] = pipeline.store_pending(
                        feedback, context, model_config
                    )
                route["memory_result"]["memory_user"] = memory_user
            except Exception as exc:
                route["memory_result"] = {
                    "status": "error", "error": str(exc)
                }
                memory_error = str(exc)
                break
        return routes, memory_user, memory_error

    def _finalize_routing(
        self,
        session_id: str,
        batch_id: str,
        routes: list[Dict[str, Any]],
        memory_user: str,
        account: str,
        error: str = "",
    ) -> Dict[str, Any]:
        # Candidate generation may have updated the Batch while the Memory
        # branch was running. Reload before merging so neither branch can
        # overwrite the other's result.
        batch = self.get_batch(session_id, batch_id)
        routing = batch.get("routing") or {}
        routing["routes"] = copy.deepcopy(routes)
        routing["status"] = "memory_failed" if error else "completed"
        routing["confirmed_by"] = account
        routing["confirmed_at"] = _now()
        if error:
            routing["memory_error"] = error
        else:
            routing.pop("memory_error", None)
        batch["routing"] = routing
        batch["memory_user"] = memory_user
        if not any(
            route.get("destination") == "rubric" for route in routes
        ):
            # Memory-only and record-only rounds do not generate a Candidate.
            batch["status"] = "completed"
        batch["updated_at"] = _now()
        _atomic_write(self._batch_path(session_id, batch_id), batch)
        return batch

    def confirm_and_propose_candidate(
        self,
        session_id: str,
        batch_id: str,
        destinations: Dict[str, str],
        memory_actions: Dict[str, str],
        memory_model_config: Dict[str, Any],
        rubric_model_config: Dict[str, Any],
        account: str = "",
        process_memory=None,
        call_memory_model: Optional[Callable[..., str]] = None,
        call_candidate_model: Optional[Callable[..., str]] = None,
    ) -> Dict[str, Any]:
        """Confirm routing and run Memory + Rubrics branches concurrently."""
        batch = self._confirm_routing_choices(
            session_id, batch_id, destinations, memory_model_config, account,
            memory_actions,
        )
        has_rubric = any(
            route.get("destination") == "rubric"
            for route in (batch.get("routing") or {}).get("routes") or []
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            memory_future = pool.submit(
                self._process_memory_routes,
                session_id, copy.deepcopy(batch), memory_model_config,
                process_memory, call_memory_model,
            )
            candidate_future = (
                pool.submit(
                    self.propose_candidate,
                    session_id, batch_id, rubric_model_config, account, "", "",
                    call_candidate_model,
                )
                if has_rubric else None
            )
            candidate = None
            candidate_error = None
            try:
                if candidate_future:
                    candidate = candidate_future.result()
            except Exception as exc:
                candidate_error = exc
            routes, memory_user, memory_error = memory_future.result()
        latest_batch = self._finalize_routing(
            session_id, batch_id, routes, memory_user, account,
            error=memory_error,
        )
        if memory_error:
            raise RubricsLoopError("Memory 处理失败: %s" % memory_error)
        if candidate_error:
            raise candidate_error
        return {"batch": latest_batch, "candidate": candidate}

    @staticmethod
    def _rubric_feedback(batch: Dict[str, Any]) -> list[Dict[str, Any]]:
        routing = batch.get("routing") or {}
        if not routing:
            return list(batch.get("feedback") or [])
        confirmed_processing = (
            routing.get("status") == "processing"
            and bool(routing.get("destinations_confirmed_at"))
        )
        if routing.get("status") != "completed" and not confirmed_processing:
            raise RubricsLoopError("请先确认 Feedback 分类")
        rubric_ids = {
            str(route.get("feedback_id") or "")
            for route in routing.get("routes") or []
            if route.get("destination") == "rubric"
        }
        return [
            item for item in batch.get("feedback") or []
            if str(item.get("feedback_id") or "") in rubric_ids
        ]

    def _optimizer_prompt(
        self,
        batch: Dict[str, Any],
        revision_note: str = "",
        previous_candidate: Optional[Dict[str, Any]] = None,
        rubric_draft: Optional[Dict[str, Any]] = None,
    ) -> str:
        reports = []
        for ref in batch.get("report_refs") or []:
            report = self.report(
                batch["session_id"], ref["skill_version"], ref["case_id"],
                ref["report_sha256"], ref["rubric_sha256"],
            )
            reports.append({
                "skill_version": ref["skill_version"],
                "case_id": ref["case_id"],
                "report_text": report["report_text"],
            })
        payload = {
            "base_rubric": batch["parent_rubric"],
            "working_rubric": copy.deepcopy(
                batch.get("working_rubric") or batch["parent_rubric"]
            ),
            "historical_changes": self._draft_history_context(rubric_draft),
            "feedback": self._rubric_feedback(batch),
            "reports": reports,
            "revision_note": revision_note,
            "previous_candidate": previous_candidate,
        }
        return "\n".join([
            "你是 OpenHarness 的 Rubrics Optimizer。请联合分析多份报告与专家 Feedback，生成精简的候选 Rubrics。",
            "输入 Feedback 已由 Feedback Router 和用户确认属于通用 Rubrics；不要重新分类，也不要写入个性化偏好。",
            "优先修改或合并现有 Check，确有缺口才新增。维度不得变化；Check 总数和判定文本均不得超过父版本。",
            "base_rubric 是累计长度预算的原始基线；working_rubric 是本轮必须继续修改的最新草案，不得退回 base_rubric。",
            "historical_changes 是此前已暂存的修改历史。若本轮再次涉及同一 Check，必须结合原始内容、历史修改原因和新 Feedback 决定保留、扩展、合并或替换，不能静默覆盖。",
            "保留所有必要字段，尤其每条 Check 的 id、label、desc、effect、redline 和 optimizer；未修改内容原样保留。",
            "不要自动改变红线。每个 operation 必须关联 feedback_ids。",
            "只输出一个 JSON 对象，字段必须为 candidate_rubric、operations、feedback_analysis、unhandled_feedback_ids、summary。",
            "feedback_analysis 每项包含 feedback_id、category、existing_check_ids、decision、reason。",
            "feedback_analysis.decision 仅允许 add_check/update_check/merge_checks/delete_check/move_check/covered/task_config/one_off_preference；若该 Feedback 被任一 operation 引用，decision 必须使用对应的修改 operation，不得写 covered 或自造近义值。",
            "operations 的 op 仅允许 add_check/update_check/merge_checks/delete_check/move_check。",
            "若 operation 涉及 historical_changes 中已改过的 Check，增加 history_action（keep_previous/extend/merge/replace/no_change）；replace 或存在冲突时还要写 conflict=true 和 conflict_resolution。",
            "\n## 输入\n" + json.dumps(payload, ensure_ascii=False, indent=2),
        ])

    def propose_candidate(
        self,
        session_id: str,
        batch_id: str,
        model_config: Dict[str, Any],
        account: str = "",
        revision_note: str = "",
        previous_candidate_id: str = "",
        call_model: Optional[Callable[..., str]] = None,
    ) -> Dict[str, Any]:
        batch = self.get_batch(session_id, batch_id)
        if not batch.get("feedback"):
            raise RubricsLoopError("请先添加 Feedback")
        rubric_feedback = self._rubric_feedback(batch)
        if not rubric_feedback:
            raise RubricsLoopError("本轮没有需要修改通用 Rubrics 的 Feedback")
        if batch.get("status") not in {"draft", "submitted", "completed"}:
            raise RubricsLoopError("当前 Batch 状态不能生成 Candidate")
        previous = None
        if previous_candidate_id:
            previous = self.get_candidate(session_id, previous_candidate_id)
        rubric_draft = self.active_draft(session_id)
        if batch.get("draft_id"):
            if not rubric_draft or rubric_draft.get("draft_id") != batch.get("draft_id"):
                raise RubricsLoopError("待验证 Rubrics 草案已变化，请开始新一轮 Feedback")
            if (
                batch.get("working_rubric_sha256")
                != rubric_draft.get("current_rubric_sha256")
            ):
                raise RubricsLoopError("待验证 Rubrics 草案已更新，本轮 Feedback 已过期")
        working_rubric = copy.deepcopy(
            batch.get("working_rubric") or batch["parent_rubric"]
        )
        batch["status"] = "optimizing"
        batch["updated_at"] = _now()
        _atomic_write(self._batch_path(session_id, batch_id), batch)
        caller = call_model or llm_client.call_llm
        try:
            raw = caller(
                self._optimizer_prompt(
                    batch, revision_note, previous, rubric_draft
                ),
                timeout_seconds=os.environ.get("LLM_REWRITE_TIMEOUT_SECONDS", "600"),
                retries=os.environ.get("LLM_REWRITE_RETRIES", "2"),
                backend=model_config.get("llm_backend"),
                model=model_config.get("llm_model"),
                reasoning_effort=model_config.get("llm_reasoning_effort"),
            )
        except Exception:
            batch["status"] = "submitted"
            batch["updated_at"] = _now()
            _atomic_write(self._batch_path(session_id, batch_id), batch)
            raise
        parsed = llm_client.extract_json(raw)
        if not isinstance(parsed, dict) or not isinstance(
            parsed.get("candidate_rubric"), dict
        ):
            batch["status"] = "submitted"
            _atomic_write(self._batch_path(session_id, batch_id), batch)
            raise RubricsLoopError("Optimizer 未返回有效 candidate_rubric")
        validation = validate_candidate_rubric(
            batch["parent_rubric"], parsed["candidate_rubric"]
        )
        allowed_operations = {
            "add_check", "update_check", "merge_checks", "delete_check",
            "move_check",
        }
        valid_feedback_ids = {
            item["feedback_id"] for item in rubric_feedback
        }
        historically_touched = {
            str(check_id)
            for revision in (rubric_draft or {}).get("revisions") or []
            for check_id in revision.get("touched_check_ids") or []
        }
        repeated_check_ids = set()
        unresolved_history_conflicts = []
        for operation in parsed.get("operations") or []:
            if operation.get("op") not in allowed_operations:
                validation["errors"].append(
                    "非法 Patch operation: %s" % operation.get("op")
                )
            linked = set(operation.get("feedback_ids") or [])
            if not linked or not linked.issubset(valid_feedback_ids):
                validation["errors"].append(
                    "每个 Patch operation 必须关联当前 Batch 的 Feedback ID"
                )
            repeated = self._operation_check_ids(operation) & historically_touched
            if repeated:
                repeated_check_ids.update(repeated)
                if operation.get("history_action") not in {
                    "keep_previous", "extend", "merge", "replace", "no_change"
                }:
                    validation["errors"].append(
                        "重复修改 %s 时必须说明 history_action"
                        % ", ".join(sorted(repeated))
                    )
                if (
                    operation.get("history_action") == "replace"
                    or operation.get("conflict") is True
                ) and not str(operation.get("conflict_resolution") or "").strip():
                    unresolved_history_conflicts.extend(sorted(repeated))
                    validation["errors"].append(
                        "替换历史修改或存在冲突时必须说明 conflict_resolution"
                    )
        validation["ok"] = not validation["errors"]
        validation["repeated_check_ids"] = sorted(repeated_check_ids)
        validation["history_conflict_check_ids"] = sorted(set(
            unresolved_history_conflicts
        ))
        validation["requires_history_conflict_confirmation"] = any(
            (
                operation.get("history_action") == "replace"
                or operation.get("conflict") is True
            )
            and bool(self._operation_check_ids(operation) & historically_touched)
            for operation in parsed.get("operations") or []
        )
        candidate_id = "rc-" + uuid.uuid4().hex[:10]
        candidate = {
            "candidate_id": candidate_id,
            "source_batch_id": batch_id,
            "session_id": session_id,
            "parent_rubric_version": batch["rubric_version"],
            "parent_rubric_sha256": batch["rubric_sha256"],
            "working_parent_rubric": working_rubric,
            "working_parent_rubric_sha256": json_sha256(working_rubric),
            "draft_id": batch.get("draft_id"),
            "draft_revision_count": batch.get("draft_revision_count", 0),
            "candidate_rubric": parsed["candidate_rubric"],
            "candidate_rubric_sha256": json_sha256(parsed["candidate_rubric"]),
            "operations": parsed.get("operations") or [],
            "feedback_analysis": parsed.get("feedback_analysis") or [],
            "rubric_feedback_ids": sorted(valid_feedback_ids),
            "unhandled_feedback_ids": parsed.get("unhandled_feedback_ids") or [],
            "summary": _summary_text(parsed.get("summary")),
            "revision_note": revision_note,
            "previous_candidate_id": previous_candidate_id or None,
            "validation": validation,
            "model_config": copy.deepcopy(model_config),
            "status": "validated" if validation["ok"] else "draft",
            "created_by": account,
            "created_at": _now(),
            "updated_at": _now(),
        }
        _atomic_write(self._candidate_path(session_id, candidate_id), candidate)
        batch["status"] = "completed"
        batch["latest_candidate_id"] = candidate_id
        batch["updated_at"] = _now()
        _atomic_write(self._batch_path(session_id, batch_id), batch)
        return candidate

    def stage_candidate(
        self,
        session_id: str,
        candidate_id: str,
        account: str = "",
        history_conflict_confirmed: bool = False,
    ) -> Dict[str, Any]:
        """Append one reviewed Candidate to the Session's cumulative draft."""
        candidate = self.get_candidate(session_id, candidate_id)
        if candidate.get("status") not in {"validated", "awaiting_review"}:
            raise RubricsLoopError("只有通过静态校验的候选 Rubrics 可以暂存")
        validation = candidate.get("validation") or {}
        if not validation.get("ok"):
            raise RubricsLoopError("候选 Rubrics 未通过静态校验")
        if (
            validation.get("requires_history_conflict_confirmation")
            and not history_conflict_confirmed
        ):
            raise RubricsLoopError("本轮覆盖了同一 Check 的历史修改，需要人工确认")
        state_rubric = self._state(session_id).get("rubric") or {}
        state_hash = json_sha256(state_rubric)
        if state_hash != candidate.get("parent_rubric_sha256"):
            raise RubricsLoopError("基线 Rubrics 已变化，候选 Rubrics 已过期")
        batch = self.get_batch(session_id, candidate["source_batch_id"])
        working_parent_hash = (
            candidate.get("working_parent_rubric_sha256")
            or batch.get("working_rubric_sha256")
            or candidate.get("parent_rubric_sha256")
        )
        draft = self.active_draft(session_id)
        if draft:
            if working_parent_hash != draft.get("current_rubric_sha256"):
                raise RubricsLoopError("待验证 Rubrics 草案已变化，候选 Rubrics 已过期")
        else:
            if working_parent_hash != state_hash:
                raise RubricsLoopError("候选 Rubrics 缺少对应的待验证草案")
            draft = {
                "draft_id": "rd-" + uuid.uuid4().hex[:10],
                "session_id": session_id,
                "base_rubric_version": candidate.get("parent_rubric_version"),
                "base_rubric_sha256": state_hash,
                "base_rubric": copy.deepcopy(state_rubric),
                "revisions": [],
                "status": "collecting",
                "created_by": account,
                "created_at": _now(),
            }
        touched_check_ids = sorted({
            check_id
            for operation in candidate.get("operations") or []
            for check_id in self._operation_check_ids(operation)
        })
        revision = {
            "revision_id": "rr-" + uuid.uuid4().hex[:10],
            "batch_id": batch["batch_id"],
            "candidate_id": candidate_id,
            "feedback": copy.deepcopy(batch.get("feedback") or []),
            "operations": copy.deepcopy(candidate.get("operations") or []),
            "feedback_analysis": copy.deepcopy(
                candidate.get("feedback_analysis") or []
            ),
            "summary": candidate.get("summary"),
            "touched_check_ids": touched_check_ids,
            "working_parent_rubric_sha256": working_parent_hash,
            "candidate_rubric_sha256": candidate.get(
                "candidate_rubric_sha256"
            ),
            "staged_by": account,
            "staged_at": _now(),
        }
        draft.setdefault("revisions", []).append(revision)
        draft["current_rubric"] = copy.deepcopy(candidate["candidate_rubric"])
        draft["current_rubric_sha256"] = candidate["candidate_rubric_sha256"]
        draft["latest_candidate_id"] = candidate_id
        draft["status"] = "collecting"
        draft["updated_at"] = _now()
        _atomic_write(self._draft_path(session_id, draft["draft_id"]), draft)

        candidate["status"] = "staged"
        candidate["draft_id"] = draft["draft_id"]
        candidate["draft_revision_count"] = len(draft["revisions"])
        candidate["feedback_batch_ids"] = [
            item["batch_id"] for item in draft["revisions"]
        ]
        candidate["cumulative_operations"] = [
            copy.deepcopy(operation)
            for item in draft["revisions"]
            for operation in item.get("operations") or []
        ]
        candidate["cumulative_feedback_analysis"] = [
            copy.deepcopy(analysis)
            for item in draft["revisions"]
            for analysis in item.get("feedback_analysis") or []
        ]
        candidate["updated_at"] = _now()
        _atomic_write(self._candidate_path(session_id, candidate_id), candidate)
        batch["status"] = "staged"
        batch["draft_id"] = draft["draft_id"]
        batch["updated_at"] = _now()
        _atomic_write(self._batch_path(session_id, batch["batch_id"]), batch)
        return {"draft": draft, "candidate": candidate}

    def get_candidate(
        self, session_id: str, candidate_id: str
    ) -> Dict[str, Any]:
        path = self._candidate_path(session_id, candidate_id)
        if not path.is_file():
            raise RubricsLoopError("Candidate 不存在: %s" % candidate_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def update_candidate(
        self, session_id: str, candidate_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        value = self.get_candidate(session_id, candidate_id)
        value.update(copy.deepcopy(updates))
        value["updated_at"] = _now()
        _atomic_write(self._candidate_path(session_id, candidate_id), value)
        return value

    def edit_candidate_rubric(
        self,
        session_id: str,
        candidate_id: str,
        candidate_rubric: Dict[str, Any],
        account: str = "",
    ) -> Dict[str, Any]:
        value = self.get_candidate(session_id, candidate_id)
        if value.get("status") not in {"draft", "validated"}:
            raise RubricsLoopError("当前 Candidate 状态不能手工修改")
        batch = self.get_batch(session_id, value["source_batch_id"])
        validation = validate_candidate_rubric(
            batch["parent_rubric"], candidate_rubric
        )
        value["candidate_rubric"] = copy.deepcopy(candidate_rubric)
        value["candidate_rubric_sha256"] = json_sha256(candidate_rubric)
        value["validation"] = validation
        value["status"] = "validated" if validation["ok"] else "draft"
        value["edited_by"] = account
        value["updated_at"] = _now()
        _atomic_write(self._candidate_path(session_id, candidate_id), value)
        return value

    def create_experiment(
        self,
        session_id: str,
        candidate_id: str,
        config: Dict[str, Any],
        account: str = "",
        redline_confirmed: bool = False,
        selected_batch_ids: Optional[list[str]] = None,
    ) -> Dict[str, Any]:
        candidate = self.get_candidate(session_id, candidate_id)
        if candidate.get("status") not in {
            "validated", "staged", "awaiting_review"
        }:
            raise RubricsLoopError("只有已校验或已暂存的候选 Rubrics 可以启动实验")
        if candidate.get("draft_id") and candidate.get("status") == "validated":
            raise RubricsLoopError("请先把本轮修改暂存到待验证草案，再验证累计版本")
        validation = candidate.get("validation") or {}
        if validation.get("requires_redline_confirmation") and not redline_confirmed:
            raise RubricsLoopError("Candidate 包含红线变化，需要开发者确认")
        state = self._state(session_id)
        if json_sha256(state.get("rubric") or {}) != candidate["parent_rubric_sha256"]:
            raise RubricsLoopError("父 Rubrics 已变化，Candidate 已过期")
        included_batch_ids = [
            str(value) for value in (
                candidate.get("feedback_batch_ids")
                or [candidate.get("source_batch_id")]
            ) if value
        ]
        explicit_selection = [
            str(value) for value in (selected_batch_ids or []) if value
        ]
        if explicit_selection and explicit_selection != included_batch_ids:
            raise RubricsLoopError(
                "所选反馈轮次与候选 Rubrics 的累计范围不一致，请重新选择"
            )
        if candidate.get("draft_id") and not explicit_selection:
            draft = self.get_draft(session_id, candidate["draft_id"])
            if str(draft.get("latest_candidate_id") or "") != candidate_id:
                raise RubricsLoopError(
                    "该候选不是待验证草案的最新累计版本；请从迭代历史选择要验证的轮次"
                )
        for existing in self._list_loop_documents(session_id, "experiments"):
            if existing.get("status") in {"created", "queued", "running"}:
                raise RubricsLoopError(
                    "当前 Session 已有验证实验运行中: %s"
                    % existing.get("experiment_id")
                )
        for existing in self._list_loop_documents(session_id, "experiments"):
            if (
                existing.get("candidate_id") == candidate_id
                and existing.get("candidate_rubric_sha256")
                == candidate.get("candidate_rubric_sha256")
            ):
                raise RubricsLoopError(
                    "该候选 Rubrics 已有验证实验 %s，请打开或原地重试，不能重复新建"
                    % existing.get("experiment_id")
                )
        config = self._normalize_experiment_config(config)
        experiment_id = "rx-" + uuid.uuid4().hex[:10]
        experiment = {
            "experiment_id": experiment_id,
            "session_id": session_id,
            "candidate_id": candidate_id,
            "candidate_rubric_sha256": candidate["candidate_rubric_sha256"],
            "included_batch_ids": included_batch_ids,
            "config": copy.deepcopy(config),
            "status": "created",
            "phase": "skill_loop",
            "created_by": account,
            "created_at": _now(),
            "updated_at": _now(),
        }
        _atomic_write(self._experiment_path(session_id, experiment_id), experiment)
        candidate["status"] = "running"
        candidate["experiment_id"] = experiment_id
        candidate["updated_at"] = _now()
        _atomic_write(self._candidate_path(session_id, candidate_id), candidate)
        if candidate.get("draft_id"):
            draft = self.get_draft(session_id, candidate["draft_id"])
            draft["status"] = "validating"
            draft["experiment_id"] = experiment_id
            draft["updated_at"] = _now()
            _atomic_write(self._draft_path(session_id, draft["draft_id"]), draft)
        return experiment

    @staticmethod
    def _normalize_experiment_config(config: Dict[str, Any]) -> Dict[str, Any]:
        value = copy.deepcopy(config or {})
        try:
            rounds = int(value.get("skill_iteration_rounds") or 2)
        except (TypeError, ValueError):
            rounds = 2
        value["skill_iteration_rounds"] = max(1, min(rounds, 5))
        value["feedback_acceptance_enabled"] = value.get(
            "feedback_acceptance_enabled", True
        ) is not False
        value["memory_enabled"] = value.get("memory_enabled", False) is True
        value["memory_user"] = _memory_user(
            value.get("memory_user") or "local"
        )
        if not isinstance(value.get("acceptance"), dict):
            value["acceptance"] = copy.deepcopy(value.get("judge") or {})
        return value

    def get_experiment(
        self, session_id: str, experiment_id: str
    ) -> Dict[str, Any]:
        path = self._experiment_path(session_id, experiment_id)
        if not path.is_file():
            raise RubricsLoopError("Experiment 不存在: %s" % experiment_id)
        return json.loads(path.read_text(encoding="utf-8"))

    def retry_experiment(
        self,
        session_id: str,
        experiment_id: str,
        candidate_id: str,
        config: Dict[str, Any],
        account: str = "",
        redline_confirmed: bool = False,
    ) -> Dict[str, Any]:
        candidate = self.get_candidate(session_id, candidate_id)
        experiment = self.get_experiment(session_id, experiment_id)
        if experiment.get("candidate_id") != candidate_id:
            raise RubricsLoopError("验证实验与候选 Rubrics 不匹配")
        if experiment.get("status") != "failed":
            raise RubricsLoopError("只有失败的验证实验可以原地重试")
        if candidate.get("status") not in {
            "validated", "staged", "awaiting_review"
        }:
            raise RubricsLoopError("当前候选 Rubrics 状态不能重试实验")
        if candidate.get("draft_id") and candidate.get("status") == "validated":
            raise RubricsLoopError("请先把本轮修改暂存到待验证草案，再验证累计版本")
        validation = candidate.get("validation") or {}
        if validation.get("requires_redline_confirmation") and not redline_confirmed:
            raise RubricsLoopError("Candidate 包含红线变化，需要开发者确认")
        state = self._state(session_id)
        if json_sha256(state.get("rubric") or {}) != candidate["parent_rubric_sha256"]:
            raise RubricsLoopError("父 Rubrics 已变化，Candidate 已过期")
        if (
            experiment.get("candidate_rubric_sha256")
            != candidate.get("candidate_rubric_sha256")
        ):
            raise RubricsLoopError("候选 Rubrics 已变化，不能复用原验证实验")
        if not experiment.get("experiment_session_id"):
            raise RubricsLoopError("原验证实验缺少隔离 Session，不能原地重试")

        attempts = copy.deepcopy(experiment.get("attempts") or [])
        attempts.append({
            "status": experiment.get("status"),
            "error": experiment.get("error"),
            "config": copy.deepcopy(experiment.get("config") or {}),
            "started_at": experiment.get("started_at"),
            "finished_at": experiment.get("finished_at"),
            "recorded_at": _now(),
        })
        retry_config = self._normalize_experiment_config(config)
        if experiment.get("loop_completed_at"):
            previous_config = copy.deepcopy(experiment.get("config") or {})
            previous_config["acceptance"] = copy.deepcopy(
                retry_config.get("acceptance")
                or previous_config.get("acceptance")
                or previous_config.get("judge")
                or {}
            )
            previous_config["feedback_acceptance_enabled"] = True
            retry_config = self._normalize_experiment_config(previous_config)
        experiment.update({
            "config": retry_config,
            "status": "created",
            "attempts": attempts,
            "retry_count": int(experiment.get("retry_count") or 0) + 1,
            "retried_by": account,
            "retried_at": _now(),
            "updated_at": _now(),
        })
        for key in ("error", "started_at", "finished_at", "result_code"):
            experiment.pop(key, None)
        if experiment.get("loop_completed_at"):
            experiment["phase"] = "feedback_acceptance"
        else:
            for key in ("feedback_results", "comparison", "final_state"):
                experiment.pop(key, None)
            experiment["phase"] = "skill_loop"
        _atomic_write(
            self._experiment_path(session_id, experiment_id), experiment
        )

        candidate["status"] = "running"
        candidate["experiment_id"] = experiment_id
        candidate.pop("experiment_error", None)
        candidate["updated_at"] = _now()
        _atomic_write(self._candidate_path(session_id, candidate_id), candidate)
        if candidate.get("draft_id"):
            draft = self.get_draft(session_id, candidate["draft_id"])
            draft["status"] = "validating"
            draft["experiment_id"] = experiment_id
            draft["updated_at"] = _now()
            _atomic_write(self._draft_path(session_id, draft["draft_id"]), draft)
        return experiment

    def get_acceptance(
        self, session_id: str, experiment_id: str
    ) -> Optional[Dict[str, Any]]:
        path = self._acceptance_path(session_id, experiment_id)
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RubricsLoopError("AI 验收记录损坏") from exc
        return value if isinstance(value, dict) else None

    def _experiment_report(
        self, experiment_session_id: str, skill_version: str, case_id: str
    ) -> str:
        try:
            output = dashboard_api.generation_case_report_document(
                self.root,
                self.sessions_root,
                experiment_session_id,
                skill_version,
                case_id,
                generation_root=self.generation_root,
            )
        except (OSError, ValueError, FileNotFoundError) as exc:
            raise RubricsLoopError(str(exc)) from exc
        return str(output.get("report_text") or "")

    def _candidate_feedback_links(
        self, candidate: Dict[str, Any]
    ) -> tuple[Dict[str, str], Dict[str, set[str]]]:
        categories: Dict[str, str] = {}
        checks: Dict[str, set[str]] = {}
        analyses = candidate.get("cumulative_feedback_analysis") or (
            candidate.get("feedback_analysis") or []
        )
        operations = candidate.get("cumulative_operations") or (
            candidate.get("operations") or []
        )
        for item in analyses:
            feedback_id = str(item.get("feedback_id") or "")
            if not feedback_id:
                continue
            categories[feedback_id] = str(item.get("category") or "")
            checks.setdefault(feedback_id, set()).update(
                str(value) for value in item.get("existing_check_ids") or []
                if value
            )
        for operation in operations:
            operation_checks = self._operation_check_ids(operation)
            for feedback_id in operation.get("feedback_ids") or []:
                checks.setdefault(str(feedback_id), set()).update(
                    operation_checks
                )
        return categories, checks

    def evaluate_feedback_acceptance(
        self,
        session_id: str,
        experiment_id: str,
        call_model: Optional[Callable[..., str]] = None,
    ) -> Dict[str, Any]:
        """Compare baseline and two Skill iterations against each Feedback."""
        experiment = self.get_experiment(session_id, experiment_id)
        if not experiment.get("loop_completed_at"):
            raise RubricsLoopError("Skill Loop 尚未完成，不能启动 AI 验收")
        candidate = self.get_candidate(session_id, experiment["candidate_id"])
        batch_ids = candidate.get("feedback_batch_ids") or [
            candidate["source_batch_id"]
        ]
        batches = [self.get_batch(session_id, value) for value in batch_ids]
        feedback = [
            item for batch in batches for item in self._rubric_feedback(batch)
        ]
        report_refs = []
        for batch in batches:
            for ref in batch.get("report_refs") or []:
                key = (ref.get("skill_version"), ref.get("case_id"))
                if not any(
                    (item.get("skill_version"), item.get("case_id")) == key
                    for item in report_refs
                ):
                    report_refs.append(ref)

        curve = ((experiment.get("final_state") or {}).get("curve") or [])
        curve_versions = [
            str(item.get("version")) for item in curve if item.get("version")
        ]
        rounds = int((experiment.get("config") or {}).get(
            "skill_iteration_rounds", 2
        ))
        iteration_versions = curve_versions[1:1 + rounds]
        if len(iteration_versions) < rounds:
            raise RubricsLoopError(
                "Skill Loop 只完成 %d/%d 轮，不能启动 AI 验收"
                % (len(iteration_versions), rounds)
            )
        categories, check_links = self._candidate_feedback_links(candidate)
        feedback_analysis = candidate.get("cumulative_feedback_analysis") or (
            candidate.get("feedback_analysis") or []
        )
        operations = candidate.get("cumulative_operations") or (
            candidate.get("operations") or []
        )
        ref_index = {
            (ref.get("skill_version"), ref.get("case_id")): ref
            for ref in report_refs
        }
        contexts: Dict[str, Dict[str, Any]] = {}
        experiment_session_id = str(experiment.get("experiment_session_id") or "")
        for item in feedback:
            feedback_id = str(item.get("feedback_id") or "")
            linked_refs = report_refs if item.get("scope") == "batch" else []
            if not linked_refs:
                source_ref = item.get("report_ref") or {}
                ref = ref_index.get((
                    source_ref.get("skill_version"), source_ref.get("case_id")
                ))
                linked_refs = [ref] if ref else []
            reports = []
            judge_signals = []
            for ref in linked_refs:
                source_report = self.report(
                    session_id,
                    ref["skill_version"],
                    ref["case_id"],
                    ref.get("report_sha256") or "",
                    ref.get("rubric_sha256") or "",
                )
                reports.append({
                    "phase": "baseline",
                    "skill_version": ref["skill_version"],
                    "case_id": ref["case_id"],
                    "report_text": source_report["report_text"],
                })
                for number, version in enumerate(iteration_versions, start=1):
                    reports.append({
                        "phase": "iteration_%d" % number,
                        "skill_version": version,
                        "case_id": ref["case_id"],
                        "report_text": self._experiment_report(
                            experiment_session_id, version, ref["case_id"]
                        ),
                    })
                    try:
                        judgment = dashboard_api.case_judgment_document(
                            self.sessions_root,
                            experiment_session_id,
                            version,
                            ref["case_id"],
                        )
                    except (OSError, ValueError, FileNotFoundError):
                        judgment = {}
                    linked_checks = sorted(check_links.get(feedback_id) or [])
                    judge_signals.append({
                        "skill_version": version,
                        "case_id": ref["case_id"],
                        "checks": {
                            check_id: (judgment.get("checks") or {}).get(check_id)
                            for check_id in linked_checks
                        },
                    })
            contexts[feedback_id] = {
                "category": categories.get(feedback_id),
                "skill_versions": ["baseline"] + iteration_versions,
                "rubric_changes": [
                    operation for operation in operations
                    if feedback_id in (operation.get("feedback_ids") or [])
                ],
                "feedback_analysis": [
                    analysis for analysis in feedback_analysis
                    if str(analysis.get("feedback_id") or "") == feedback_id
                ],
                "reports": reports,
                "judge_signals": judge_signals,
            }

        model_config = copy.deepcopy(
            (experiment.get("config") or {}).get("acceptance")
            or (experiment.get("config") or {}).get("judge")
            or {}
        )
        stored = self.get_acceptance(session_id, experiment_id) or {}
        partial = {}
        if stored.get("model_config") == model_config:
            partial = {
                str(item.get("feedback_id")): item
                for item in stored.get("feedback_results") or []
                if item.get("feedback_id")
            }
        record = {
            "experiment_id": experiment_id,
            "session_id": session_id,
            "experiment_session_id": experiment_session_id,
            "status": "running",
            "skill_versions": ["baseline"] + iteration_versions,
            "model_config": model_config,
            "feedback_results": list(partial.values()),
            "started_at": stored.get("started_at") or _now(),
            "updated_at": _now(),
        }
        _atomic_write(self._acceptance_path(session_id, experiment_id), record)

        def persist_result(result: Dict[str, Any]):
            partial[result["feedback_id"]] = result
            record["feedback_results"] = list(partial.values())
            record["updated_at"] = _now()
            _atomic_write(self._acceptance_path(session_id, experiment_id), record)

        try:
            result = feedback_acceptance.evaluate(
                feedback,
                contexts,
                model_config,
                existing_results=partial,
                on_result=persist_result,
                call_model=call_model,
            )
        except Exception as exc:
            record.update({
                "status": "failed",
                "error": str(exc),
                "feedback_results": list(partial.values()),
                "updated_at": _now(),
            })
            _atomic_write(self._acceptance_path(session_id, experiment_id), record)
            raise
        record.update(result)
        record["finished_at"] = _now()
        record["updated_at"] = _now()
        _atomic_write(self._acceptance_path(session_id, experiment_id), record)
        return record

    def update_experiment(
        self, session_id: str, experiment_id: str, updates: Dict[str, Any]
    ) -> Dict[str, Any]:
        value = self.get_experiment(session_id, experiment_id)
        value.update(copy.deepcopy(updates))
        value["updated_at"] = _now()
        _atomic_write(self._experiment_path(session_id, experiment_id), value)
        return value

    def _registry_paths(self, product_id: str) -> tuple[Path, Path]:
        product_id = _safe_segment(product_id, " Product ID")
        root = (self.registry_root / product_id).resolve()
        root.relative_to(self.registry_root)
        return root / "registry.json", root / "versions"

    def _next_version(self, current: str, existing: Iterable[str]) -> str:
        match = re.fullmatch(r"v(\d+)\.(\d+)", str(current or ""))
        if match:
            major, patch = int(match.group(1)), int(match.group(2)) + 1
            while "v%d.%d" % (major, patch) in existing:
                patch += 1
            return "v%d.%d" % (major, patch)
        number = 1
        while "r%d" % number in existing:
            number += 1
        return "r%d" % number

    def adopt_candidate(
        self, session_id: str, candidate_id: str, account: str = ""
    ) -> Dict[str, Any]:
        candidate = self.get_candidate(session_id, candidate_id)
        if candidate.get("status") not in {"validated", "awaiting_review"}:
            raise RubricsLoopError("Candidate 尚未通过验证或已被处理")
        state = self._state(session_id)
        current_hash = json_sha256(state.get("rubric") or {})
        if current_hash != candidate["parent_rubric_sha256"]:
            raise RubricsLoopError("父 Rubrics 已变化，Candidate 已过期")
        rubric = copy.deepcopy(candidate["candidate_rubric"])
        product_id = str(rubric.get("product") or state.get("product_id") or "")
        registry_path, versions_root = self._registry_paths(product_id)
        registry = (
            json.loads(registry_path.read_text(encoding="utf-8"))
            if registry_path.is_file()
            else {"product_id": product_id, "default_version": None, "versions": []}
        )
        existing = [str(item.get("version")) for item in registry.get("versions") or []]
        version = self._next_version(candidate["parent_rubric_version"], existing)
        rubric["version"] = version
        rubric_hash = json_sha256(rubric)
        version_path = versions_root / (version + ".json")
        if version_path.exists():
            raise RubricsLoopError("Rubrics 版本已存在: %s" % version)
        _atomic_write(version_path, rubric)
        registry.setdefault("versions", []).append({
            "version": version,
            "rubric_sha256": rubric_hash,
            "parent_version": candidate["parent_rubric_version"],
            "candidate_id": candidate_id,
            "created_by": account,
            "created_at": _now(),
        })
        _atomic_write(registry_path, registry)
        candidate["status"] = "adopted"
        candidate["adopted_version"] = version
        candidate["adopted_rubric_sha256"] = rubric_hash
        candidate["updated_at"] = _now()
        _atomic_write(self._candidate_path(session_id, candidate_id), candidate)
        if candidate.get("draft_id"):
            draft = self.get_draft(session_id, candidate["draft_id"])
            draft["status"] = "adopted"
            draft["adopted_version"] = version
            draft["updated_at"] = _now()
            _atomic_write(self._draft_path(session_id, draft["draft_id"]), draft)
        return {"version": version, "rubric_sha256": rubric_hash, "rubric": rubric}

    def reject_candidate(
        self, session_id: str, candidate_id: str, reason: str, account: str = ""
    ) -> Dict[str, Any]:
        candidate = self.get_candidate(session_id, candidate_id)
        candidate["status"] = "rejected"
        candidate["decision_reason"] = str(reason or "").strip()
        candidate["decided_by"] = account
        candidate["updated_at"] = _now()
        _atomic_write(self._candidate_path(session_id, candidate_id), candidate)
        return candidate

    def set_default(self, product_id: str, version: str) -> Dict[str, Any]:
        registry_path, versions_root = self._registry_paths(product_id)
        if not (versions_root / (_safe_segment(version, " Rubrics 版本") + ".json")).is_file():
            raise RubricsLoopError("Rubrics 版本不存在: %s" % version)
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        registry["default_version"] = version
        registry["updated_at"] = _now()
        _atomic_write(registry_path, registry)
        return registry
