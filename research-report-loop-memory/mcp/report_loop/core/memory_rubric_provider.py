"""Read a stable L2B Rubric snapshot from the local Memory Git repository."""

from __future__ import annotations

import hashlib
import json
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
            capture_output=True,
            timeout=8,
            check=False,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(detail or "git command failed")
        return completed.stdout

    def _read_document(self, head: str, relative_path: str) -> dict[str, Any] | None:
        completed = subprocess.run(
            ["git", "show", f"{head}:{relative_path}"],
            cwd=self.repository,
            text=True,
            capture_output=True,
            timeout=8,
            check=False,
        )
        if completed.returncode != 0:
            return None
        value = json.loads(completed.stdout)
        if not isinstance(value, dict) or value.get("schemaVersion") not in (1, 2):
            raise ValueError(f"invalid memory rubric document: {relative_path}")
        value["schemaVersion"] = 2
        value["rubrics"] = [
            {
                **item,
                "criterionKey": str(item.get("criterionKey") or item.get("id") or ""),
                "operation": str(item.get("operation") or "add"),
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

    def load(self, *, audience: str = "", project: str = "") -> dict[str, Any]:
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
            candidates = [
                ("core", "", "system/rubrics.json"),
            ]
            if audience.strip():
                candidates.append((
                    "audience",
                    audience.strip(),
                    f"audiences/{scope_storage_key(audience)}/rubrics.json",
                ))
            if project.strip():
                candidates.append((
                    "project",
                    project.strip(),
                    f"projects/{scope_storage_key(project)}/rubrics.json",
                ))

            manifest = self._read_manifest(head)
            documents: list[dict[str, Any]] = []
            selected: list[dict[str, Any]] = []
            for requested_scope, requested_value, relative_path in candidates:
                document = self._read_document(head, relative_path)
                if document is None:
                    continue
                if document.get("scope") != requested_scope:
                    raise ValueError(f"memory rubric scope mismatch: {relative_path}")
                stored_value = str(document.get("scopeValue") or "")
                if requested_scope != "core" and canonical_scope_value(stored_value) != canonical_scope_value(requested_value):
                    raise ValueError(f"memory rubric scopeValue mismatch: {relative_path}")
                scope_value = stored_value or requested_value
                item_ids: list[str] = []
                for raw_item in document.get("rubrics") or []:
                    if not isinstance(raw_item, dict):
                        continue
                    item_id = str(raw_item.get("id") or "").strip()
                    if not item_id or raw_item.get("status") != "active":
                        continue
                    item = {
                        **raw_item,
                        "scope": requested_scope,
                        "scopeValue": scope_value or None,
                        "sourcePath": relative_path,
                    }
                    selected.append(item)
                    item_ids.append(item_id)
                documents.append({
                    "path": relative_path,
                    "scope": requested_scope,
                    "scopeValue": scope_value or None,
                    "itemIds": item_ids,
                })
            items = sorted(
                selected,
                key=lambda item: (
                    _SCOPE_PRIORITY[str(item.get("scope") or "core")],
                    str(item.get("criterionKey") or ""),
                    str(item["id"]),
                ),
            )
            return {
                "status": "loaded",
                "revision": head,
                "rubricSetVersion": manifest.get("version"),
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
