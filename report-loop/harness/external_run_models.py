# -*- coding: utf-8 -*-
"""真实外部报告生成链路的数据契约。

这些类型只描述 OpenHarness 与外部执行器之间的稳定边界；WorkBuddy 的
stream-json、workspace 和 subprocess 细节留在 ``workbuddy_batch`` 内部。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


@dataclass(frozen=True)
class ReportOutputContract:
    """Runner 用于验收最终报告的硬约束。"""

    required_glob: str = "deliverables/report.md"
    allowed_extensions: Tuple[str, ...] = (".md",)
    min_bytes: int = 500
    max_files: int = 1

    def __post_init__(self) -> None:
        if not self.required_glob.strip():
            raise ValueError("required_glob 不能为空")
        if self.min_bytes < 1:
            raise ValueError("min_bytes 必须至少为 1")
        if self.max_files < 1:
            raise ValueError("max_files 必须至少为 1")
        normalized = tuple(
            item.lower() if item.startswith(".") else f".{item.lower()}"
            for item in self.allowed_extensions
        )
        object.__setattr__(self, "allowed_extensions", normalized)


@dataclass(frozen=True)
class ExternalRunRequest:
    """一次 OpenHarness → WorkBuddy 批量生成请求。"""

    case_file: Path
    output_root: Path
    skill_version: str
    session_id: Optional[str] = None
    skill_name: Optional[str] = None
    skill_path: Optional[Path] = None
    model: Optional[str] = None
    effort: Optional[str] = None
    parallel: int = 20
    timeout_seconds: float = 900.0
    stall_timeout_seconds: float = 180.0
    max_report_retries: int = 3
    output_contract: ReportOutputContract = field(
        default_factory=ReportOutputContract
    )
    command: Optional[Tuple[str, ...]] = None
    workbuddy_home: Optional[Path] = None
    product_config: Optional[Path] = None
    allowed_material_roots: Tuple[Path, ...] = ()
    allowed_tools: Tuple[str, ...] = ()
    disallowed_tools: Tuple[str, ...] = ()
    environment: Dict[str, str] = field(default_factory=dict)
    case_map: Dict[str, Dict[str, str]] = field(default_factory=dict)
    openharness_case_ids: Tuple[str, ...] = ()
    auto_judge: bool = False
    persist_report_text: bool = True

    def __post_init__(self) -> None:
        if not self.skill_version.strip():
            raise ValueError("skill_version 不能为空")
        if bool(self.skill_name) == bool(self.skill_path):
            raise ValueError("skill_name 与 skill_path 必须且只能指定一个")
        if self.parallel < 1:
            raise ValueError("parallel 必须至少为 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if self.stall_timeout_seconds <= 0:
            raise ValueError("stall_timeout_seconds 必须大于 0")
        if self.max_report_retries < 0:
            raise ValueError("max_report_retries 不能小于 0")
        if self.command is not None and not self.command:
            raise ValueError("command 不能为空 tuple")

    @property
    def max_attempts(self) -> int:
        return 1 + self.max_report_retries

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        for key in (
            "case_file",
            "output_root",
            "skill_path",
            "workbuddy_home",
            "product_config",
        ):
            value = payload.get(key)
            payload[key] = str(value) if value is not None else None
        payload["allowed_material_roots"] = [
            str(item) for item in self.allowed_material_roots
        ]
        payload["openharness_case_ids"] = list(
            self.openharness_case_ids
        )
        payload["command"] = list(self.command) if self.command else None
        # 环境变量可能含密钥，只落盘变量名。
        payload["environment"] = sorted(self.environment)
        return payload


@dataclass(frozen=True)
class ReportArtifact:
    original_workspace_path: str
    captured_path: str
    sha256: str
    size: int
    mime_type: str
    text: str

    def to_dict(self, include_text: bool = True) -> Dict[str, Any]:
        payload = asdict(self)
        if not include_text:
            payload.pop("text", None)
        return payload


@dataclass(frozen=True)
class ExternalAttemptResult:
    attempt: int
    max_attempts: int
    wb_case_id: str
    openharness_case_id: str
    status: str
    wb_status: str
    wb_run_id: str
    wb_session_id: Optional[str]
    run_dir: str
    trace_path: str
    manifest_path: str
    duration_ms: Optional[int]
    configured_model: Optional[str]
    observed_models: Tuple[str, ...]
    usage: Dict[str, Any]
    error: Optional[str] = None
    warning: Optional[str] = None
    report: Optional[ReportArtifact] = None

    @property
    def has_valid_report(self) -> bool:
        return self.report is not None

    def to_dict(self, include_report_text: bool = True) -> Dict[str, Any]:
        payload = asdict(self)
        payload["report"] = (
            self.report.to_dict(include_report_text) if self.report else None
        )
        return payload


@dataclass
class ExternalCaseResult:
    wb_case_id: str
    openharness_case_id: str
    split: str
    status: str = "queued"
    attempts: list[ExternalAttemptResult] = field(default_factory=list)
    report: Optional[ReportArtifact] = None

    def to_dict(self, include_report_text: bool = True) -> Dict[str, Any]:
        return {
            "wb_case_id": self.wb_case_id,
            "openharness_case_id": self.openharness_case_id,
            "split": self.split,
            "status": self.status,
            "attempt_count": len(self.attempts),
            "report": (
                self.report.to_dict(include_report_text) if self.report else None
            ),
            "attempts": [
                item.to_dict(include_report_text) for item in self.attempts
            ],
        }


@dataclass
class ExternalBatchResult:
    generation_id: str
    session_id: Optional[str]
    skill_version: str
    status: str
    output_dir: str
    created_at: str
    finished_at: str
    cases: list[ExternalCaseResult]

    @property
    def succeeded(self) -> bool:
        return bool(self.cases) and all(
            item.status == "generated" for item in self.cases
        )

    def to_dict(self, include_report_text: bool = True) -> Dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "session_id": self.session_id,
            "skill_version": self.skill_version,
            "status": self.status,
            "output_dir": self.output_dir,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "case_count": len(self.cases),
            "generated_count": sum(
                item.status == "generated" for item in self.cases
            ),
            "cases": [
                item.to_dict(include_report_text) for item in self.cases
            ],
        }
