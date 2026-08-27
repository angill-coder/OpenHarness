"""Read a stable L2B Rubric snapshot from the local Memory Git repository."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
import unicodedata
from pathlib import Path
from typing import Any


_SCOPE_PRIORITY = {"core": 0, "audience": 1, "project": 2}


def storage_slug(value: str) -> str:
    normalized = canonical_scope_value(value).lower()
    pieces: list[str] = []
    separated = False
    for character in normalized:
        if character.isalnum():
            pieces.append(character)
            separated = False
        elif pieces and not separated:
            pieces.append("-")
            separated = True
    slug = "".join(pieces).strip("-")
    return slug or hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:12]


def canonical_scope_value(value: str) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip()


def scope_storage_key(value: str) -> str:
    canonical = canonical_scope_value(value)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{storage_slug(canonical)}--{digest}"


class MemoryRubricProvider:
    """Read-only provider; it never starts the Memory MCP or mutates Git."""

    def __init__(self, memory_data_dir: Path) -> None:
        self.memory_data_dir = Path(memory_data_dir).expanduser().resolve()
        self.repository = (
            self.memory_data_dir
            / "l2b-rubrics"
            / "personal"
            / "default"
        )

    def _git(self, *args: str) -> str:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.repository,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            timeout=8,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(detail or "git command failed")
        return completed.stdout

    def _memory_enabled(self) -> bool:
        settings_path = self.memory_data_dir / "settings.json"
        try:
            value = json.loads(settings_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            return False
        return (
            isinstance(value, dict)
            and value.get("schemaVersion") == 1
            and value.get("memoryEnabled") is True
        )

    def _read_document(self, head: str, relative_path: str) -> dict[str, Any] | None:
        completed = subprocess.run(
            ["git", "show", f"{head}:{relative_path}"],
            cwd=self.repository,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            timeout=8,
            check=False,
        )
        if completed.returncode != 0:
            return None
        value = json.loads(completed.stdout)
        if not isinstance(value, dict) or value.get("schemaVersion") not in (1, 2, 3):
            raise ValueError(f"invalid memory rubric document: {relative_path}")
        value["schemaVersion"] = 3
        value["rubrics"] = [
            {
                "id": str(item.get("id") or "").strip(),
                "statement": str(item.get("statement") or item.get("desc") or "").strip(),
                "status": str(item.get("status") or "active"),
                "sourceL1Ids": list(item.get("sourceL1Ids") or []),
            }
            for item in value.get("rubrics") or []
            if isinstance(item, dict)
        ]
        return value

    def _read_manifest(self, head: str) -> dict[str, Any]:
        completed = subprocess.run(
            ["git", "show", f"{head}:manifest.json"],
            cwd=self.repository,
            text=True,
            encoding="utf-8",
            errors="strict",
            capture_output=True,
            timeout=8,
            check=False,
        )
        if completed.returncode != 0:
            return {"version": "legacy", "versionNumber": None}
        value = json.loads(completed.stdout)
        if not isinstance(value, dict) or value.get("schemaVersion") != 1:
            raise ValueError("invalid rubric set manifest")
        return value

    def _document_paths(self, head: str) -> list[tuple[str, str]]:
        tracked = self._git(
            "-c", "core.quotePath=false", "ls-tree", "-r", "--name-only", head,
            "--", "system", "audiences", "projects",
        ).splitlines()
        candidates: list[tuple[str, str]] = []
        for relative_path in tracked:
            if relative_path == "system/rubrics.json":
                candidates.append(("core", relative_path))
            elif relative_path.startswith("audiences/") and relative_path.endswith("/rubrics.json"):
                candidates.append(("audience", relative_path))
            elif relative_path.startswith("projects/") and relative_path.endswith("/rubrics.json"):
                candidates.append(("project", relative_path))
        return sorted(candidates, key=lambda value: (_SCOPE_PRIORITY[value[0]], value[1]))

    def load_sources(self, source_l1_ids: list[str]) -> list[dict[str, Any]]:
        """Read only the exact L1 atoms referenced by selected L2B rubrics."""

        if not self._memory_enabled():
            return []

        requested = list(dict.fromkeys(
            str(value or "").strip() for value in source_l1_ids if str(value or "").strip()
        ))
        if not requested:
            return []
        database = self.memory_data_dir / "l0-l1-memory" / "memorycore" / "vectors.db"
        if not database.is_file():
            return []
        placeholders = ",".join("?" for _ in requested)
        try:
            with sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True, timeout=3) as connection:
                rows = connection.execute(
                    f"""SELECT record_id, content, metadata_json
                        FROM l1_records
                        WHERE record_id IN ({placeholders})""",
                    requested,
                ).fetchall()
        except sqlite3.Error:
            return []
        by_id: dict[str, dict[str, Any]] = {}
        for record_id, content, metadata_raw in rows:
            try:
                metadata = json.loads(metadata_raw or "{}")
            except json.JSONDecodeError:
                metadata = {}
            if metadata.get("domain") != "report_writing":
                continue
            by_id[str(record_id)] = {
                "id": str(record_id),
                "content": str(content or "").strip(),
                "scope": metadata.get("scope"),
                "scopeValue": metadata.get("scopeValue"),
                "lifecycle": metadata.get("lifecycle"),
            }
        return [by_id[value] for value in requested if value in by_id]

    def load(self, *, audience: str = "", project: str = "") -> dict[str, Any]:
        if not self._memory_enabled():
            return {
                "status": "disabled",
                "revision": None,
                "rubricSetVersion": None,
                "documents": [],
                "items": [],
                "warnings": [],
            }
        if not self.repository.exists():
            return {
                "status": "empty",
                "revision": None,
                "rubricSetVersion": None,
                "documents": [],
                "items": [],
                "warnings": [],
            }
        try:
            head = self._git("rev-parse", "HEAD").strip()
            manifest = self._read_manifest(head)
            selected: list[dict[str, Any]] = []
            document_metadata: list[dict[str, Any]] = []
            for expected_scope, relative_path in self._document_paths(head):
                document = self._read_document(head, relative_path)
                if document is None:
                    continue
                if document.get("scope") != expected_scope:
                    raise ValueError(f"memory rubric scope mismatch: {relative_path}")
                stored_value = str(document.get("scopeValue") or "")
                if expected_scope != "core" and not canonical_scope_value(stored_value):
                    raise ValueError(f"memory rubric scopeValue missing: {relative_path}")
                for raw_item in document.get("rubrics") or []:
                    if not isinstance(raw_item, dict):
                        continue
                    item_id = str(raw_item.get("id") or "").strip()
                    if not item_id or raw_item.get("status") != "active":
                        continue
                    item = {
                        **raw_item,
                        "scope": expected_scope,
                        "scopeValue": stored_value or None,
                        "sourcePath": relative_path,
                    }
                    selected.append(item)
                document_metadata.append({
                    "path": relative_path,
                    "scope": expected_scope,
                    "scopeValue": stored_value or None,
                })

            id_counts: dict[str, int] = {}
            for item in selected:
                item_id = str(item["id"])
                id_counts[item_id] = id_counts.get(item_id, 0) + 1
            for item in selected:
                original_id = str(item["id"])
                if id_counts[original_id] <= 1:
                    continue
                identity = "\0".join([
                    original_id,
                    str(item.get("scope") or "core"),
                    str(item.get("scopeValue") or ""),
                    str(item.get("sourcePath") or ""),
                ])
                item["sourceMemoryId"] = original_id
                item["id"] = (
                    f"{original_id}::{item.get('scope')}:"
                    f"{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:10]}"
                )
            items = sorted(
                selected,
                key=lambda item: (
                    _SCOPE_PRIORITY[str(item.get("scope") or "core")],
                    str(item.get("scopeValue") or ""),
                    str(item["id"]),
                ),
            )
            documents = [
                {
                    **metadata,
                    "itemIds": [
                        str(item["id"])
                        for item in items
                        if item.get("sourcePath") == metadata["path"]
                    ],
                }
                for metadata in document_metadata
            ]
            return {
                "status": "loaded",
                "revision": head,
                "rubricSetVersion": manifest.get("version"),
                "queryContext": {
                    "audience": str(audience or "").strip(),
                    "project": str(project or "").strip(),
                },
                "documents": documents,
                "items": items,
                "warnings": [],
            }
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            return {
                "status": "unavailable",
                "revision": None,
                "rubricSetVersion": None,
                "documents": [],
                "items": [],
                "warnings": [f"memory_rubric_provider:{exc}"],
            }
