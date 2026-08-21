"""Report Loop 独立持久化；不读写 Skill Loop 的 app/sessions。"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Iterable, Optional


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")


class ReportLoopStore:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        value = str(run_id or "").strip()
        if not _SAFE_ID.fullmatch(value):
            raise ValueError("非法 Report Run id")
        path = (self.root / value).resolve()
        path.relative_to(self.root)
        return path

    @staticmethod
    def _atomic_json(path: Path, payload: Dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def initialize(self, run: Dict) -> None:
        directory = self.run_dir(run["id"])
        directory.mkdir(parents=True, exist_ok=False)
        self._atomic_json(
            directory / "meta.json",
            {
                "id": run["id"],
                "creator": run.get("creator", ""),
                "data_id": run["data_id"],
                "case_id": run["case_id"],
                "created_at": run["created_at"],
                "loop_kind": "report",
            },
        )
        self.save(run)

    def save(self, run: Dict) -> None:
        payload = dict(run)
        payload["updated_at"] = round(time.time(), 3)
        run["updated_at"] = payload["updated_at"]
        self._atomic_json(self.run_dir(run["id"]) / "state.json", payload)

    def append_event(self, run_id: str, event: str, payload: Dict) -> None:
        path = self.run_dir(run_id) / "events.jsonl"
        record = {
            "ts": round(time.time(), 3),
            "type": event,
            "payload": payload,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def save_report(self, run_id: str, version: str, text: str) -> Path:
        path = self.run_dir(run_id) / "reports" / (version + ".md")
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".md.tmp")
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, path)
        return path

    def load_report(self, run_id: str, version: str) -> str:
        directory = self.run_dir(run_id)
        state_path = directory / "state.json"
        if state_path.is_file():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            item = next(
                (
                    value for value in state.get("revisions", [])
                    if value.get("version") == version
                ),
                None,
            )
            report_path = str((item or {}).get("report_path") or "").strip()
            if report_path:
                path = (directory / report_path).resolve()
                path.relative_to(directory)
                if path.is_file():
                    return path.read_text(encoding="utf-8")

        # Runs created before compact storage keep their report copy here.
        legacy = directory / "reports" / (version + ".md")
        return legacy.read_text(encoding="utf-8") if legacy.is_file() else ""

    def save_judgment(
        self,
        run_id: str,
        version: str,
        judgment: Dict,
    ) -> Path:
        path = self.run_dir(run_id) / "judgments" / (version + ".json")
        self._atomic_json(path, judgment)
        return path

    def load(self, run_id: str) -> Optional[Dict]:
        path = self.run_dir(run_id) / "state.json"
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def load_all(self) -> Iterable[Dict]:
        if not self.root.is_dir():
            return []
        runs = []
        for directory in self.root.iterdir():
            if not directory.is_dir() or not _SAFE_ID.fullmatch(directory.name):
                continue
            try:
                run = self.load(directory.name)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if run:
                runs.append(run)
        return sorted(
            runs,
            key=lambda item: float(item.get("created_at") or 0),
            reverse=True,
        )
