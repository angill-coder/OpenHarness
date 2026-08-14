# -*- coding: utf-8 -*-
"""前端一键真实报告生成任务编排。

HTTP 层只负责创建/查询任务；本模块在后台线程中调用 harness Runner，
接收 attempt 进度，并把通过验收的报告逐 case 导入 Session。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
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
import codex_runner as codex_runner_mod  # noqa: E402
from workbuddy_batch.adapter import (  # noqa: E402
    discover_command as discover_workbuddy_command,
)
from workbuddy_batch.dataset import load_cases  # noqa: E402

from generation_models import (  # noqa: E402
    ACTIVE_STATES,
    GenerationCaseState,
    GenerationJob,
)
from model_config import (  # noqa: E402
    DEFAULT_CODEX_REASONING_EFFORT,
    DEFAULT_GENERATION_CODEX_MODEL,
    DEFAULT_GENERATION_WB_MODEL,
    SUPPORTED_CODEX_MODELS,
    SUPPORTED_CODEX_REASONING_EFFORTS,
    SUPPORTED_WB_MODELS,
)
import persistence as persist  # noqa: E402
import iteration_trace  # noqa: E402


def _read_trace_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


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
    if len(text) <= limit:
        return text
    return text[:limit] + "\n... [truncated]"


def _generation_trace(case_result) -> Dict:
    attempts = list(getattr(case_result, "attempts", ()) or ())
    if not attempts:
        return {
            "status": "unavailable",
            "operations": [],
            "rounds": [],
            "conversation": [],
            "conversationAvailable": False,
        }
    attempt = attempts[-1]
    trace_dir = Path(attempt.trace_path)
    raw_operations = _read_trace_json(
        trace_dir / "1_operations.json",
        [],
    )
    operations = []
    for operation in raw_operations if isinstance(raw_operations, list) else []:
        if not isinstance(operation, dict):
            continue
        operations.append({
            "name": str(operation.get("name") or "tool"),
            "status": str(operation.get("status") or "unknown"),
            "round": operation.get("round_index"),
            "durationMs": operation.get("duration_ms"),
            "input": _trace_text(operation.get("input")),
            "result": _trace_text(operation.get("result")),
        })
    rounds = []
    rounds_root = trace_dir / "rounds"
    if rounds_root.is_dir():
        for result_path in sorted(rounds_root.glob("*/result.json")):
            item = _read_trace_json(result_path, {})
            if not isinstance(item, dict):
                continue
            rounds.append({
                "name": result_path.parent.name,
                "status": str(item.get("status") or "unknown"),
                "durationMs": item.get("duration_ms"),
                "output": _trace_text(item.get("final_output")),
            })
    model = (
        attempt.observed_models[0]
        if attempt.observed_models
        else attempt.configured_model
    )
    return {
        "status": attempt.wb_status or attempt.status,
        "model": model,
        "durationMs": attempt.duration_ms,
        "attempt": attempt.attempt,
        "sessionId": attempt.wb_session_id,
        "operations": operations,
        "rounds": rounds,
        "conversation": [],
        "conversationAvailable": (trace_dir.parent / "conversation.md").is_file(),
    }


from skill_compiler import (  # noqa: E402
    compile_session_skill,
    directory_hash as compiled_skill_hash,
)


class GenerationJobError(ValueError):
    """可直接返回给前端的任务配置或状态错误。"""


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
    dataset_paths: tuple[tuple[str, Path], ...] = ()
    skill_path: Optional[Path] = None
    skill_name: Optional[str] = None
    backend: str = "workbuddy"
    model: Optional[str] = DEFAULT_GENERATION_WB_MODEL
    models: tuple[str, ...] = SUPPORTED_WB_MODELS
    reasoning_effort: Optional[str] = None
    reasoning_efforts: tuple[str, ...] = ()
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
        backend = str(
            os.environ.get("OPENHARNESS_RUNNER_LLM_BACKEND")
            or os.environ.get("OPENHARNESS_RUNNER_BACKEND")
            or "workbuddy"
        ).strip().lower()
        backend = {
            "wb": "workbuddy",
            "workbuddy_cli": "workbuddy",
            "codex_cli": "codex",
        }.get(backend, backend)
        legacy_dataset = os.environ.get("OPENHARNESS_WB_DATASET")
        dataset_root = ROOT / "data" / "research-report"
        dataset_paths = tuple(
            (
                version,
                Path(
                    os.environ.get(
                        "OPENHARNESS_WB_DATASET_" + version.upper(),
                        legacy_dataset or str(dataset_root / version / "data.json"),
                    )
                ),
            )
            for version in ("v1", "v2", "v3")
        )
        dataset = dict(dataset_paths)["v1"]
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
            os.environ.get("OPENHARNESS_CODEX_CLI_PATH")
            if backend == "codex"
            else os.environ.get("OPENHARNESS_WB_CLI_PATH")
        ) or None
        if backend == "codex":
            default_model = DEFAULT_GENERATION_CODEX_MODEL
            selected_model = os.environ.get(
                "OPENHARNESS_RUNNER_CODEX_MODEL",
                default_model,
            )
            models = SUPPORTED_CODEX_MODELS
            reasoning_effort = os.environ.get(
                "OPENHARNESS_RUNNER_CODEX_REASONING_EFFORT",
                DEFAULT_CODEX_REASONING_EFFORT,
            )
            reasoning_efforts = SUPPORTED_CODEX_REASONING_EFFORTS
        else:
            selected_model = os.environ.get(
                "OPENHARNESS_WB_MODEL",
                DEFAULT_GENERATION_WB_MODEL,
            )
            models = SUPPORTED_WB_MODELS
            reasoning_effort = None
            reasoning_efforts = ()
        return cls(
            dataset_path=dataset,
            output_root=output,
            dataset_paths=dataset_paths,
            skill_path=skill_path,
            skill_name=skill_name,
            backend=backend,
            model=selected_model or None,
            models=models,
            reasoning_effort=reasoning_effort,
            reasoning_efforts=reasoning_efforts,
            parallel=_env_int(
                "OPENHARNESS_RUNNER_PARALLEL",
                _env_int("OPENHARNESS_WB_PARALLEL", 20),
            ),
            max_report_retries=_env_int(
                "OPENHARNESS_RUNNER_MAX_REPORT_RETRIES",
                _env_int("OPENHARNESS_WB_MAX_REPORT_RETRIES", 3),
            ),
            timeout_seconds=_env_float(
                "OPENHARNESS_RUNNER_TIMEOUT",
                _env_float("OPENHARNESS_WB_TIMEOUT", 900.0),
            ),
            stall_timeout_seconds=_env_float(
                "OPENHARNESS_RUNNER_STALL_TIMEOUT",
                (
                    900.0
                    if backend == "codex"
                    else _env_float("OPENHARNESS_WB_STALL_TIMEOUT", 180.0)
                ),
            ),
            max_concurrent_jobs=_env_int(
                "OPENHARNESS_RUNNER_MAX_CONCURRENT_JOBS",
                _env_int("OPENHARNESS_WB_MAX_CONCURRENT_JOBS", 1),
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

    def dataset_for_version(self, data_version: str) -> Path:
        normalized = str(data_version or "v1").strip().lower()
        match = re.search(r"v([123])", normalized)
        key = "v" + match.group(1) if match else "v1"
        return dict(self.dataset_paths).get(key, self.dataset_path)

    def validate(self) -> None:
        if self.backend not in {"workbuddy", "codex"}:
            raise GenerationJobError(
                "Runner 后端仅支持 workbuddy 或 codex"
            )
        configured = self.dataset_paths or (("v1", self.dataset_path),)
        for data_version, dataset_path in configured:
            if not dataset_path.expanduser().is_file():
                raise GenerationJobError(
                    "WB %s dataset missing: %s" % (data_version, dataset_path)
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
        if self.backend == "codex":
            if not self.reasoning_effort:
                raise GenerationJobError("Codex Runner 推理力度不能为空")
            if self.reasoning_effort not in self.reasoning_efforts:
                raise GenerationJobError(
                    "不支持的 Codex Runner 推理力度: %s"
                    % self.reasoning_effort
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
            "dataset_paths": {
                key: str(path.expanduser().resolve())
                for key, path in (self.dataset_paths or (("v1", self.dataset_path),))
            },
            "output_root": str(self.output_root.expanduser().resolve()),
            "skill_mode": "session_artifact",
            "skill_ref": "复制唯一基础 Skill，并写入当前版本 directive",
            "base_skill_ref": str(
                self.skill_path.expanduser().resolve()
                if self.skill_path
                else self.skill_name
            ),
            "backend": self.backend,
            "model": self.model,
            "models": list(self.models),
            "reasoning_effort": self.reasoning_effort,
            "reasoning_efforts": list(self.reasoning_efforts),
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
        self.runner_func = runner_func or (
            codex_runner_mod.run_external_cases
            if self.settings.backend == "codex"
            else runner_mod.run_external_cases
        )
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
            self._validate_runtime()
            payload.update({"ready": True, "error": None})
        except (GenerationJobError, OSError, ValueError) as exc:
            payload.update({"ready": False, "error": str(exc)})
        return payload

    def _validate_runtime(self) -> None:
        self.settings.validate()
        if not self._uses_real_runner:
            return
        try:
            command = self.settings.command or (
                codex_runner_mod.discover_command()
                if self.settings.backend == "codex"
                else discover_workbuddy_command()
            )
        except FileNotFoundError as exc:
            raise GenerationJobError(str(exc)) from exc
        executable = Path(command[0]).expanduser()
        if (
            (executable.is_absolute() or executable.parent != Path("."))
            and not executable.is_file()
        ):
            raise GenerationJobError(
                "%s CLI 不存在: %s"
                % (self.settings.backend, executable)
            )

    def dataset_path_for_session(self, session_id: str) -> Path:
        metadata = persist.load_meta(session_id) or {}
        marker = metadata.get("experiment_data") or metadata.get("data_version") or "v1"
        if isinstance(marker, dict):
            marker = marker.get("id") or marker.get("label") or "v1"
        match = re.search(r"v([123])", str(marker).lower())
        data_version = "v" + match.group(1) if match else "v1"
        return self.settings.dataset_for_version(data_version).expanduser().resolve()

    def _dataset_index(
        self,
        dataset_path: Optional[Path] = None,
    ) -> Dict[str, Dict[str, str]]:
        selected_dataset = (
            dataset_path or self.settings.dataset_path
        ).expanduser().resolve()
        try:
            cases = load_cases(selected_dataset)
        except Exception as exc:
            raise GenerationJobError(
                "WB dataset parse failed (%s): %s" % (selected_dataset, exc)
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

    def _write_session_dataset(
        self,
        session_id: str,
        version: str,
        session_cases: Dict[str, Dict],
        requested: Iterable[str],
    ) -> Path:
        """Snapshot inline Session inputs for cases absent from configured data."""
        rows = []
        for case_id in requested:
            source = session_cases[case_id]
            input_payload = source.get("input")
            if not isinstance(input_payload, dict) or not input_payload:
                raise GenerationJobError(
                    "Session case %s 缺少可供 Runner 使用的 input" % case_id
                )
            topic = str(
                source.get("topic") or input_payload.get("topic") or case_id
            )
            brief = str(input_payload.get("brief") or "").strip()
            prompt = (
                "请根据以下用户输入与其中的原始素材生成完整调研报告。"
                "只能使用这些材料，不得引入未提供的事实。\n\n"
                "主题：%s\n%s\n\n用户输入 JSON：\n%s"
                % (
                    topic,
                    ("任务要求：" + brief) if brief else "",
                    json.dumps(input_payload, ensure_ascii=False, indent=2),
                )
            )
            rows.append(
                {
                    "case_id": case_id,
                    "split": str(source.get("split") or "dev"),
                    "input": input_payload,
                    "turns": [
                        {"round": 0, "label": "task", "prompt": prompt}
                    ],
                    "metadata": {"source_kind": "session_inline"},
                }
            )
        payload = {
            "schema_version": "openharness-wb/v1",
            "defaults": {"skills": ["research-report"]},
            "cases": rows,
        }
        content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
        target_dir = (
            self.settings.output_root / "_session_datasets" / session_id
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / ("%s-%s.json" % (version, digest))
        if not target.exists():
            temporary = target.with_suffix(".tmp")
            temporary.write_text(content, encoding="utf-8")
            temporary.replace(target)
        return target.resolve()

    def _persist(self, job: GenerationJob) -> None:
        job.updated_at = time.time()
        payload = job.to_dict()
        persist.save_generation_job(
            job.session_id,
            job.job_id,
            payload,
        )
        session = self.sessions.get(job.session_id)
        if session is not None:
            iteration_trace.record_generation_job(session, payload)

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
        self._validate_runtime()
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

        # 先做版本数据集边界校验，再处理幂等/活跃任务复用，避免无效
        # case_ids 借复用分支绕过 API 校验。
        if case_ids is not None:
            case_ids = list(dict.fromkeys(str(item) for item in case_ids))
            if not case_ids:
                raise GenerationJobError("case_ids 不能为空")
            with self.session_lock(session_id):
                target = session._eval_target()
                eligible_case_ids = session._case_ids_for(target)
            out_of_scope = sorted(set(case_ids) - eligible_case_ids)
            if out_of_scope:
                raise GenerationJobError(
                    "case 不属于当前 Skill 版本绑定的数据集: "
                    + ", ".join(out_of_scope)
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

        dataset_path = self.dataset_path_for_session(session_id)
        dataset = self._dataset_index(dataset_path)
        with self.session_lock(session_id):
            if not session.cases:
                raise GenerationJobError(
                    "请先给 Session 导入评测数据"
                )
            target = session._eval_target()
            eligible_cases = session._cases_for(target)
            eligible_case_ids = session._case_ids_for(target)
            if not eligible_case_ids:
                raise GenerationJobError(
                    "当前 Skill 版本绑定的数据集没有有效 case"
                )
            session_case_rows = {
                str(item["case_id"]): item
                for item in eligible_cases
            }
            session_cases = {
                case_id: str(item.get("split") or "dev")
                for case_id, item in session_case_rows.items()
            }
            version = target["version"]
            skill = _skill_for_version(session, version)
            iteration = iteration_trace.ensure_iteration(
                session,
                version,
            )
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
        out_of_scope = sorted(set(requested) - eligible_case_ids)
        if out_of_scope:
            raise GenerationJobError(
                "case 不属于当前 Skill 版本绑定的数据集: "
                + ", ".join(out_of_scope)
            )
        missing_dataset = sorted(set(requested) - set(dataset))
        if missing_dataset:
            if set(missing_dataset) != set(requested):
                raise GenerationJobError(
                    "data.json 仅覆盖部分 Session case，缺少: "
                    + ", ".join(missing_dataset)
                )
            dataset_path = self._write_session_dataset(
                session_id,
                version,
                session_case_rows,
                requested,
            )
            dataset = self._dataset_index(dataset_path)

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
            dataset_path=str(dataset_path),
            skill_mode="session_artifact",
            skill_ref=str(frozen_skill.path),
            model=selected_model,
            parallel=selected_parallel,
            max_report_retries=self.settings.max_report_retries,
            timeout_seconds=self.settings.timeout_seconds,
            stall_timeout_seconds=self.settings.stall_timeout_seconds,
            created_at=now,
            updated_at=now,
            backend=self.settings.backend,
            reasoning_effort=self.settings.reasoning_effort,
            dataset_sha256=_file_hash(dataset_path),
            compiler_version=frozen_skill.compiler_version,
            base_skill_hash=frozen_skill.base_skill_hash,
            iteration_id=iteration.get("iteration_id"),
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
        session = self.sessions.get(previous.session_id)
        if session is None:
            raise GenerationJobError(
                "会话不存在: %s" % previous.session_id
            )
        with self.session_lock(previous.session_id):
            current_version = session._eval_target()["version"]
        if previous.skill_version != current_version:
            raise GenerationJobError(
                "只能重试当前 Skill 版本的任务；原任务版本为 %s，当前为 %s"
                % (previous.skill_version, current_version)
            )
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
            effort=job.reasoning_effort,
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
        case_results=None,
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
            traces = {
                item.openharness_case_id: _generation_trace(item)
                for item in (case_results or [])
                if item.openharness_case_id in reports
            }
            imported = session.import_generated_outputs(
                reports,
                job.skill_version,
                job.generation_id,
                account=job.account,
                traces=traces,
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

        self._import_reports(job, new_reports, result.cases)

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
                    self._import_reports(job, reports, result.cases)

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
