"""Headless Report Loop runtime for host-Agent writing and isolated judging."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .judge_batch import (
    DEFAULT_DIMENSION_PARALLELISM,
    DEFAULT_JUDGE_MAX_RETRIES,
    JUDGE_STRATEGY_PER_DIMENSION,
    judge_report,
)
from .judge_prompt import build_judge_prompt
from .judge_provider import PROVIDER_WORKBUDDY, call_judge, resolve_settings
from .memory_rubric_provider import MemoryRubricProvider
from .report_failure import failure_report_from_checks
from .report_loop_gate import evaluate_candidate_gate
from .report_scoring import normalize_check_scores, score_labeled_check_judgment
from .rubric_compiler import compile_rubric
from .rubric_resolution import failed_resolution_plan, resolve_memory_rubrics
from .workbuddy_cli import extract_json


_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]+$")
_MAX_ARTIFACT_BYTES = 800_000
_MAX_STRUCTURED_DATA_BYTES = 5_000_000


class ReportLoopError(RuntimeError):
    """Invalid loop state, artifact, or Judge result."""


def _now() -> float:
    return round(time.time(), 3)


def _sha_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ReportLoopRuntime:
    def __init__(
        self,
        data_dir: Path | None = None,
        rubric_path: Path | None = None,
        judge_call: Callable[[str], str] | None = None,
        memory_provider: MemoryRubricProvider | None = None,
        judge_provider: str | None = None,
        judge_model: str | None = None,
        judge_effort: str | None = None,
        judge_fallback_model: str | None = None,
        judge_fallback_effort: str | None = None,
    ) -> None:
        plugin_root = Path(__file__).resolve().parents[3]
        configured = os.environ.get("RESEARCH_REPORT_LOOP_DIR", "~/.research-report-loop")
        self.data_dir = Path(data_dir or Path(configured).expanduser()).resolve()
        self.runs_dir = self.data_dir / "runs"
        self.rubric_path = Path(
            rubric_path or plugin_root / "rubrics" / "v2_rubric_research.json"
        ).resolve()
        memory_dir = os.environ.get("RESEARCH_REPORT_MEMORY_V2_0821_DIR")
        self.memory_provider = memory_provider or (
            MemoryRubricProvider(Path(memory_dir).expanduser())
            if memory_dir
            else None
        )
        self.judge_settings = resolve_settings(
            provider=judge_provider,
            model=judge_model,
            effort=judge_effort,
        )
        self.judge_fallback_settings = (
            resolve_settings(
                provider=PROVIDER_WORKBUDDY,
                model=judge_fallback_model,
                effort=judge_fallback_effort,
            )
            if judge_fallback_model
            else None
        )
        self._judge_fallback_active = threading.Event()
        self._judge_fallback_reason: str | None = None
        self._custom_judge_call = judge_call
        self.judge_call = judge_call or (
            lambda prompt: call_judge(prompt, settings=self.judge_settings)
        )
        self._lock = threading.RLock()
        self.runs_dir.mkdir(parents=True, exist_ok=True, mode=0o700)

    def _run_dir(self, run_id: str) -> Path:
        value = str(run_id or "").strip()
        if not _SAFE_ID.fullmatch(value):
            raise ReportLoopError("非法 Report Loop runId")
        target = (self.runs_dir / value).resolve()
        target.relative_to(self.runs_dir)
        return target

    def run_directory(self, run_id: str) -> Path:
        """Return the validated directory for a Report Loop run."""
        return self._run_dir(run_id)

    def deadline_at(self, run_id: str) -> float:
        """Return the immutable wall-clock deadline for a run."""
        with self._lock:
            return float(self._load(run_id)["deadlineAt"])

    @staticmethod
    def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)

    def _save(self, run: dict[str, Any]) -> None:
        run["updatedAt"] = _now()
        self._atomic_json(self._run_dir(run["id"]) / "state.json", run)

    def _event(self, run_id: str, event: str, payload: dict[str, Any]) -> None:
        path = self._run_dir(run_id) / "events.jsonl"
        record = {"ts": _now(), "type": event, "payload": payload}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _load(self, run_id: str) -> dict[str, Any]:
        path = self._run_dir(run_id) / "state.json"
        if not path.is_file():
            raise ReportLoopError(f"Report Loop 不存在: {run_id}")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _read_artifact(value: str, *, minimum_bytes: int) -> tuple[Path, str]:
        path = Path(str(value or "")).expanduser().resolve()
        if not path.is_file():
            raise ReportLoopError(f"报告文件不存在: {path}")
        if path.suffix.lower() not in {".md", ".txt"}:
            raise ReportLoopError("Report Loop MVP 只接受 Markdown 或纯文本报告")
        size = path.stat().st_size
        if size > _MAX_ARTIFACT_BYTES:
            raise ReportLoopError("报告文件超过 800 KB")
        text = path.read_text(encoding="utf-8-sig").strip()
        if len(text.encode("utf-8")) < minimum_bytes:
            raise ReportLoopError(f"报告内容不足 {minimum_bytes} bytes")
        return path, text

    @staticmethod
    def _load_structured_data(value: str | None) -> Any:
        if not value:
            return None
        path = Path(value).expanduser().resolve()
        if not path.is_file():
            raise ReportLoopError(f"Structured Data 不存在: {path}")
        if path.stat().st_size > _MAX_STRUCTURED_DATA_BYTES:
            raise ReportLoopError("Structured Data 超过 5 MB")
        try:
            return json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError as exc:
            raise ReportLoopError(f"Structured Data 不是有效 JSON: {exc}") from exc

    def start(
        self,
        *,
        task: str = "",
        originalUserQuery: str | None = None,
        intakeContext: dict[str, Any] | None = None,
        writerModel: dict[str, Any] | None = None,
        audience: str = "",
        project: str = "",
        artifactPath: str | None = None,
        structuredDataPath: str | None = None,
        targetScore: float = 5.0,
        maxJudgedVersions: int | None = None,
        maxElapsedSeconds: int = 3600,
        skillVersion: str = "research-report-loop/1.0.0-mvp.35",
    ) -> dict[str, Any]:
        original_query = str(originalUserQuery or task or "").strip()
        if not original_query:
            raise ReportLoopError("originalUserQuery 不能为空")
        intake = copy.deepcopy(intakeContext or {})
        if intakeContext is not None:
            if not isinstance(intakeContext, dict):
                raise ReportLoopError("intakeContext 必须是对象")
            for field in ("reportBackground", "materialHypothesis"):
                item = intake.get(field)
                if not isinstance(item, dict) or not str(item.get("value") or "").strip():
                    raise ReportLoopError(f"intakeContext.{field}.value 不能为空")
            materials = intake.get("priorityMaterials")
            if not isinstance(materials, list) or not materials:
                raise ReportLoopError("intakeContext.priorityMaterials 至少包含一个素材文件或目录")
        task = str(task or "").strip() or original_query
        target = float(targetScore)
        elapsed = int(maxElapsedSeconds)
        if target != 5.0:
            raise ReportLoopError("Report Loop 目标分固定为 5.0")
        if maxJudgedVersions is not None and int(maxJudgedVersions) < 1:
            raise ReportLoopError("maxJudgedVersions 必须为正整数或 null")
        if elapsed != 3600:
            raise ReportLoopError("Report Loop 最长运行时间固定为 3600 秒")
        if artifactPath:
            self._read_artifact(artifactPath, minimum_bytes=1)
        structured_data = self._load_structured_data(structuredDataPath)
        if not self.rubric_path.is_file():
            raise ReportLoopError(f"基础 Rubrics 不存在: {self.rubric_path}")
        base_rubric_raw = self.rubric_path.read_text(encoding="utf-8-sig")
        base_rubric = json.loads(base_rubric_raw)
        if not base_rubric.get("dimensions"):
            raise ReportLoopError("基础 Rubrics 没有 dimensions")
        memory_snapshot = (
            self.memory_provider.load(
                audience=str(audience or "").strip(),
                project=str(project or "").strip(),
            )
            if self.memory_provider is not None
            else {
                "status": "disabled", "revision": None, "rubricSetVersion": None,
                "documents": [], "items": [], "warnings": [],
            }
        )
        try:
            resolution_plan = resolve_memory_rubrics(
                base_rubric,
                memory_snapshot,
                task=task,
                audience=str(audience or "").strip(),
                project=str(project or "").strip(),
                call_model=self.judge_call,
                extract_json=extract_json,
                load_sources=(
                    self.memory_provider.load_sources
                    if self.memory_provider is not None
                    else None
                ),
            )
        except Exception as exc:
            resolution_plan = failed_resolution_plan(memory_snapshot, str(exc))
        rubric, compile_metadata = compile_rubric(
            base_rubric,
            memory_snapshot=memory_snapshot,
            resolution_plan=resolution_plan,
        )
        rubric_raw = json.dumps(rubric, ensure_ascii=False, indent=2) + "\n"
        judge_parallelism = max(
            DEFAULT_DIMENSION_PARALLELISM,
            len([dimension for dimension in rubric.get("dimensions", []) if dimension.get("checks")]),
        )
        run_id = "report-" + uuid.uuid4().hex[:12]
        directory = self._run_dir(run_id)
        with self._lock:
            directory.mkdir(parents=True, exist_ok=False, mode=0o700)
            self._atomic_json(directory / "base_rubric.json", base_rubric)
            self._atomic_json(directory / "memory_rubrics.json", memory_snapshot)
            self._atomic_json(directory / "rubric_resolution_plan.json", resolution_plan)
            frozen_rubric = directory / "compiled_rubric.json"
            frozen_rubric.write_text(rubric_raw, encoding="utf-8")
            if structured_data is not None:
                self._atomic_json(directory / "structured_data.json", structured_data)
            created = _now()
            run = {
                "schemaVersion": 2,
                "id": run_id,
                "task": task,
                "originalUserQuery": original_query,
                "intakeContext": intake,
                "writerModel": copy.deepcopy(writerModel or {}),
                "audience": str(audience or "").strip(),
                "project": str(project or "").strip(),
                "initialArtifactPath": str(Path(artifactPath).expanduser().resolve()) if artifactPath else None,
                "structuredDataPath": "structured_data.json" if structured_data is not None else None,
                "skillVersion": str(skillVersion or ""),
                "rubricVersion": rubric.get("version"),
                "baseRubricVersion": compile_metadata.get("baseRubricVersion"),
                "rubricSetVersion": compile_metadata.get("rubricSetVersion"),
                "rubricResolverHash": compile_metadata.get("resolverHash"),
                "resolutionStatus": compile_metadata.get("resolutionStatus"),
                "resolutionPlanHash": compile_metadata.get("resolutionPlanHash"),
                "resolutionPromptVersion": compile_metadata.get("resolutionPromptVersion"),
                "rubricSha256": hashlib.sha256(rubric_raw.encode("utf-8")).hexdigest(),
                "memoryRevision": memory_snapshot.get("revision"),
                "memoryRubricIds": [
                    item.get("id") for item in memory_snapshot.get("items") or []
                ],
                "appliedMemoryRubricIds": compile_metadata.get("appliedMemoryRubricIds") or [],
                "rubricCompile": compile_metadata,
                "judgeProvider": self.judge_settings.provider,
                "judgeModel": self.judge_settings.model,
                "judgeEffort": self.judge_settings.effort,
                "judgeFallbackProvider": (
                    self.judge_fallback_settings.provider
                    if self.judge_fallback_settings else None
                ),
                "judgeFallbackModel": (
                    self.judge_fallback_settings.model
                    if self.judge_fallback_settings else None
                ),
                "judgeFallbackEffort": (
                    self.judge_fallback_settings.effort
                    if self.judge_fallback_settings else None
                ),
                "judgeStrategy": JUDGE_STRATEGY_PER_DIMENSION,
                "judgeParallelism": judge_parallelism,
                "stopPolicy": {
                    "targetScore": 5.0,
                    "maxJudgedVersions": None,
                    "maxNoImprovement": 2,
                    "maxElapsedSeconds": elapsed,
                },
                "stopState": {"stopped": False, "code": None, "reason": None},
                "deadlineAt": created + elapsed,
                "status": "started",
                "currentVersion": None,
                "noImprovementStreak": 0,
                "revisions": [],
                "createdAt": created,
                "updatedAt": created,
            }
            self._save(run)
            self._event(run_id, "loop.started", {
                "rubricSha256": run["rubricSha256"],
                "judgeProvider": run["judgeProvider"],
                "judgeModel": run["judgeModel"],
                "judgeEffort": run["judgeEffort"],
                "judgeFallbackProvider": run["judgeFallbackProvider"],
                "judgeFallbackModel": run["judgeFallbackModel"],
                "judgeParallelism": run["judgeParallelism"],
                "maxJudgedVersions": None,
                "memoryRevision": run["memoryRevision"],
                "memoryRubricIds": run["memoryRubricIds"],
                "resolutionStatus": run["resolutionStatus"],
                "resolutionPlanHash": run["resolutionPlanHash"],
            })
        return {
            "status": "started",
            "runId": run_id,
            "rubricRevision": run["rubricSha256"],
            "judgeProvider": run["judgeProvider"],
            "judgeModel": run["judgeModel"],
            "judgeEffort": run["judgeEffort"],
            "judgeFallbackProvider": run["judgeFallbackProvider"],
            "judgeFallbackModel": run["judgeFallbackModel"],
            "judgeStrategy": run["judgeStrategy"],
            "judgeParallelism": run["judgeParallelism"],
            "memoryRevision": run["memoryRevision"],
            "memoryRubricIds": run["memoryRubricIds"],
            "rubricSetVersion": run["rubricSetVersion"],
            "rubricResolverHash": run["rubricResolverHash"],
            "resolutionStatus": run["resolutionStatus"],
            "resolutionPlanHash": run["resolutionPlanHash"],
            "appliedMemoryRubricIds": run["appliedMemoryRubricIds"],
            "nextAction": "submit",
        }

    @staticmethod
    def _revision(run: dict[str, Any], version: str) -> dict[str, Any]:
        item = next(
            (value for value in run.get("revisions", []) if value.get("version") == version),
            None,
        )
        if item is None:
            raise ReportLoopError(f"报告版本不存在: {version}")
        return item

    @staticmethod
    def _score_view(judgment: dict[str, Any]) -> dict[str, float]:
        result = dict(judgment.get("dimensions") or {})
        result["overall"] = float(judgment.get("overall") or 0)
        result["red_line_fails"] = len(judgment.get("redlineChecks") or [])
        return result

    @staticmethod
    def _target_dimensions(parent: dict[str, Any], rubric: dict[str, Any]) -> list[str]:
        targets = [
            str(item.get("dimension"))
            for item in (parent.get("failureReport") or {}).get("dimensions") or []
            if item.get("dimension")
        ]
        return targets or [
            str(item.get("name"))
            for item in rubric.get("dimensions", [])
            if item.get("name")
        ]

    @staticmethod
    def _drop_tolerance(rubric: dict[str, Any]) -> float:
        gate = next(
            (item for item in rubric.get("gates", []) if item.get("id") == "no_regression"),
            {},
        )
        return float(gate.get("drop_tolerance", 0.15))

    def _stop(self, run: dict[str, Any]) -> None:
        best = self._revision(run, run["currentVersion"])
        judgment = best["judgment"]
        policy = run["stopPolicy"]
        reason: tuple[str, str] | None = None
        if (
            float(judgment.get("overall") or 0) >= float(policy["targetScore"])
            and not judgment.get("redlineChecks")
            and not judgment.get("hardFloorFailures")
        ):
            reason = ("target_reached", "总分达到 5.0，且没有红线或硬门槛失败")
        elif int(run.get("noImprovementStreak") or 0) >= int(policy["maxNoImprovement"]):
            reason = ("no_improvement", "连续两个候选版本未被采纳")
        elif time.time() >= float(run["deadlineAt"]):
            reason = ("time_budget_exhausted", "Report Loop 已达到最长运行时间")
        run["stopState"] = {
            "stopped": reason is not None,
            "code": reason[0] if reason else None,
            "reason": reason[1] if reason else None,
        }
        run["status"] = "completed" if reason else "judged"

    @staticmethod
    def _revision_brief(
        run: dict[str, Any],
        rubric: dict[str, Any],
        candidate: dict[str, Any],
    ) -> dict[str, Any]:
        check_rank = {"met": 1.0, "partial": 0.5, "miss": 0.0, 1: 1.0, 1.0: 1.0, 0.5: 0.5, 0: 0.0, 0.0: 0.0}
        best = next(
            item for item in run["revisions"] if item["version"] == run["currentVersion"]
        )
        checks = (best.get("judgment") or {}).get("checks") or {}
        reasoning = (best.get("judgment") or {}).get("reasoning") or {}
        repair: list[dict[str, Any]] = []
        preserve: list[dict[str, Any]] = []
        user_overrides: list[dict[str, Any]] = []
        definitions: dict[str, dict[str, Any]] = {}
        for dimension in rubric.get("dimensions", []):
            for check in dimension.get("checks", []):
                check_id = str(check["id"])
                definition = {
                    "checkId": check_id,
                    "dimension": dimension.get("name"),
                    "label": check.get("label", check_id),
                    "requirement": check.get("desc", ""),
                }
                definitions[check_id] = definition
                if checks.get(check_id) in {"met", 1, 1.0}:
                    reason = str(reasoning.get(check_id) or "")
                    if re.match(r"^\s*user_override\s*[:：]", reason, re.I):
                        user_overrides.append({
                            "checkId": check_id,
                            "dimension": definition["dimension"],
                            "label": definition["label"],
                            "reason": reason,
                        })
                    else:
                        preserve.append({
                            "checkId": check_id,
                            "dimension": definition["dimension"],
                            "label": definition["label"],
                            "requirement": definition["requirement"],
                        })
                else:
                    repair.append({
                        **definition,
                        "status": checks.get(check_id),
                        "reason": str(reasoning.get(check_id) or ""),
                    })
        avoid: list[dict[str, Any]] = []
        if candidate.get("decision") == "rejected" and candidate.get("parentVersion"):
            parent = next(
                item for item in run["revisions"] if item["version"] == candidate["parentVersion"]
            )
            parent_checks = (parent.get("judgment") or {}).get("checks") or {}
            candidate_checks = (candidate.get("judgment") or {}).get("checks") or {}
            candidate_reasoning = (candidate.get("judgment") or {}).get("reasoning") or {}
            for check_id, prior in parent_checks.items():
                prior_rank = check_rank.get(prior)
                candidate_rank = check_rank.get(candidate_checks.get(check_id))
                if prior_rank is None or candidate_rank is None or candidate_rank >= prior_rank:
                    continue
                avoid.append({
                    **definitions.get(str(check_id), {"checkId": str(check_id)}),
                    "rejectedVersion": candidate["version"],
                    "reason": str(candidate_reasoning.get(check_id) or "候选版本引入回退"),
                })
        return {
            "baseVersion": run["currentVersion"],
            "repair": repair,
            "preserve": preserve,
            "avoid": avoid,
            "userRequirements": run["task"],
            "userOverrides": user_overrides,
            "instruction": (
                "以 bestArtifactPath 对应的已采纳版本为唯一基线；优先修复 repair，"
                "保持 preserve，避免重新引入 avoid；只做与修复目标有关的修改。"
                "必须继续遵守 userRequirements；userOverrides 中的 Rubric 已因用户明确要求"
                "豁免，不得为了追求 Rubric 分数反向修改用户指定的结构、表达或交付形式。"
            ),
        }

    def submit(
        self, *, runId: str, artifactPath: str, timeoutSeconds: float | None = None
    ) -> dict[str, Any]:
        with self._lock:
            run = self._load(runId)
            if (run.get("stopState") or {}).get("stopped"):
                raise ReportLoopError("Report Loop 已停止，不能再提交新版本")
            minimum = int(os.environ.get("RESEARCH_REPORT_LOOP_MIN_REPORT_BYTES", "500"))
            source_path, report_text = self._read_artifact(artifactPath, minimum_bytes=minimum)
            version = f"v{len(run.get('revisions') or []) + 1}"
            parent_version = run.get("currentVersion")
            run_dir = self._run_dir(runId)
            rubric = json.loads((run_dir / "compiled_rubric.json").read_text(encoding="utf-8"))
            structured_data = None
            if run.get("structuredDataPath"):
                structured_data = json.loads(
                    (run_dir / run["structuredDataPath"]).read_text(encoding="utf-8")
                )
            case = {
                "case_id": runId,
                "original_user_query": run.get("originalUserQuery") or run["task"],
                "intake_context": copy.deepcopy(run.get("intakeContext") or {}),
                "audience": run.get("audience"),
                "input": {"intake": run["task"]},
                "turns": [{"round": 0, "label": "report_task", "prompt": run["task"]}],
                "structured_data": structured_data,
            }
            remaining = max(0.0, float(run["deadlineAt"]) - time.time())
            requested = remaining if timeoutSeconds is None else min(
                remaining, float(timeoutSeconds)
            )
            if requested <= 0:
                raise ReportLoopError("报告循环已达到最长运行时间")
            judge_call = self._custom_judge_call or (
                lambda prompt: call_judge(
                    prompt, settings=self.judge_settings, timeout_seconds=requested
                )
            )
            fallback_call = (
                (
                    lambda prompt: call_judge(
                        prompt,
                        settings=self.judge_fallback_settings,
                        timeout_seconds=requested,
                    )
                )
                if self.judge_fallback_settings
                else None
            )

            def activate_fallback(reason: str) -> None:
                if not self._judge_fallback_active.is_set():
                    self._judge_fallback_reason = reason
                    self._judge_fallback_active.set()

        try:
            result = judge_report(
                case,
                report_text,
                rubric,
                build_judge_prompt,
                judge_call,
                extract_json,
                strategy=JUDGE_STRATEGY_PER_DIMENSION,
                max_retries=DEFAULT_JUDGE_MAX_RETRIES,
                dimension_parallel=int(run.get("judgeParallelism") or DEFAULT_DIMENSION_PARALLELISM),
                fallback_call_model=fallback_call,
                fallback_active=self._judge_fallback_active.is_set,
                activate_fallback=activate_fallback,
            )
        except Exception as exc:
            with self._lock:
                self._event(runId, "judge.failed", {
                    "sourceArtifactPath": str(source_path),
                    "error": str(exc),
                })
            raise ReportLoopError(f"Judge 调用失败: {exc}") from exc
        if result.get("status") != "judged":
            error = result.get("error") or result.get("status")
            with self._lock:
                self._event(runId, "judge.failed", {"error": error})
            raise ReportLoopError(f"Judge 未完成: {error}")

        scored = score_labeled_check_judgment(result.get("checks") or {}, rubric)
        fallback_used = bool((result.get("judge_meta") or {}).get("fallback_used"))
        actual_settings = (
            self.judge_fallback_settings
            if fallback_used and self.judge_fallback_settings
            else self.judge_settings
        )
        judgment = {
            "checks": result.get("checks") or {},
            "checkScores": normalize_check_scores(result.get("checks") or {}),
            "reasoning": result.get("reasoning") or {},
            "dimensions": scored.get("scores") or {},
            "overall": scored.get("overall"),
            "redlineChecks": scored.get("redline_checks") or [],
            "hardFloorFailures": scored.get("hard_floor_failures") or [],
            "caseFailedGate": scored.get("case_failed_gate", False),
            "judgeMeta": result.get("judge_meta") or {},
            "judgeTrace": result.get("judge_trace") or {},
            "judgeProvider": actual_settings.provider,
            "judgeModel": actual_settings.model,
            "judgeEffort": actual_settings.effort,
            "judgeFallbackUsed": fallback_used,
            "judgeFallbackReason": self._judge_fallback_reason if fallback_used else None,
            "createdAt": _now(),
        }
        failure_report = failure_report_from_checks(
            judgment["checks"], judgment["reasoning"], rubric
        )

        with self._lock:
            run = self._load(runId)
            if len(run.get("revisions") or []) + 1 != int(version[1:]):
                raise ReportLoopError("报告版本已被并发修改")
            report_path = run_dir / "reports" / f"{version}.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(report_text + "\n", encoding="utf-8")
            item = {
                "version": version,
                "parentVersion": parent_version,
                "sourceArtifactPath": str(source_path),
                "reportPath": report_path.relative_to(run_dir).as_posix(),
                "reportSha256": _sha_text(report_text),
                "judgment": judgment,
                "failureReport": failure_report,
                "decision": "pending",
                "adoptionGate": None,
                "createdAt": _now(),
            }
            if version == "v1":
                item["decision"] = "accepted"
                run["currentVersion"] = version
                run["noImprovementStreak"] = 0
            else:
                parent = self._revision(run, parent_version)
                all_dimensions = [
                    str(dimension.get("name"))
                    for dimension in rubric.get("dimensions", [])
                    if dimension.get("name")
                ]
                targets = self._target_dimensions(parent, rubric)
                gate = evaluate_candidate_gate(
                    self._score_view(judgment),
                    self._score_view(parent["judgment"]),
                    targets,
                    all_dimensions,
                    self._drop_tolerance(rubric),
                )
                item["adoptionGate"] = gate
                item["targetDimensions"] = targets
                if gate["accepted"]:
                    item["decision"] = "accepted"
                    run["currentVersion"] = version
                    run["noImprovementStreak"] = 0
                else:
                    item["decision"] = "rejected"
                    run["noImprovementStreak"] = int(run.get("noImprovementStreak") or 0) + 1
            run["revisions"].append(item)
            judgment_path = run_dir / "judgments" / f"{version}.json"
            self._atomic_json(judgment_path, judgment)
            self._stop(run)
            brief = self._revision_brief(run, rubric, item)
            self._atomic_json(run_dir / "revision_briefs" / f"{version}.json", brief)
            self._save(run)
            self._event(runId, "report.judged", {
                "version": version,
                "decision": item["decision"],
                "overall": judgment["overall"],
                "currentVersion": run["currentVersion"],
                "stopCode": run["stopState"]["code"],
            })
            best = self._revision(run, run["currentVersion"])
            return {
                "status": "judged",
                "runId": runId,
                "version": version,
                "decision": item["decision"],
                "overall": judgment["overall"],
                "dimensions": judgment["dimensions"],
                "failedChecks": [
                    check_id
                    for check_id, value in judgment["checks"].items()
                    if value not in {"met", 1, 1.0}
                ],
                "revisionBrief": brief,
                "adoptionGate": item["adoptionGate"],
                "nextAction": "deliver" if run["stopState"]["stopped"] else "revise",
                "stopReason": run["stopState"]["reason"],
                "stopCode": run["stopState"]["code"],
                "bestVersion": run["currentVersion"],
                "bestArtifactPath": str((run_dir / best["reportPath"]).resolve()),
                "judgedVersions": len(run["revisions"]),
                "maxJudgedVersions": None,
            }

    def finish(self, *, runId: str, reason: str | None = None) -> dict[str, Any]:
        allowed_reasons = {
            "judge_unavailable",
            "rewrite_unavailable",
            "user_cancelled",
            "time_budget_exhausted",
        }
        with self._lock:
            run = self._load(runId)
            if not run.get("currentVersion"):
                raise ReportLoopError("还没有完成 Judge 的报告版本")
            stopped = bool((run.get("stopState") or {}).get("stopped"))
            if not stopped:
                if reason not in allowed_reasons:
                    raise ReportLoopError(
                        "Loop 尚未达到停止条件；仅在 Judge 不可用或用户取消时允许提前结束"
                    )
                run["stopState"] = {
                    "stopped": True,
                    "code": reason,
                    "reason": {
                        "judge_unavailable": "Judge infrastructure unavailable; returning the best judged version",
                        "rewrite_unavailable": "Rewriter infrastructure unavailable; returning the best judged version",
                        "user_cancelled": "User cancelled further iteration",
                        "time_budget_exhausted": "Report Loop reached its 60-minute deadline",
                    }[reason],
                }
                run["status"] = "completed"
                self._save(run)
                self._event(runId, "loop.finished_early", {"reason": reason})
            best = self._revision(run, run["currentVersion"])
            return {
                "status": "completed",
                "runId": runId,
                "bestVersion": run["currentVersion"],
                "bestScore": best["judgment"]["overall"],
                "bestArtifactPath": str((self._run_dir(runId) / best["reportPath"]).resolve()),
                "stopCode": run["stopState"]["code"],
                "stopReason": run["stopState"]["reason"],
                "judgedVersions": len(run["revisions"]),
                "judgeProvider": best["judgment"]["judgeProvider"],
                "judgeModel": best["judgment"]["judgeModel"],
                "judgeEffort": best["judgment"]["judgeEffort"],
                "judgeFallbackUsed": bool(best["judgment"].get("judgeFallbackUsed")),
            }

    def status(self, *, runId: str) -> dict[str, Any]:
        with self._lock:
            run = self._load(runId)
            result = copy.deepcopy(run)
            for item in result.get("revisions", []):
                judgment = item.get("judgment") or {}
                judgment.pop("judgeTrace", None)
            return result
