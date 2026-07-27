# -*- coding: utf-8 -*-
"""前端真实报告生成任务的数据契约。"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


ACTIVE_STATES = {
    "queued",
    "running",
    "retrying",
    "importing",
    "cancel_requested",
}
TERMINAL_STATES = {
    "completed",
    "partial",
    "failed",
    "cancelled",
    "interrupted",
}


@dataclass
class GenerationCaseState:
    case_id: str
    split: str
    status: str = "queued"
    attempts: int = 0
    error: Optional[str] = None
    report_sha256: Optional[str] = None
    report_size: Optional[int] = None
    imported: bool = False

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "GenerationCaseState":
        return cls(
            case_id=str(payload["case_id"]),
            split=str(payload.get("split") or "dev"),
            status=str(payload.get("status") or "queued"),
            attempts=int(payload.get("attempts") or 0),
            error=payload.get("error"),
            report_sha256=payload.get("report_sha256"),
            report_size=payload.get("report_size"),
            imported=bool(payload.get("imported", False)),
        )


@dataclass
class GenerationJob:
    job_id: str
    session_id: str
    account: str
    skill_version: str
    skill_artifact_hash: str
    execution_skill_hash: Optional[str]
    dataset_path: str
    skill_mode: str
    skill_ref: str
    model: Optional[str]
    parallel: int
    max_report_retries: int
    timeout_seconds: float
    stall_timeout_seconds: float
    created_at: float
    updated_at: float
    dataset_sha256: Optional[str] = None
    compiler_version: Optional[str] = None
    status: str = "queued"
    cases: List[GenerationCaseState] = field(default_factory=list)
    idempotency_key: Optional[str] = None
    parent_job_id: Optional[str] = None
    generation_id: Optional[str] = None
    output_dir: Optional[str] = None
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    error: Optional[str] = None
    cancel_requested: bool = False

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATES

    @property
    def terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    @property
    def generated_count(self) -> int:
        return sum(
            item.status in {"generated", "imported"} or item.imported
            for item in self.cases
        )

    @property
    def imported_count(self) -> int:
        return sum(item.imported for item in self.cases)

    @property
    def failed_case_ids(self) -> List[str]:
        return [
            item.case_id
            for item in self.cases
            if not item.imported
        ]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "active": self.active,
                "terminal": self.terminal,
                "case_count": len(self.cases),
                "generated_count": self.generated_count,
                "imported_count": self.imported_count,
                "failed_case_ids": self.failed_case_ids,
            }
        )
        return payload

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "GenerationJob":
        fields = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                "active",
                "terminal",
                "case_count",
                "generated_count",
                "imported_count",
                "failed_case_ids",
            }
        }
        fields["cases"] = [
            GenerationCaseState.from_dict(item)
            for item in payload.get("cases", [])
        ]
        fields.setdefault("dataset_sha256", None)
        fields.setdefault("compiler_version", None)
        return cls(**fields)
