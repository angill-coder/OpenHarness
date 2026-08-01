# -*- coding: utf-8 -*-
"""前端一键真实报告生成任务编排。

HTTP 层只负责创建/查询任务；本模块在后台线程中调用 harness Runner，
接收 attempt 进度，并把通过验收的报告逐 case 导入 Session。
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARNESS = ROOT / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from external_run_models import (  # noqa: E402
    ExternalRunRequest,
    ReportOutputContract,
)
import runner as runner_mod  # noqa: E402
from workbuddy_batch.adapter import discover_command  # noqa: E402
from workbuddy_batch.dataset import load_cases  # noqa: E402

from generation_models import (  # noqa: E402
    ACTIVE_STATES,
    GenerationCaseState,
    GenerationJob,
)
import persistence as persist  # noqa: E402
from skill_compiler import (  # noqa: E402
    compile_session_skill,
    directory_hash as compiled_skill_hash,
)


class GenerationJobError(ValueError):
    """可直接返回给前端的任务配置或状态错误。"""


SUPPORTED_WB_MODELS = (
    "deepseek-v4-pro-ioa",
    "hy3-ioa",
    "deepseek-v4-flash-ioa",
    "claude-opus-4.8-1m",
    "claude-opus-4.8",
    "claude-opus-4.7-1m",
    "claude-opus-4.7",
    "claude-opus-4.6-1m",
    "claude-opus-4.6",
    "claude-sonnet-5-1m",
    "claude-sonnet-5",
    "claude-sonnet-4.6-1m",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gemini-3.5-flash",
    "glm-5.2-ioa",
    "glm-5v-turbo-ioa",
    "kimi-k3-ioa",
    "kimi-k2.7-ioa",
    "kimi-k2.6-ioa",
    "minimax-m3-ioa",
)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    return float(value) if value not in (None, "") else default


@dataclass(frozen=True)
class GenerationSettings:
    dataset_path: Path
    output_root: Path
    skill_path: Optional[Path] = None
    skill_name: Optional[str] = None
    model: Optional[str] = "deepseek-v4-pro-ioa"
    models: tuple[str, ...] = SUPPORTED_WB_MODELS
    parallel: int = 20
    max_report_retries: int = 3
    timeout_seconds: float = 900.0
    stall_timeout_seconds: float = 180.0
    max_concurrent_jobs: int = 1
    command: Optional[tuple[str, ...]] = None
    workbuddy_home: Optional[Path] = None
    product_config: Optional[Path] = None
    min_report_bytes: int = 500

    @classmethod
    def from_env(cls) -> "GenerationSettings":
        dataset = Path(
            os.environ.get(
                "OPENHARNESS_WB_DATASET",
                str(
                    ROOT
                    / "data"
                    / "20260727_test_data"
                    / "data.json"
                ),
            )
        )
        output = Path(
            os.environ.get(
                "OPENHARNESS_WB_OUTPUT",
                str(ROOT / "generation_runs"),
            )
        )
        skill_name = (
            os.environ.get("OPENHARNESS_WB_SKILL_NAME") or None
        )
        skill_path = None
        if not skill_name:
            skill_path = Path(
                os.environ.get(
                    "OPENHARNESS_WB_SKILL_PATH",
                    str(ROOT / "skills" / "research-report"),
                )
            )
        command_path = (
            os.environ.get("OPENHARNESS_WB_CLI_PATH") or None
        )
        return cls(
            dataset_path=dataset,
            output_root=output,
            skill_path=skill_path,
            skill_name=skill_name,
            model=os.environ.get(
                "OPENHARNESS_WB_MODEL",
                "deepseek-v4-pro-ioa",
            )
            or None,
            parallel=_env_int("OPENHARNESS_WB_PARALLEL", 20),
            max_report_retries=_env_int(
                "OPENHARNESS_WB_MAX_REPORT_RETRIES",
                3,
            ),
            timeout_seconds=_env_float(
                "OPENHARNESS_WB_TIMEOUT",
                900.0,
            ),
            stall_timeout_seconds=_env_float(
                "OPENHARNESS_WB_STALL_TIMEOUT",
                180.0,
            ),
            max_concurrent_jobs=_env_int(
                "OPENHARNESS_WB_MAX_CONCURRENT_JOBS",
                1,
            ),
            command=(command_path,) if command_path else None,
            workbuddy_home=(
                Path(os.environ["OPENHARNESS_WB_HOME"])
                if os.environ.get("OPENHARNESS_WB_HOME")
                else None
            ),
            product_config=(
                Path(os.environ["OPENHARNESS_WB_PRODUCT_CONFIG"])
                if os.environ.get("OPENHARNESS_WB_PRODUCT_CONFIG")
                else None
            ),
            min_report_bytes=_env_int(
                "OPENHARNESS_WB_MIN_REPORT_BYTES",
                500,
            ),
        )

    def validate(self, require_dataset: bool = True) -> None:
        if (
            require_dataset
            and not self.dataset_path.expanduser().is_file()
        ):
            raise GenerationJobError(
                "WB dataset 不存在: %s" % self.dataset_path
            )
        if self.skill_name or not self.skill_path:
            raise GenerationJobError(
                "Session Skill 版本演进必须配置唯一基础 skill_path"
            )
        path = self.skill_path.expanduser()
        if not path.is_dir():
            raise GenerationJobError(
                "基础 Skill 必须是目录: %s" % path
            )
        for relative in (
            Path("SKILL.md"),
            Path("references") / "instructions.md",
        ):
            if not (path / relative).is_file():
                raise GenerationJobError(
                    "基础 Skill 缺少 %s: %s" % (relative, path)
                )
        if self.parallel < 1:
            raise GenerationJobError("parallel 必须至少为 1")
        if not self.models:
            raise GenerationJobError("报告生成模型列表不能为空")
        if self.model and self.model not in self.models:
            raise GenerationJobError(
                "默认报告生成模型不在支持列表中: %s" % self.model
            )
        if self.max_report_retries < 0:
            raise GenerationJobError(
                "max_report_retries 不能小于 0"
            )
        if self.max_concurrent_jobs < 1:
            raise GenerationJobError(
                "max_concurrent_jobs 必须至少为 1"
            )

    def public_dict(self) -> Dict:
        return {
            "dataset_path": str(self.dataset_path.expanduser().resolve()),
            "output_root": str(self.output_root.expanduser().resolve()),
            "skill_mode": "session_artifact",
            "skill_ref": "复制唯一基础 Skill，并写入当前版本 directive",
            "base_skill_ref": str(
                self.skill_path.expanduser().resolve()
                if self.skill_path
                else self.skill_name
            ),
            "model": self.model,
            "models": list(self.models),
            "parallel": self.parallel,
            "max_report_retries": self.max_report_retries,
            "max_attempts": self.max_report_retries + 1,
            "timeout_seconds": self.timeout_seconds,
            "stall_timeout_seconds": self.stall_timeout_seconds,
            "max_concurrent_jobs": self.max_concurrent_jobs,
            "min_report_bytes": self.min_report_bytes,
            "versioned_skill": True,
        }


def _json_hash(value) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _directory_hash(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return None
    root = path.expanduser().resolve()
    files = [root] if root.is_file() else sorted(
        item
        for item in root.rglob("*")
        if item.is_file()
        and "__pycache__" not in item.parts
        and not item.name.startswith(".")
    )
    digest = hashlib.sha256()
    for item in files:
        relative = item.name if root.is_file() else str(
            item.relative_to(root)
        )
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.expanduser().resolve().open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _skill_for_version(session, version: str):
    matches = [
        item
        for item in session.versions
        if item["version"] == version
    ]
    if matches:
        return matches[-1]["skill"]
    raise GenerationJobError("Session 中不存在 Skill 版本: %s" % version)


class GenerationJobService:
    def __init__(
        self,
        sessions: Dict[str, object],
        settings: Optional[GenerationSettings] = None,
        runner_func: Optional[Callable] = None,
    ):
        self.sessions = sessions
        self.settings = settings or GenerationSettings.from_env()
        self._uses_real_runner = runner_func is None
        self.runner_func = runner_func or runner_mod.run_external_cases
        self._lock = threading.RLock()
        self._session_locks: Dict[str, threading.RLock] = {}
        self._jobs: Dict[str, GenerationJob] = {}
        self._active_by_session: Dict[str, str] = {}
        self._cancel_events: Dict[str, threading.Event] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._semaphore = threading.Semaphore(
            self.settings.max_concurrent_jobs
        )
        self._restore_jobs()

    def _restore_jobs(self) -> None:
        for payload in persist.load_generation_jobs():
            try:
                job = GenerationJob.from_dict(payload)
            except (KeyError, TypeError, ValueError):
                continue
            if job.status in ACTIVE_STATES:
                job.status = "interrupted"
                job.error = (
                    "服务重启中断了后台任务；可点击“仅重试失败 case”"
                )
                job.finished_at = time.time()
                job.updated_at = job.finished_at
                persist.save_generation_job(
                    job.session_id,
                    job.job_id,
                    job.to_dict(),
                )
            self._jobs[job.job_id] = job

    def session_lock(self, session_id: str) -> threading.RLock:
        with self._lock:
            return self._session_locks.setdefault(
                session_id,
                threading.RLock(),
            )

    def configuration(self) -> Dict:
        payload = self.settings.public_dict()
        try:
            self._validate_runtime(require_dataset=False)
            payload.update({"ready": True, "error": None})
        except (GenerationJobError, OSError, ValueError) as exc:
            payload.update({"ready": False, "error": str(exc)})
        return payload

    def _validate_runtime(self, require_dataset: bool = True) -> None:
        self.settings.validate(require_dataset=require_dataset)
        if not self._uses_real_runner:
            return
        try:
            command = self.settings.command or discover_command()
        except FileNotFoundError as exc:
            raise GenerationJobError(str(exc)) from exc
        executable = Path(command[0]).expanduser()
        if (
            (executable.is_absolute() or executable.parent != Path("."))
            and not executable.is_file()
        ):
            raise GenerationJobError(
                "WorkBuddy CLI 不存在: %s" % executable
            )

    def _dataset_index(
        self,
        dataset_path: Optional[Path] = None,
    ) -> Dict[str, Dict[str, str]]:
        selected_path = (
            dataset_path or self.settings.dataset_path
        ).expanduser().resolve()
        try:
            cases = load_cases(selected_path)
        except Exception as exc:
            raise GenerationJobError(
                "WB dataset 解析失败: %s" % exc
            ) from exc
        index = {}
        for case in cases:
            is_unified = (
                case.metadata.get("dataset_schema_version")
                == "openharness-wb/v1"
            )
            case_id = str(
                case.case_id
                if is_unified
                else (
                    case.metadata.get("openharness_case_id")
                    or case.case_id
                )
            )
            if case_id in index:
                raise GenerationJobError(
                    "WB dataset 存在重复 OpenHarness case: %s"
                    % case_id
                )
            index[case_id] = {
                "wb_case_id": case.case_id,
                "split": str(
                    case.metadata.get("split") or "dev"
                ),
            }
        return index

    def _persist(self, job: GenerationJob) -> None:
        job.updated_at = time.time()
        persist.save_generation_job(
            job.session_id,
            job.job_id,
            job.to_dict(),
        )

    def get(self, job_id: str) -> GenerationJob:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                raise GenerationJobError("生成任务不存在: %s" % job_id)
            return job

    def latest_for_session(
        self,
        session_id: str,
    ) -> Optional[GenerationJob]:
        with self._lock:
            jobs = [
                item
                for item in self._jobs.values()
                if item.session_id == session_id
            ]
            return max(
                jobs,
                key=lambda item: item.created_at,
                default=None,
            )

    def active_for_session(
        self,
        session_id: str,
    ) -> Optional[GenerationJob]:
        with self._lock:
            job_id = self._active_by_session.get(session_id)
            job = self._jobs.get(job_id) if job_id else None
            if job is not None and job.active:
                return job
            if job_id:
                self._active_by_session.pop(session_id, None)
            return None

    def list_for_session(
        self,
        session_id: str,
    ) -> list[GenerationJob]:
        with self._lock:
            return sorted(
                (
                    item
                    for item in self._jobs.values()
                    if item.session_id == session_id
                ),
                key=lambda item: item.created_at,
                reverse=True,
            )

    def start(
        self,
        session_id: str,
        account: str,
        case_ids: Optional[Iterable[str]] = None,
        parallel: Optional[int] = None,
        model: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        parent_job_id: Optional[str] = None,
    ) -> tuple[GenerationJob, bool]:
        self._validate_runtime(require_dataset=False)
        session = self.sessions.get(session_id)
        if session is None:
            raise GenerationJobError("会话不存在: %s" % session_id)
        if case_ids is not None and not isinstance(
            case_ids,
            (list, tuple, set),
        ):
            raise GenerationJobError("case_ids 必须是数组")
        if isinstance(parallel, bool) or (
            isinstance(parallel, float) and not parallel.is_integer()
        ):
            raise GenerationJobError("报告生成并发必须是整数")
        try:
            selected_parallel = int(
                self.settings.parallel if parallel is None else parallel
            )
        except (TypeError, ValueError) as exc:
            raise GenerationJobError("报告生成并发必须是整数") from exc
        if selected_parallel < 1:
            raise GenerationJobError("报告生成并发必须至少为 1")
        if model is not None and not isinstance(model, str):
            raise GenerationJobError("报告生成模型必须是字符串")
        selected_model = (
            self.settings.model
            if model is None
            else model.strip()
        )
        if not selected_model:
            raise GenerationJobError("报告生成模型不能为空")
        if selected_model not in self.settings.models:
            raise GenerationJobError(
                "不支持的报告生成模型: %s" % selected_model
            )

        with self._lock:
            if idempotency_key:
                for existing in self._jobs.values():
                    if (
                        existing.session_id == session_id
                        and existing.idempotency_key == idempotency_key
                    ):
                        return existing, True
            active_id = self._active_by_session.get(session_id)
            if active_id:
                active = self._jobs[active_id]
                if active.active:
                    return active, True
                self._active_by_session.pop(session_id, None)

        with self.session_lock(session_id):
            if not session.cases:
                raise GenerationJobError(
                    "请先给 Session 导入评测数据"
                )
            source = getattr(session, "data_source", {}) or {}
            source_path = source.get("dataset_path")
            selected_dataset_path = (
                Path(source_path).expanduser().resolve()
                if (
                    source.get("kind") in {"configured", "uploaded"}
                    and source_path
                )
                else self.settings.dataset_path.expanduser().resolve()
            )
            if not selected_dataset_path.is_file():
                raise GenerationJobError(
                    "Session 数据集不存在: %s" % selected_dataset_path
                )
            dataset = self._dataset_index(selected_dataset_path)
            session_cases = {
                str(item["case_id"]): str(
                    item.get("split") or "dev"
                )
                for item in session.cases
            }
            version = session._eval_target()["version"]
            skill = _skill_for_version(session, version)
            try:
                frozen_skill = compile_session_skill(
                    self.settings.output_root,
                    session_id,
                    skill,
                    self.settings.skill_path,
                )
            except (OSError, ValueError) as exc:
                raise GenerationJobError(
                    "Session Skill 编译失败: %s" % exc
                ) from exc
            skill_artifact_hash = frozen_skill.artifact_hash
        requested = list(
            dict.fromkeys(
                str(item)
                for item in (
                    case_ids
                    if case_ids is not None
                    else session_cases.keys()
                )
            )
        )
        if not requested:
            raise GenerationJobError("没有要执行的 case")
        unknown_session = sorted(set(requested) - set(session_cases))
        if unknown_session:
            raise GenerationJobError(
                "case 不属于当前 Session: "
                + ", ".join(unknown_session)
            )
        missing_dataset = sorted(set(requested) - set(dataset))
        if missing_dataset:
            raise GenerationJobError(
                "data.json 缺少这些 Session case 的映射: "
                + ", ".join(missing_dataset)
            )

        now = time.time()
        job_id = "job-%s-%s" % (
            time.strftime("%Y%m%dT%H%M%S"),
            uuid.uuid4().hex[:8],
        )
        job = GenerationJob(
            job_id=job_id,
            session_id=session_id,
            account=account,
            skill_version=version,
            skill_artifact_hash=skill_artifact_hash,
            execution_skill_hash=frozen_skill.directory_hash,
            dataset_path=str(selected_dataset_path),
            skill_mode="session_artifact",
            skill_ref=str(frozen_skill.path),
            model=selected_model,
            parallel=selected_parallel,
            max_report_retries=self.settings.max_report_retries,
            timeout_seconds=self.settings.timeout_seconds,
            stall_timeout_seconds=self.settings.stall_timeout_seconds,
            created_at=now,
            updated_at=now,
            dataset_sha256=_file_hash(selected_dataset_path),
            compiler_version=frozen_skill.compiler_version,
            base_skill_hash=frozen_skill.base_skill_hash,
            cases=[
                GenerationCaseState(
                    case_id=case_id,
                    split=dataset[case_id]["split"],
                )
                for case_id in requested
            ],
            idempotency_key=idempotency_key,
            parent_job_id=parent_job_id,
        )
        cancel_event = threading.Event()
        thread = threading.Thread(
            target=self._run,
            args=(job_id,),
            name="generation-" + job_id,
            daemon=True,
        )
        with self._lock:
            if idempotency_key:
                for existing in self._jobs.values():
                    if (
                        existing.session_id == session_id
                        and existing.idempotency_key
                        == idempotency_key
                    ):
                        return existing, True
            active_id = self._active_by_session.get(session_id)
            if active_id:
                active = self._jobs[active_id]
                if active.active:
                    return active, True
                self._active_by_session.pop(session_id, None)
            self._jobs[job_id] = job
            self._active_by_session[session_id] = job_id
            self._cancel_events[job_id] = cancel_event
            self._threads[job_id] = thread
            self._persist(job)
        thread.start()
        return job, False

    def retry(
        self,
        job_id: str,
        account: str,
        parallel: Optional[int] = None,
        model: Optional[str] = None,
        idempotency_key: Optional[str] = None,
    ) -> tuple[GenerationJob, bool]:
        previous = self.get(job_id)
        if previous.active:
            raise GenerationJobError("任务仍在执行，不能创建重试")
        failed_ids = previous.failed_case_ids
        if not failed_ids:
            raise GenerationJobError("该任务没有失败 case")
        return self.start(
            previous.session_id,
            account,
            case_ids=failed_ids,
            parallel=(
                previous.parallel
                if parallel is None
                else parallel
            ),
            model=previous.model if model is None else model,
            idempotency_key=idempotency_key,
            parent_job_id=previous.job_id,
        )

    def cancel(self, job_id: str) -> GenerationJob:
        with self._lock:
            job = self.get(job_id)
            if not job.active:
                return job
            job.cancel_requested = True
            job.status = "cancel_requested"
            event = self._cancel_events.get(job_id)
            if event:
                event.set()
            self._persist(job)
            return job

    def _request_for(self, job: GenerationJob) -> ExternalRunRequest:
        if job.skill_mode != "session_artifact":
            raise GenerationJobError(
                "生成任务未冻结 Session Skill，拒绝执行"
            )
        return ExternalRunRequest(
            case_file=Path(job.dataset_path),
            output_root=self.settings.output_root,
            skill_version=job.skill_version,
            session_id=job.session_id,
            skill_name=None,
            skill_path=Path(job.skill_ref),
            model=job.model,
            parallel=job.parallel,
            timeout_seconds=job.timeout_seconds,
            stall_timeout_seconds=job.stall_timeout_seconds,
            max_report_retries=job.max_report_retries,
            output_contract=ReportOutputContract(
                min_bytes=self.settings.min_report_bytes,
            ),
            command=self.settings.command,
            workbuddy_home=self.settings.workbuddy_home,
            product_config=self.settings.product_config,
            allowed_material_roots=(
                Path(job.dataset_path).expanduser().resolve().parent,
            ),
            openharness_case_ids=tuple(
                item.case_id for item in job.cases
            ),
        )

    def _import_reports(
        self,
        job: GenerationJob,
        reports: Dict[str, str],
    ) -> None:
        """校验冻结输入，并把本次新产出的报告立即导入 Session。"""
        if not reports:
            return
        if not job.generation_id:
            raise GenerationJobError("生成任务缺少 generation_id")
        session = self.sessions.get(job.session_id)
        if session is None:
            raise GenerationJobError(
                "报告已生成，但 Session 已不存在"
            )
        if (
            job.dataset_sha256 is not None
            and _file_hash(Path(job.dataset_path))
            != job.dataset_sha256
        ):
            raise GenerationJobError(
                "数据集在任务期间发生变化，拒绝自动导入"
            )
        if (
            job.execution_skill_hash is not None
            and compiled_skill_hash(Path(job.skill_ref))
            != job.execution_skill_hash
        ):
            raise GenerationJobError(
                "执行 Skill 文件在任务期间发生变化，拒绝自动导入"
            )
        with self.session_lock(job.session_id):
            skill = _skill_for_version(
                session,
                job.skill_version,
            )
            if _json_hash(skill.to_dict()) != job.skill_artifact_hash:
                raise GenerationJobError(
                    "Skill 版本内容在任务期间发生变化，拒绝自动导入"
                )
            imported = session.import_generated_outputs(
                reports,
                job.skill_version,
                job.generation_id,
                account=job.account,
            )
            if "error" in imported:
                raise GenerationJobError(imported["error"])

    def _on_progress(self, job_id: str, result) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.generation_id = result.generation_id
            job.output_dir = result.output_dir
            by_case = {item.case_id: item for item in job.cases}
            new_reports = {}
            for external in result.cases:
                case = by_case.get(external.openharness_case_id)
                if case is None:
                    continue
                if not case.imported:
                    case.status = external.status
                case.attempts = len(external.attempts)
                last = external.attempts[-1] if external.attempts else None
                case.error = last.error if last else None
                if external.report:
                    case.report_sha256 = external.report.sha256
                    case.report_size = external.report.size
                    if not case.imported:
                        new_reports[case.case_id] = external.report.text
            if not job.cancel_requested:
                job.status = (
                    "retrying"
                    if any(item.status == "retrying" for item in job.cases)
                    else "running"
                )
            self._persist(job)

        self._import_reports(job, new_reports)

        if new_reports:
            with self._lock:
                for case in job.cases:
                    if case.case_id in new_reports:
                        case.imported = True
                        case.status = "imported"
                self._persist(job)

    def _run(self, job_id: str) -> None:
        job = self.get(job_id)
        cancel_event = self._cancel_events[job_id]
        try:
            with self._semaphore:
                if cancel_event.is_set():
                    with self._lock:
                        job.status = "cancelled"
                        job.finished_at = time.time()
                        self._persist(job)
                    return
                with self._lock:
                    job.status = "running"
                    job.started_at = time.time()
                    self._persist(job)
                if (
                    job.dataset_sha256 is not None
                    and _file_hash(Path(job.dataset_path))
                    != job.dataset_sha256
                ):
                    raise GenerationJobError(
                        "数据集在任务启动后发生变化，拒绝执行"
                    )

                result = self.runner_func(
                    self._request_for(job),
                    progress_callback=lambda update: self._on_progress(
                        job_id,
                        update,
                    ),
                    should_cancel=cancel_event.is_set,
                )
                reports = {
                    item.openharness_case_id: item.report.text
                    for item in result.cases
                    if item.report is not None
                    and not any(
                        case.case_id == item.openharness_case_id
                        and case.imported
                        for case in job.cases
                    )
                }
                if reports:
                    with self._lock:
                        job.status = "importing"
                        self._persist(job)
                    self._import_reports(job, reports)

                with self._lock:
                    generated_ids = {
                        item.openharness_case_id
                        for item in result.cases
                        if item.report is not None
                    }
                    for item in job.cases:
                        if item.case_id in generated_ids:
                            item.imported = True
                            item.status = "imported"
                    if job.imported_count == len(job.cases):
                        job.status = "completed"
                    elif job.cancel_requested:
                        job.status = "cancelled"
                    elif job.imported_count:
                        job.status = "partial"
                    else:
                        job.status = "failed"
                        job.error = "所有 case 均未产出有效报告"
                    job.finished_at = time.time()
                    self._persist(job)
        except Exception as exc:
            with self._lock:
                job.status = (
                    "cancelled"
                    if job.cancel_requested
                    else "failed"
                )
                job.error = str(exc)
                job.finished_at = time.time()
                self._persist(job)
        finally:
            with self._lock:
                if self._active_by_session.get(job.session_id) == job_id:
                    self._active_by_session.pop(job.session_id, None)

    def wait(self, job_id: str, timeout: float = 10.0) -> GenerationJob:
        """测试/本地脚本辅助；HTTP 请求不调用。"""
        thread = self._threads.get(job_id)
        if thread:
            thread.join(timeout)
        return self.get(job_id)
