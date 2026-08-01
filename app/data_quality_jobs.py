# -*- coding: utf-8 -*-
"""Background jobs for the Web UI data-cleaning and quality workflow."""

from __future__ import annotations

import os
import json
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARNESS = ROOT / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from data_workflow import (  # noqa: E402
    DataQualityCancelled,
    DataWorkflowRequest,
    run_data_workflow,
)


class DataQualityJobError(ValueError):
    """A configuration or job-state error safe to expose to the UI."""


@dataclass
class DataQualityCaseState:
    case_id: str
    status: str = "queued"
    stage: Optional[str] = None
    overall_score: Optional[float] = None
    omission_score: Optional[float] = None
    conflict_score: Optional[float] = None
    signal_score: Optional[float] = None
    error: Optional[str] = None


@dataclass
class DataQualityJob:
    job_id: str
    session_id: str
    dataset_path: str
    output_root: str
    model: str
    effort: str
    parallel: int
    repair_metadata: bool
    created_at: float
    updated_at: float
    status: str = "queued"
    cases: list[DataQualityCaseState] = field(default_factory=list)
    average_score: Optional[float] = None
    median_score: Optional[float] = None
    average_omission_score: Optional[float] = None
    average_conflict_score: Optional[float] = None
    average_signal_score: Optional[float] = None
    error: Optional[str] = None
    cancel_requested: bool = False
    finished_at: Optional[float] = None

    @property
    def active(self) -> bool:
        return self.status in {"queued", "running", "cancel_requested"}

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload.update(
            {
                "active": self.active,
                "case_count": len(self.cases),
                "completed_count": sum(
                    item.status in {"success", "failed"}
                    for item in self.cases
                ),
            }
        )
        return payload


@dataclass(frozen=True)
class DataQualitySettings:
    output_root: Path = ROOT / "quality_runs"
    model: str = "gpt-5.6-sol"
    effort: str = "medium"
    parallel: int = 2
    timeout_seconds: float = 1800.0
    retries: int = 1
    codex_cli: str = "codex"

    @classmethod
    def from_env(cls) -> "DataQualitySettings":
        return cls(
            output_root=Path(
                os.environ.get(
                    "OPENHARNESS_DATA_QUALITY_OUTPUT",
                    str(ROOT / "quality_runs"),
                )
            ),
            model=os.environ.get(
                "OPENHARNESS_DATA_QUALITY_MODEL",
                "gpt-5.6-sol",
            ),
            effort=os.environ.get(
                "OPENHARNESS_DATA_QUALITY_EFFORT",
                "medium",
            ),
            parallel=int(
                os.environ.get("OPENHARNESS_DATA_QUALITY_PARALLEL", "2")
            ),
            timeout_seconds=float(
                os.environ.get("OPENHARNESS_DATA_QUALITY_TIMEOUT", "1800")
            ),
            retries=int(
                os.environ.get("OPENHARNESS_DATA_QUALITY_RETRIES", "1")
            ),
            codex_cli=os.environ.get(
                "OPENHARNESS_DATA_QUALITY_CODEX_CLI",
                "codex",
            ),
        )

    def validate(self) -> None:
        if self.parallel < 1:
            raise DataQualityJobError("数据质检并发必须至少为 1")
        if self.timeout_seconds <= 0:
            raise DataQualityJobError("数据质检超时必须大于 0")
        if self.retries < 0:
            raise DataQualityJobError("数据质检重试次数不能小于 0")
        if not self.codex_cli.strip():
            raise DataQualityJobError("Codex CLI 路径不能为空")

    def public_dict(self) -> dict:
        return {
            "model": self.model,
            "effort": self.effort,
            "parallel": self.parallel,
            "output_root": str(self.output_root.expanduser().resolve()),
        }


class DataQualityJobService:
    def __init__(
        self,
        settings: Optional[DataQualitySettings] = None,
        runner_func: Optional[Callable] = None,
    ):
        self.settings = settings or DataQualitySettings.from_env()
        self.runner_func = runner_func or run_data_workflow
        self._lock = threading.RLock()
        self._jobs: Dict[str, DataQualityJob] = {}
        self._active_by_session: Dict[str, str] = {}
        self._cancel_events: Dict[str, threading.Event] = {}

    def configuration(self) -> dict:
        payload = self.settings.public_dict()
        try:
            self.settings.validate()
            payload.update({"ready": True, "error": None})
        except (DataQualityJobError, OSError, ValueError) as exc:
            payload.update({"ready": False, "error": str(exc)})
        return payload

    def get(self, job_id: str) -> DataQualityJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise DataQualityJobError("数据质检任务不存在: %s" % job_id)
            return job

    def latest_for_session(self, session_id: str) -> Optional[DataQualityJob]:
        with self._lock:
            jobs = [
                item
                for item in self._jobs.values()
                if item.session_id == session_id
            ]
            if jobs:
                return max(jobs, key=lambda item: item.created_at)
            restored = self._restore_completed_job(session_id)
            if restored is not None:
                self._jobs[restored.job_id] = restored
            return restored

    @staticmethod
    def _score_metrics(output_dir: str | Path) -> dict:
        audit_path = Path(output_dir) / "audit.json"
        if not audit_path.is_file():
            return {}
        try:
            return json.loads(
                audit_path.read_text(encoding="utf-8")
            )["projects"][0]["metrics"]
        except (
            OSError,
            KeyError,
            IndexError,
            TypeError,
            json.JSONDecodeError,
        ):
            return {}

    def _restore_completed_job(
        self,
        session_id: str,
    ) -> Optional[DataQualityJob]:
        session_root = (
            self.settings.output_root.expanduser().resolve() / session_id
        )
        summaries = sorted(
            session_root.glob("dq-*/summary.json"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if not summaries:
            return None
        summary_path = summaries[0]
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        cases = []
        dimension_scores = {
            "omission_score": [],
            "conflict_score": [],
            "signal_score": [],
        }
        repair_metadata = False
        for item in summary.get("cases") or []:
            metrics = self._score_metrics(item.get("output_dir") or "")
            case = DataQualityCaseState(
                case_id=str(item.get("case_id") or ""),
                status=str(item.get("status") or "failed"),
                overall_score=item.get("overall_score"),
                error=item.get("error"),
            )
            for name in dimension_scores:
                value = metrics.get(name)
                if value is not None:
                    value = float(value)
                    setattr(case, name, value)
                    dimension_scores[name].append(value)
            repair_metadata = repair_metadata or bool(
                item.get("repair_status")
            )
            cases.append(case)
        mtime = summary_path.stat().st_mtime
        scored = [
            item.overall_score
            for item in cases
            if item.overall_score is not None
        ]
        job = DataQualityJob(
            job_id=summary_path.parent.name,
            session_id=session_id,
            dataset_path="",
            output_root=str(summary_path.parent),
            model="",
            effort="",
            parallel=1,
            repair_metadata=repair_metadata,
            created_at=mtime,
            updated_at=mtime,
            finished_at=mtime,
            status=(
                "completed"
                if summary.get("status") == "success"
                else str(summary.get("status") or "failed")
            ),
            cases=cases,
            average_score=summary.get("average_case_score"),
            median_score=summary.get("median_case_score"),
        )
        if scored and job.average_score is None:
            job.average_score = round(sum(scored) / len(scored), 1)
        for name, values in dimension_scores.items():
            if values:
                setattr(
                    job,
                    "average_" + name,
                    round(sum(values) / len(values), 1),
                )
        return job

    def active_for_session(self, session_id: str) -> Optional[DataQualityJob]:
        with self._lock:
            job_id = self._active_by_session.get(session_id)
            return self._jobs.get(job_id) if job_id else None

    def start(
        self,
        *,
        session_id: str,
        dataset_path: Path,
        case_ids: list[str],
        repair_metadata: bool = False,
        parallel: Optional[int] = None,
    ) -> DataQualityJob:
        dataset = dataset_path.expanduser().resolve()
        if not dataset.is_file():
            raise DataQualityJobError("数据集不存在: %s" % dataset)
        if not case_ids:
            raise DataQualityJobError("尚未导入可质检的 case")
        selected_parallel = self.settings.parallel if parallel is None else int(parallel)
        if selected_parallel < 1:
            raise DataQualityJobError("数据质检并发必须至少为 1")
        with self._lock:
            active = self.active_for_session(session_id)
            if active is not None:
                return active
            now = time.time()
            job_id = "dq-" + uuid.uuid4().hex[:12]
            output = (
                self.settings.output_root.expanduser().resolve()
                / session_id
                / job_id
            )
            job = DataQualityJob(
                job_id=job_id,
                session_id=session_id,
                dataset_path=str(dataset),
                output_root=str(output),
                model=self.settings.model,
                effort=self.settings.effort,
                parallel=selected_parallel,
                repair_metadata=bool(repair_metadata),
                created_at=now,
                updated_at=now,
                cases=[
                    DataQualityCaseState(case_id=str(case_id))
                    for case_id in case_ids
                ],
            )
            cancel_event = threading.Event()
            self._jobs[job_id] = job
            self._active_by_session[session_id] = job_id
            self._cancel_events[job_id] = cancel_event
            thread = threading.Thread(
                target=self._run,
                args=(job, cancel_event),
                name=job_id,
                daemon=True,
            )
            thread.start()
            return job

    def cancel(self, job_id: str) -> DataQualityJob:
        with self._lock:
            job = self.get(job_id)
            if not job.active:
                return job
            job.cancel_requested = True
            job.status = "cancel_requested"
            job.updated_at = time.time()
            self._cancel_events[job_id].set()
            return job

    def _progress(self, job: DataQualityJob, event: dict) -> None:
        with self._lock:
            case_id = event.get("case_id")
            case = next(
                (item for item in job.cases if item.case_id == case_id),
                None,
            )
            if event["event"] == "case_started" and case is not None:
                case.status = "running"
            elif event["event"] == "stage_started" and case is not None:
                case.stage = event.get("stage")
            elif event["event"] == "stage_completed" and case is not None:
                case.overall_score = event.get(
                    "overall_score",
                    case.overall_score,
                )
            elif event["event"] == "case_completed" and case is not None:
                case.status = event.get("status") or "failed"
                case.overall_score = event.get(
                    "overall_score",
                    case.overall_score,
                )
                case.error = event.get("error")
            job.updated_at = time.time()

    def _run(self, job: DataQualityJob, cancel_event: threading.Event) -> None:
        with self._lock:
            job.status = "running"
            job.updated_at = time.time()
        stages = (
            ("metadata", "audit", "repair")
            if job.repair_metadata
            else ("metadata", "audit")
        )
        request = DataWorkflowRequest(
            output_root=Path(job.output_root),
            dataset=Path(job.dataset_path),
            case_ids=tuple(item.case_id for item in job.cases),
            stages=stages,
            model=job.model,
            effort=job.effort,
            parallel=job.parallel,
            timeout_seconds=self.settings.timeout_seconds,
            retries=self.settings.retries,
            codex_command=(self.settings.codex_cli,),
        )
        try:
            result = self.runner_func(
                request,
                progress_callback=lambda event: self._progress(job, event),
                should_cancel=cancel_event.is_set,
            )
            scores = [
                item.overall_score
                for item in result.cases
                if item.overall_score is not None
            ]
            dimension_scores = {
                "omission_score": [],
                "conflict_score": [],
                "signal_score": [],
            }
            with self._lock:
                by_id = {item.case_id: item for item in result.cases}
                for case in job.cases:
                    result_case = by_id.get(case.case_id)
                    if result_case is not None:
                        case.status = result_case.status
                        case.overall_score = result_case.overall_score
                        case.error = result_case.error
                        metrics = self._score_metrics(
                            result_case.output_dir
                        )
                        for name in dimension_scores:
                            value = metrics.get(name)
                            if value is not None:
                                value = float(value)
                                setattr(case, name, value)
                                dimension_scores[name].append(value)
                job.status = (
                    "completed"
                    if result.status == "success"
                    else result.status
                )
                if scores:
                    ordered = sorted(scores)
                    job.average_score = round(sum(scores) / len(scores), 1)
                    midpoint = len(ordered) // 2
                    job.median_score = (
                        ordered[midpoint]
                        if len(ordered) % 2
                        else round(
                            (ordered[midpoint - 1] + ordered[midpoint]) / 2,
                            1,
                        )
                    )
                for name, values in dimension_scores.items():
                    if values:
                        setattr(
                            job,
                            "average_" + name,
                            round(sum(values) / len(values), 1),
                        )
        except DataQualityCancelled as exc:
            with self._lock:
                job.status = "cancelled"
                job.error = str(exc)
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                job.status = "failed"
                job.error = str(exc)
        finally:
            with self._lock:
                job.finished_at = time.time()
                job.updated_at = job.finished_at
                self._active_by_session.pop(job.session_id, None)
