# -*- coding: utf-8 -*-
"""Private source-structured-data and data-quality audit engine.

The module deliberately has no OpenHarness app dependency.  It accepts either
an OpenHarness ``data.json`` or one standalone case, then runs Codex-backed
stages behind the public :mod:`data_workflow` API.
"""

from __future__ import annotations

import json
import re
import statistics
import subprocess
import tempfile
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = Path(__file__).resolve().parent / "data_quality_assets"
STRUCTURED_DATA_SKILL = ASSET_ROOT / "structured_data_prompt.md"
STRUCTURED_DATA_SCHEMA = ASSET_ROOT / "structured_data.schema.json"
AUDIT_DIMENSIONS = (
    REPO_ROOT / "skills" / "data-quality-audit" / "references" / "dimensions.md"
)
AUDIT_SCHEMA_DOC = (
    REPO_ROOT / "skills" / "data-quality-audit" / "references" / "result-schema.md"
)
AUDIT_SCHEMA = ASSET_ROOT / "audit.schema.json"
STRUCTURED_DATA_REPAIR_SCHEMA = ASSET_ROOT / "structured_data_repair.schema.json"

DEFAULT_STAGES = ("structured_data", "audit")
SCORE_VERSION = "dq-v2.1"


class DataQualityError(RuntimeError):
    """Raised for invalid inputs or invalid Codex outputs."""


class DataQualityCancelled(DataQualityError):
    """Raised when a caller cancels an in-progress workflow."""


ProgressCallback = Callable[[dict[str, Any]], None]
CancelCallback = Callable[[], bool]


def _notify(
    callback: ProgressCallback | None,
    event: str,
    **details: Any,
) -> None:
    if callback is not None:
        callback({"event": event, **details})


def _check_cancelled(callback: CancelCallback | None) -> None:
    if callback is not None and callback():
        raise DataQualityCancelled("数据质检已取消")


@dataclass(frozen=True)
class DataQualityRequest:
    """One standalone or OpenHarness-backed data-quality run."""

    output_root: Path
    dataset: Path | None = None
    case_ids: tuple[str, ...] = ()
    source_paths: tuple[Path, ...] = ()
    human_report: Path | None = None
    case_id: str | None = None
    background: str = ""
    structured_data: Path | None = None
    stages: tuple[str, ...] = DEFAULT_STAGES
    model: str = "gpt-5.6-sol"
    effort: str = "medium"
    parallel: int = 1
    timeout_seconds: float = 1800.0
    retries: int = 1
    force_structured_data: bool = False
    force_audit: bool = False
    force_repair: bool = False
    publish_structured_data: bool = False
    codex_command: tuple[str, ...] = ("codex",)
    structured_data_skill: Path = STRUCTURED_DATA_SKILL
    structured_data_schema: Path = STRUCTURED_DATA_SCHEMA
    audit_dimensions: Path = AUDIT_DIMENSIONS
    audit_schema_doc: Path = AUDIT_SCHEMA_DOC
    audit_schema: Path = AUDIT_SCHEMA
    structured_data_repair_schema: Path = STRUCTURED_DATA_REPAIR_SCHEMA

    def __post_init__(self) -> None:
        if bool(self.dataset) == bool(self.source_paths):
            raise ValueError("dataset 与 source_paths 必须且只能提供一种")
        if self.dataset is None and not (self.case_id or "").strip():
            raise ValueError("Standalone 模式必须提供 case_id")
        unknown = set(self.stages) - {"structured_data", "audit", "repair"}
        if unknown or not self.stages:
            raise ValueError(f"不支持的 stages: {sorted(unknown)}")
        if "repair" in self.stages and "audit" not in self.stages:
            raise ValueError("repair 阶段必须同时运行 audit")
        if "audit" in self.stages and self.dataset is None and self.human_report is None:
            raise ValueError("Standalone audit 必须提供 human_report")
        if self.parallel < 1:
            raise ValueError("parallel 必须至少为 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if self.retries < 0:
            raise ValueError("retries 不能小于 0")
        if not self.codex_command:
            raise ValueError("codex_command 不能为空")


@dataclass
class DataQualityCaseResult:
    case_id: str
    project: str
    status: str
    output_dir: str
    structured_data_status: str | None = None
    audit_status: str | None = None
    repair_status: str | None = None
    structured_data_items: int = 0
    structured_data_gap_count: int = 0
    repaired_structured_data_items: int = 0
    overall_score: float | None = None
    elapsed_seconds: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DataQualityBatchResult:
    status: str
    output_root: str
    started_at: str
    finished_at: str
    cases: list[DataQualityCaseResult] = field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return bool(self.cases) and all(item.status == "success" for item in self.cases)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "output_root": self.output_root,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "case_count": len(self.cases),
            "succeeded_count": sum(item.status == "success" for item in self.cases),
            "failed_count": sum(item.status != "success" for item in self.cases),
            "cases": [item.to_dict() for item in self.cases],
        }


@dataclass(frozen=True)
class _CaseBundle:
    case_id: str
    project: str
    background: str
    source_paths: tuple[Path, ...]
    human_report_text: str
    human_report_file: Path | None
    existing_structured_data: Path | None


def _safe_name(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-.")
    return clean or "case"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DataQualityError(f"无法读取 JSON {path}: {exc}") from exc


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _background(case: dict[str, Any]) -> str:
    lines = []
    for turn in case.get("turns") or []:
        if not isinstance(turn, dict) or turn.get("round") not in (0, 1, "0", "1"):
            continue
        text = str(turn.get("prompt") or turn.get("content") or "").strip()
        if text:
            lines.append(f"[round {turn.get('round')}] {text}")
    if lines:
        return "\n\n".join(lines)
    source = case.get("input") or {}
    return "\n\n".join(
        str(source.get(key) or "").strip()
        for key in ("brief", "intake")
        if str(source.get(key) or "").strip()
    )


def _resolve_dataset_paths(
    case: dict[str, Any], base: Path
) -> tuple[tuple[Path, ...], Path | None]:
    """Resolve writer inputs without feeding an old Structured Data file back as source."""
    paths = []
    structured_data_path = None
    for item in case.get("input_files") or []:
        if not isinstance(item, dict) or not item.get("source"):
            continue
        path = Path(str(item["source"])).expanduser()
        if not path.is_absolute():
            path = base / path
        path = path.resolve()
        if not path.exists():
            raise DataQualityError(f"原始资料不存在: {path}")
        target = str(item.get("target") or "").replace("\\", "/").lstrip("./")
        if target == "materials/00_structured_data.json":
            if structured_data_path is not None:
                raise DataQualityError(
                    f"{case.get('case_id') or case.get('id')} 存在多个 Structured Data 输入"
                )
            structured_data_path = path
            continue
        paths.append(path)
    if not paths:
        raise DataQualityError(
            f"{case.get('case_id') or case.get('id')} 没有可用 input_files"
        )
    sources = tuple(paths)
    if structured_data_path is None:
        _, structured_data_path = _project_and_structured_data(sources)
    return sources, structured_data_path


def _project_and_structured_data(
    sources: tuple[Path, ...],
) -> tuple[str, Path | None]:
    parents = {
        path.parent.resolve()
        for path in sources
        if path.is_dir() and path.name in {"source", "sources"}
    }
    if len(parents) == 1:
        case_dir = next(iter(parents))
        return case_dir.name, case_dir / "structured_data.json"
    first = sources[0]
    return (first.parent.name if first.is_file() else first.name), None


def _load_bundles(request: DataQualityRequest) -> list[_CaseBundle]:
    if request.dataset is None:
        sources = tuple(path.expanduser().resolve() for path in request.source_paths)
        missing = [str(path) for path in sources if not path.exists()]
        if missing:
            raise DataQualityError("原始资料不存在: " + ", ".join(missing))
        human_report = request.human_report
        if human_report is not None:
            human_report = human_report.expanduser().resolve()
            if not human_report.is_file():
                raise DataQualityError(f"human_report 不存在: {human_report}")
        project, discovered = _project_and_structured_data(sources)
        return [
            _CaseBundle(
                case_id=str(request.case_id),
                project=project,
                background=request.background.strip(),
                source_paths=sources,
                human_report_text="",
                human_report_file=human_report,
                existing_structured_data=(
                    request.structured_data.expanduser().resolve()
                    if request.structured_data is not None
                    else discovered
                ),
            )
        ]

    dataset = request.dataset.expanduser().resolve()
    payload = _read_json(dataset)
    cases = payload.get("cases") if isinstance(payload, dict) else payload
    if not isinstance(cases, list):
        raise DataQualityError("dataset 必须是数组或包含 cases 数组")
    selected = set(request.case_ids)
    bundles = []
    available = set()
    for case in cases:
        if not isinstance(case, dict):
            raise DataQualityError("dataset cases 中存在非对象元素")
        case_id = str(case.get("case_id") or case.get("id") or "").strip()
        if not case_id:
            raise DataQualityError("case 缺少 case_id")
        available.add(case_id)
        if selected and case_id not in selected:
            continue
        sources, structured_data_path = _resolve_dataset_paths(case, dataset.parent)
        if structured_data_path is not None:
            project = structured_data_path.parent.name
        else:
            project, _ = _project_and_structured_data(sources)
        human_report = case.get("human_report") or {}
        human_report_text = str(human_report.get("human_report_text") or "").strip()
        human_report_file_value = str(human_report.get("human_report_file") or "").strip()
        human_report_file = None
        if human_report_file_value:
            human_report_file = Path(human_report_file_value).expanduser()
            if not human_report_file.is_absolute():
                human_report_file = dataset.parent / human_report_file
            human_report_file = human_report_file.resolve()
            if not human_report_file.is_file():
                human_report_file = None
        if "audit" in request.stages and not human_report_text and human_report_file is None:
            raise DataQualityError(f"{case_id} 缺少可用 human_report")
        bundles.append(
            _CaseBundle(
                case_id=case_id,
                project=project,
                background=_background(case),
                source_paths=sources,
                human_report_text=human_report_text,
                human_report_file=human_report_file,
                existing_structured_data=structured_data_path,
            )
        )
    missing_ids = sorted(selected - available)
    if missing_ids:
        raise DataQualityError("dataset 中没有 case: " + ", ".join(missing_ids))
    if not bundles:
        raise DataQualityError("没有需要处理的 case")
    return bundles


class CodexJsonRunner:
    """One reusable, read-only Codex CLI JSON runner."""

    def __init__(self, request: DataQualityRequest) -> None:
        self.request = request

    def run(
        self,
        *,
        prompt: str,
        schema_path: Path,
        cwd: Path,
    ) -> tuple[dict[str, Any], float]:
        last_error = ""
        for attempt in range(1, self.request.retries + 2):
            started = time.monotonic()
            with tempfile.TemporaryDirectory(prefix="data-quality-") as temporary:
                result_path = Path(temporary) / "result.json"
                command = [
                    *self.request.codex_command,
                    "exec",
                    "--ephemeral",
                    "--ignore-user-config",
                    "--sandbox",
                    "read-only",
                    "--skip-git-repo-check",
                    "--color",
                    "never",
                    "--model",
                    self.request.model,
                    "--config",
                    f'model_reasoning_effort="{self.request.effort}"',
                    "--output-schema",
                    str(schema_path),
                    "--output-last-message",
                    str(result_path),
                    "-C",
                    str(cwd),
                    "-",
                ]
                try:
                    completed = subprocess.run(
                        command,
                        input=prompt,
                        text=True,
                        capture_output=True,
                        timeout=self.request.timeout_seconds,
                        check=False,
                    )
                    if completed.returncode != 0:
                        detail = completed.stderr.strip() or completed.stdout.strip()
                        raise DataQualityError(
                            f"Codex CLI exit={completed.returncode}: {detail[-3000:]}"
                        )
                    payload = _read_json(result_path)
                    if not isinstance(payload, dict):
                        raise DataQualityError("Codex 输出不是 JSON 对象")
                    return payload, round(time.monotonic() - started, 1)
                except (
                    OSError,
                    subprocess.TimeoutExpired,
                    DataQualityError,
                ) as exc:
                    last_error = f"attempt {attempt}: {exc}"
        raise DataQualityError(last_error)


def _validate_structured_data(payload: Any, case_id: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DataQualityError("Structured Data 不是 JSON 对象")
    if payload.get("schema") != "openharness-structured-data/v1":
        raise DataQualityError("Structured Data schema 不正确")
    if payload.get("case_id") != case_id:
        raise DataQualityError("Structured Data case_id 不一致")
    if set(payload) != {"schema", "case_id", "items", "unresolved"}:
        raise DataQualityError("Structured Data 包含未约定字段")
    items = payload.get("items")
    if not isinstance(items, list) or not items:
        raise DataQualityError("Structured Data items 为空")
    ids = []
    for index, item in enumerate(items, 1):
        if not isinstance(item, dict) or set(item) != {
            "id",
            "type",
            "source_ref",
            "content",
        }:
            raise DataQualityError(f"Structured Data items[{index}] 字段不合法")
        if not all(isinstance(value, str) and value.strip() for value in item.values()):
            raise DataQualityError(f"Structured Data items[{index}] 存在空字段")
        if not re.fullmatch(r"EV-\d{3,}", item["id"]):
            raise DataQualityError(f"Evidence ID 不合法: {item['id']}")
        ids.append(item["id"])
    if len(ids) != len(set(ids)):
        raise DataQualityError("Evidence ID 重复")
    unresolved = payload.get("unresolved")
    if not isinstance(unresolved, list) or any(
        not isinstance(item, str) or not item.strip() for item in unresolved
    ):
        raise DataQualityError("Structured Data unresolved 不合法")
    return payload


def _load_valid_structured_data(path: Path | None, case_id: str) -> dict[str, Any] | None:
    if path is None or not path.is_file():
        return None
    try:
        return _validate_structured_data(_read_json(path), case_id)
    except DataQualityError:
        return None


def _structured_data_prompt(bundle: _CaseBundle, skill_path: Path) -> str:
    sources = "\n".join(f"- {path}" for path in bundle.source_paths)
    return f"""先完整阅读并严格执行这个 Skill：
{skill_path}

为下面这个 case 生成 Structured Data。

case_id:
{bundle.case_id}

round 0-1 背景信息：
{bundle.background or "未提供额外背景"}

原始资料路径：
{sources}

要求：
- 实际检查上述路径中的全部相关文件。
- 只读取原始资料和背景信息，不读取 human report、参考报告或候选报告。
- 不写入或修改任何原始资料，不使用 shell heredoc 或临时文件。
- 最终只输出符合 Skill 和 output schema 的 JSON。
"""


def _audit_prompt(
    bundle: _CaseBundle,
    structured_data_path: Path,
    dimensions_path: Path,
    schema_doc_path: Path,
) -> str:
    sources = "\n".join(f"- {path}" for path in bundle.source_paths)
    if bundle.human_report_text:
        human_report = f"""human_report 文本如下：
<human_report>
{bundle.human_report_text}
</human_report>"""
    else:
        human_report = f"完整读取 human_report 文件：{bundle.human_report_file}"
    return f"""你正在执行研究数据质检。只读分析，不修改任何文件。

先完整读取：
1. Structured Data：{structured_data_path}
2. 判定规则：{dimensions_path}
3. 输出字段：{schema_doc_path}

项目：{bundle.project}
case_id：{bundle.case_id}
背景：
{bundle.background or "未提供额外背景"}

{human_report}

原始 source：
{sources}

任务：
- 从 human_report 抽取最多 40 条真正影响摘要、结论、建议或章节主论点的关键论据。
- 对 structured_data 中每一个 EV id 恰好分类一次：used / noise / conflict。
- 对每条 Human Report 关键论据同时检查 structured_data 和原始 source；structured_data 与 source
  都无充分支撑才列 omission。
- 如果事实已存在于 source，但 structured_data 完全漏掉或遗漏关键数字、分群、
  反例、限定条件，则列入 structured_data_gaps，不得误判为数据遗漏。
- structured_data_gaps.source_fact 必须重新从 source 提取并可独立核验，不得复制
  只存在于 human_report 的内容；ID 使用 MG-001 起连续编号。
- 发现冲突时，核对对象、时间、样本、地域和口径；可解释差异只写 scope_risks。
- 数值相对误差不超过1%，或比例绝对差不超过0.5个百分点时，不得判 conflict。
- 若差异只是四舍五入且排序、方向或约数结论不变，归入 used，并把精确计数差异写入 scope_risks。
- “来源不足以推出Human Report结论”属于 omission，不属于 conflict。
- structured_data 是 source 的结构化索引，不重复计数。
- 不使用 shell heredoc 或任何临时文件；优先使用只读命令。
- quote 只保留足够核验的短摘录，中文输出。
- 严格返回 output schema 要求的 JSON，不要返回 Markdown。
"""


def _structured_data_gaps_payload(
    bundle: _CaseBundle,
    project: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema": "openharness-structured-data-gaps/v1",
        "case_id": bundle.case_id,
        "items": [
            {
                "id": item["id"],
                "gap_type": item["gap_type"],
                "importance": item["importance"],
                "source_fact": item["source_fact"],
                "source_ref": item["source_ref"],
            }
            for item in project["structured_data_gaps"]
        ],
    }


def _repair_prompt(
    bundle: _CaseBundle,
    structured_data_path: Path,
    gaps_path: Path,
) -> str:
    sources = "\n".join(f"- {path}" for path in bundle.source_paths)
    return f"""你正在修复 Structured Data 的抽取遗漏。只读分析，不修改文件。

完整读取：
1. 原始 Structured Data：{structured_data_path}
2. 待核验的 Structured Data 缺口：{gaps_path}
3. 原始 source：
{sources}

case_id：{bundle.case_id}

要求：
- 不读取 human_report、audit.json、audit.raw.json 或参考报告。
- 对每个 MG id 重新回查 source；只有 source 可独立核验时才生成 addition。
- addition 必须是 source-grounded 的完整事实，并使用真实文件名及准确页码、
  Sheet、表格或数据区域作为 source_ref。
- 不复制缺口描述的措辞；以 source 为准重新表述，不增加 source 中没有的数字、
  结论、因果或技术机制。
- 不重复现有 Structured Data 已充分覆盖的事实；若多个 MG 可由同一条独立证据覆盖，
  可合并到一个 addition 的 gap_ids。
- 无法从 source 复核、与现有 Evidence 实质重复或定位不可靠时放入 skipped，
  并说明原因。
- 只输出新增 Evidence，不修改、删除或重新编号现有 Evidence；Python 会负责
  合并和分配 EV id。
- 每个 MG id 必须恰好出现在一个 addition.gap_ids 或一个 skipped.gap_id 中。
- 严格返回 output schema 要求的 JSON，不要返回 Markdown。
"""


def _validate_repair(
    raw: dict[str, Any],
    case_id: str,
    expected_gap_ids: set[str],
) -> dict[str, Any]:
    if raw.get("case_id") != case_id:
        raise DataQualityError("Structured Data repair case_id 不一致")
    additions = raw.get("additions")
    skipped = raw.get("skipped")
    if not isinstance(additions, list) or not isinstance(skipped, list):
        raise DataQualityError("Structured Data repair additions/skipped 不合法")
    covered: list[str] = []
    for index, item in enumerate(additions, 1):
        if not isinstance(item, dict) or set(item) != {
            "gap_ids",
            "type",
            "source_ref",
            "content",
        }:
            raise DataQualityError(f"Structured Data repair additions[{index}] 字段不合法")
        gap_ids = item.get("gap_ids")
        if not isinstance(gap_ids, list) or not gap_ids:
            raise DataQualityError(f"Structured Data repair additions[{index}] gap_ids 为空")
        if not all(
            isinstance(value, str) and value.strip()
            for key, value in item.items()
            if key != "gap_ids"
        ):
            raise DataQualityError(f"Structured Data repair additions[{index}] 存在空字段")
        covered.extend(gap_ids)
    for index, item in enumerate(skipped, 1):
        if not isinstance(item, dict) or set(item) != {"gap_id", "reason"}:
            raise DataQualityError(f"Structured Data repair skipped[{index}] 字段不合法")
        if not all(isinstance(value, str) and value.strip() for value in item.values()):
            raise DataQualityError(f"Structured Data repair skipped[{index}] 存在空字段")
        covered.append(item["gap_id"])
    if len(covered) != len(set(covered)) or set(covered) != expected_gap_ids:
        raise DataQualityError("Structured Data repair 未唯一处理全部 Structured Data gap")
    return raw


def _merge_structured_data(
    structured_data: dict[str, Any],
    repair: dict[str, Any],
) -> dict[str, Any]:
    merged = {
        "schema": structured_data["schema"],
        "case_id": structured_data["case_id"],
        "items": [dict(item) for item in structured_data["items"]],
        "unresolved": list(structured_data["unresolved"]),
    }
    next_number = max(
        int(item["id"].split("-", 1)[1])
        for item in merged["items"]
    ) + 1
    for addition in repair["additions"]:
        merged["items"].append(
            {
                "id": f"EV-{next_number:03d}",
                "type": addition["type"],
                "source_ref": addition["source_ref"],
                "content": addition["content"],
            }
        )
        next_number += 1
    return _validate_structured_data(merged, structured_data["case_id"])


def _validate_and_score_audit(
    raw: dict[str, Any],
    bundle: _CaseBundle,
    structured_data: dict[str, Any],
) -> dict[str, Any]:
    if raw.get("project") != bundle.project or raw.get("case_id") != bundle.case_id:
        raise DataQualityError("Audit 项目标识不一致")
    expected_ids = {item["id"] for item in structured_data["items"]}
    classifications = raw.get("evidence_classifications")
    if not isinstance(classifications, list):
        raise DataQualityError("Audit evidence_classifications 不合法")
    actual_ids = [item.get("evidence_id") for item in classifications]
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected_ids:
        raise DataQualityError("Audit 未对全部 Evidence 唯一分类")
    human_report_core = raw.get("human_report_core")
    if not isinstance(human_report_core, list) or not human_report_core:
        raise DataQualityError("Audit human_report_core 为空")
    human_report_ids = {item.get("id") for item in human_report_core}
    omissions = raw.get("omissions")
    conflicts = raw.get("conflicts")
    structured_data_gaps = raw.get("structured_data_gaps")
    if (
        not isinstance(omissions, list)
        or not isinstance(conflicts, list)
        or not isinstance(structured_data_gaps, list)
    ):
        raise DataQualityError("Audit omissions/conflicts/structured_data_gaps 不合法")
    if any(item.get("human_report_id") not in human_report_ids for item in omissions):
        raise DataQualityError("Audit omission 引用了未知 Human Report")
    gap_ids = [item.get("id") for item in structured_data_gaps]
    expected_gap_ids = [f"MG-{index:03d}" for index in range(1, len(gap_ids) + 1)]
    if gap_ids != expected_gap_ids:
        raise DataQualityError("Audit Structured Data gap ID 不合法或重复")
    for index, item in enumerate(structured_data_gaps, 1):
        if not isinstance(item, dict) or set(item) != {
            "id",
            "human_report_ids",
            "gap_type",
            "importance",
            "source_fact",
            "source_ref",
            "reason",
        }:
            raise DataQualityError(f"Audit Structured Data gap[{index}] 字段不合法")
        if (
            not item["human_report_ids"]
            or any(human_report_id not in human_report_ids for human_report_id in item["human_report_ids"])
        ):
            raise DataQualityError("Audit Structured Data gap 引用了未知 Human Report")
        if item["gap_type"] not in {"missing", "underrepresented"}:
            raise DataQualityError("Audit Structured Data gap 类型不合法")
        if item["importance"] not in {"critical", "material"}:
            raise DataQualityError("Audit Structured Data gap 重要性不合法")
        if not all(
            isinstance(item[key], str) and item[key].strip()
            for key in ("source_fact", "source_ref", "reason")
        ):
            raise DataQualityError("Audit Structured Data gap 存在空字段")
    classified_conflicts = {
        item["evidence_id"]
        for item in classifications
        if item.get("classification") == "conflict"
    }
    detailed_conflicts = {item.get("evidence_id") for item in conflicts}
    if classified_conflicts != detailed_conflicts:
        raise DataQualityError("Audit conflict 分类与明细不一致")
    for item in conflicts:
        reason = str(item.get("reason") or "")
        rounding_only = (
            "四舍五入" in reason
            or re.search(r"相差\s*(?:1|一)(?:个|例|条|题)?", reason)
        )
        unchanged = any(
            marker in reason
            for marker in ("不受影响", "方向不变", "结论不变", "排序不变")
        )
        if "容差内" in reason or "不构成冲突" in reason or (
            rounding_only and unchanged
        ):
            raise DataQualityError(
                f"Audit 将容差内/取整差异误判为 conflict: {item.get('evidence_id')}"
            )

    counts = Counter(item["classification"] for item in classifications)
    human_report_total = len(human_report_core)
    source_total = len(classifications)
    omission_count = len(omissions)
    critical_omissions = sum(item.get("severity") == "critical" for item in omissions)
    ordinary_omissions = omission_count - critical_omissions
    conflict_severity: dict[str, str] = {}
    for item in conflicts:
        evidence_id = item["evidence_id"]
        if item.get("severity") == "critical" or evidence_id not in conflict_severity:
            conflict_severity[evidence_id] = item.get("severity", "material")
    critical_conflicts = sum(value == "critical" for value in conflict_severity.values())
    ordinary_conflicts = len(conflict_severity) - critical_conflicts
    conflict_count = len(conflict_severity)

    omission_ratio = omission_count / human_report_total
    conflict_ratio = conflict_count / human_report_total
    noise_rate = counts["noise"] / source_total
    omission_score = 100 * (1 - omission_ratio)
    conflict_score = max(
        0.0,
        100 - 20 * critical_conflicts - 10 * ordinary_conflicts,
    )
    signal_score = 100 * (1 - noise_rate)
    overall_score = (
        0.40 * omission_score
        + 0.40 * conflict_score
        + 0.20 * signal_score
    )

    result = dict(raw)
    result.pop("confidence", None)
    result.pop("confidence_notes", None)
    result["metrics"] = {
        "source_evidence_total": source_total,
        "used_count": counts["used"],
        "noise_count": counts["noise"],
        "conflict_count": conflict_count,
        "conflict_detail_count": len(conflicts),
        "human_report_core_total": human_report_total,
        "omission_count": omission_count,
        "critical_omission_count": critical_omissions,
        "ordinary_omission_count": ordinary_omissions,
        "critical_conflict_count": critical_conflicts,
        "ordinary_conflict_count": ordinary_conflicts,
        "omission_ratio": round(omission_ratio, 4),
        "conflict_ratio": round(conflict_ratio, 4),
        "noise_rate": round(noise_rate, 4),
        "omission_score": round(omission_score, 1),
        "conflict_score": round(conflict_score, 1),
        "signal_score": round(signal_score, 1),
        "overall_score": round(overall_score, 1),
        "source_file_count": sum(
            1
            for source in bundle.source_paths
            for path in ([source] if source.is_file() else source.rglob("*"))
            if path.is_file()
        ),
        "unresolved_count": len(structured_data["unresolved"]),
        "structured_data_gap_count": len(structured_data_gaps),
    }
    return result


_OMISSION_SEVERITY = {"critical": "关键遗漏", "material": "普通遗漏"}
_CONFLICT_SEVERITY = {"critical": "关键冲突", "material": "普通冲突"}
_CLASSIFICATION = {"used": "已采用", "noise": "噪声", "conflict": "冲突"}


def _clean(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def render_audit_markdown(project: dict[str, Any], audit_date: str) -> str:
    """Render one scored project without document-generation dependencies."""

    metrics = project["metrics"]
    lines = [
        f"# {project['project']} 数据质检报告",
        "",
        f"- 审计日期：{audit_date}",
        f"- 评分版本：{SCORE_VERSION}",
        "",
        "## 结论",
        "",
        project["assessment"],
        "",
        "## 分数",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 综合质量分 | {metrics['overall_score']:.1f} / 100 |",
        f"| 遗漏覆盖分 | {metrics['omission_score']:.1f} / 100 |",
        f"| 冲突一致性分 | {metrics['conflict_score']:.1f} / 100 |",
        f"| 信噪分 | {metrics['signal_score']:.1f} / 100 |",
        (
            f"| 遗漏项 | {metrics['omission_count']}/{metrics['human_report_core_total']}"
            f"（{metrics['omission_ratio'] * 100:.1f}%）；关键遗漏 "
            f"{metrics['critical_omission_count']} 条，普通遗漏 "
            f"{metrics['ordinary_omission_count']} 条 |"
        ),
        (
            f"| 冲突项 | {metrics['conflict_count']}/{metrics['human_report_core_total']}"
            f"（{metrics['conflict_ratio'] * 100:.1f}%）；关键冲突 "
            f"{metrics['critical_conflict_count']} 条，普通冲突 "
            f"{metrics['ordinary_conflict_count']} 条 |"
        ),
        (
            f"| 信噪比（噪声占比） | {metrics['noise_count']}/"
            f"{metrics['source_evidence_total']}"
            f"（{metrics['noise_rate'] * 100:.1f}%） |"
        ),
        "",
        "综合分 = 遗漏覆盖分×40% + 冲突一致性分×40% + 信噪分×20%。",
        "",
        f"## 遗漏项（{len(project['omissions'])}）",
        "",
    ]
    for index, item in enumerate(project["omissions"], 1):
        lines.extend(
            [
                (
                    f"### O-{index:02d} "
                    f"[{_OMISSION_SEVERITY[item['severity']]}] {item['human_report_text']}"
                ),
                "",
                f"- Human Report 原文：{item['human_report_quote']}",
                f"- 位置：{item['human_report_location']}",
                f"- 回查范围：{item['search_note']}",
                f"- 判定：{item['reason']}",
                "",
            ]
        )
    lines.extend(
        [
            (
                f"## 冲突项（{metrics['conflict_count']} 项；"
                f"{len(project['conflicts'])} 条 Human Report 对照明细）"
            ),
            "",
        ]
    )
    for index, item in enumerate(project["conflicts"], 1):
        lines.extend(
            [
                (
                    f"### C-{index:02d} "
                    f"[{_CONFLICT_SEVERITY[item['severity']]}] "
                    f"{item['conflict_type']}"
                ),
                "",
                f"- Human Report：{item['human_report_quote']}",
                f"- 来源论据：{item['source_text']}",
                f"- 来源位置：{item['source_ref']}",
                f"- 冲突说明：{item['reason']}",
                "",
            ]
        )
    lines.extend(
        [
            f"## Structured Data 完整性缺口（{len(project['structured_data_gaps'])}）",
            "",
        ]
    )
    for item in project["structured_data_gaps"]:
        gap_label = (
            "完全漏提"
            if item["gap_type"] == "missing"
            else "提取不完整"
        )
        lines.extend(
            [
                f"### {item['id']} [{gap_label}] {item['source_fact']}",
                "",
                f"- 对应 Human Report：{'、'.join(item['human_report_ids'])}",
                f"- 来源位置：{item['source_ref']}",
                f"- 判定：{item['reason']}",
                "",
            ]
        )
    noise_items = [
        item
        for item in project["evidence_classifications"]
        if item["classification"] == "noise"
    ]
    lines.extend(
        [
            (
                f"## 噪声项（{len(noise_items)}；"
                f"噪声率 {metrics['noise_rate'] * 100:.1f}%）"
            ),
            "",
            "| 主题 | 证据 ID | 代表内容 | 未采用原因 |",
            "|---|---|---|---|",
        ]
    )
    for cluster in project["noise_clusters"]:
        lines.append(
            f"| {_clean(cluster['theme'])} | "
            f"{_clean('、'.join(cluster['evidence_ids']))} | "
            f"{_clean(cluster['representative_text'])} | "
            f"{_clean(cluster['reason'])} |"
        )
    lines.extend(["", "## 口径与可核验风险", ""])
    lines.extend(f"- {item}" for item in project["scope_risks"])
    lines.extend(["", "## 整改建议", ""])
    lines.extend(f"- {item}" for item in project["recommendations"])
    lines.extend(
        [
            "",
            "## 来源论据分类",
            "",
            "| ID | 分类 | 对应 Human Report | 判定依据 |",
            "|---|---|---|---|",
        ]
    )
    for item in project["evidence_classifications"]:
        lines.append(
            f"| {_clean(item['evidence_id'])} | "
            f"{_CLASSIFICATION[item['classification']]} | "
            f"{_clean('、'.join(item['human_report_ids']) or '—')} | "
            f"{_clean(item['reason'])} |"
        )
    lines.append("")
    return "\n".join(lines)


def _audit_payload(project: dict[str, Any], dataset_root: str) -> dict[str, Any]:
    return {
        "schema": "data-quality-audit/v2",
        "audit_date": date.today().isoformat(),
        "dataset_root": dataset_root,
        "dataset_summary": {
            "project_count": 1,
            "overall_score": project["metrics"]["overall_score"],
            "median_project_score": project["metrics"]["overall_score"],
            "total_source_evidence": project["metrics"]["source_evidence_total"],
            "total_human_report_core": project["metrics"]["human_report_core_total"],
            "total_omissions": project["metrics"]["omission_count"],
            "total_conflicts": project["metrics"]["conflict_count"],
            "total_noise": project["metrics"]["noise_count"],
            "total_structured_data_gaps": project["metrics"]["structured_data_gap_count"],
        },
        "methodology": {
            "categories": ["omission", "conflict", "noise"],
            "weights": {"omission": 0.40, "conflict": 0.40, "signal": 0.20},
            "noise_rate": "noise / source_evidence_total",
            "score_version": SCORE_VERSION,
        },
        "projects": [project],
    }


def _process_case(
    bundle: _CaseBundle,
    request: DataQualityRequest,
    runner: CodexJsonRunner,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> DataQualityCaseResult:
    started = time.monotonic()
    output_dir = request.output_root.expanduser().resolve() / _safe_name(bundle.case_id)
    output_dir.mkdir(parents=True, exist_ok=True)
    structured_data_path = output_dir / "structured_data.json"
    audit_raw_path = output_dir / "audit.raw.json"
    audit_path = output_dir / "audit.json"
    report_path = output_dir / "audit.md"
    gaps_path = output_dir / "structured_data_gaps.json"
    repair_raw_path = output_dir / "structured_data_repair.raw.json"
    repaired_structured_data_path = output_dir / "structured_data.repaired.json"
    timings: dict[str, float] = {}
    structured_data_status = None
    audit_status = None
    repair_status = None
    structured_data_gap_count = 0
    repaired_structured_data_items = 0
    try:
        _check_cancelled(should_cancel)
        _notify(progress_callback, "case_started", case_id=bundle.case_id)
        structured_data = None
        if not request.force_structured_data:
            structured_data = _load_valid_structured_data(structured_data_path, bundle.case_id)
            if structured_data is not None:
                structured_data_status = "skipped"
            if structured_data is None:
                structured_data = _load_valid_structured_data(bundle.existing_structured_data, bundle.case_id)
                if structured_data is not None:
                    _write_json(structured_data_path, structured_data)
                    structured_data_status = "reused"
        if structured_data is None:
            _notify(
                progress_callback,
                "stage_started",
                case_id=bundle.case_id,
                stage="structured_data",
            )
            raw_structured_data, elapsed = runner.run(
                prompt=_structured_data_prompt(
                    bundle,
                    request.structured_data_skill.expanduser().resolve(),
                ),
                schema_path=request.structured_data_schema.expanduser().resolve(),
                cwd=REPO_ROOT,
            )
            timings["structured_data"] = elapsed
            structured_data = _validate_structured_data(raw_structured_data, bundle.case_id)
            _write_json(structured_data_path, structured_data)
            structured_data_status = "generated"
        _notify(
            progress_callback,
            "stage_completed",
            case_id=bundle.case_id,
            stage="structured_data",
            status=structured_data_status,
        )

        overall_score = None
        audit_project = None
        if "audit" in request.stages:
            _check_cancelled(should_cancel)
            _notify(
                progress_callback,
                "stage_started",
                case_id=bundle.case_id,
                stage="audit",
            )
            audit_payload = None
            if audit_path.is_file() and not request.force_audit:
                candidate = _read_json(audit_path)
                if (
                    isinstance(candidate, dict)
                    and candidate.get("schema") == "data-quality-audit/v2"
                    and candidate.get("projects")
                    and candidate["projects"][0].get("case_id") == bundle.case_id
                    and isinstance(
                        candidate["projects"][0].get("structured_data_gaps"),
                        list,
                    )
                ):
                    audit_payload = candidate
                    audit_status = "skipped"
            if audit_payload is None:
                prompt = _audit_prompt(
                    bundle,
                    structured_data_path,
                    request.audit_dimensions.expanduser().resolve(),
                    request.audit_schema_doc.expanduser().resolve(),
                )
                audit_elapsed = 0.0
                for semantic_attempt in range(request.retries + 1):
                    raw_audit, elapsed = runner.run(
                        prompt=prompt,
                        schema_path=request.audit_schema.expanduser().resolve(),
                        cwd=REPO_ROOT,
                    )
                    audit_elapsed += elapsed
                    try:
                        project = _validate_and_score_audit(
                            raw_audit,
                            bundle,
                            structured_data,
                        )
                        break
                    except DataQualityError as exc:
                        if semantic_attempt >= request.retries:
                            raise
                        prompt += (
                            "\n\n上一次结构化结果未通过确定性校验："
                            f"{exc}。请重新核验全部分类后输出完整 JSON。"
                        )
                timings["audit"] = round(audit_elapsed, 1)
                _write_json(audit_raw_path, raw_audit)
                dataset_root = str(
                    request.dataset.expanduser().resolve().parent
                    if request.dataset is not None
                    else bundle.source_paths[0].parent
                )
                audit_payload = _audit_payload(project, dataset_root)
                _write_json(audit_path, audit_payload)
                report_path.write_text(
                    render_audit_markdown(project, audit_payload["audit_date"]),
                    encoding="utf-8",
                )
                audit_status = "generated"
            audit_project = audit_payload["projects"][0]
            overall_score = audit_project["metrics"]["overall_score"]
            structured_data_gap_count = len(audit_project["structured_data_gaps"])
            _write_json(
                gaps_path,
                _structured_data_gaps_payload(bundle, audit_project),
            )
            _notify(
                progress_callback,
                "stage_completed",
                case_id=bundle.case_id,
                stage="audit",
                status=audit_status,
                overall_score=overall_score,
            )

        final_structured_data = structured_data
        if "repair" in request.stages:
            _check_cancelled(should_cancel)
            _notify(
                progress_callback,
                "stage_started",
                case_id=bundle.case_id,
                stage="repair",
            )
            cached_repair = None
            if repaired_structured_data_path.is_file() and not any(
                (
                    request.force_structured_data,
                    request.force_audit,
                    request.force_repair,
                )
            ):
                cached_repair = _load_valid_structured_data(
                    repaired_structured_data_path,
                    bundle.case_id,
                )
            if cached_repair is not None:
                final_structured_data = cached_repair
                repair_status = "skipped"
            elif not audit_project["structured_data_gaps"]:
                _write_json(repaired_structured_data_path, structured_data)
                final_structured_data = structured_data
                repair_status = "not_needed"
            else:
                raw_repair, elapsed = runner.run(
                    prompt=_repair_prompt(
                        bundle,
                        structured_data_path,
                        gaps_path,
                    ),
                    schema_path=request.structured_data_repair_schema.expanduser().resolve(),
                    cwd=REPO_ROOT,
                )
                timings["repair"] = elapsed
                expected_gap_ids = {
                    item["id"]
                    for item in audit_project["structured_data_gaps"]
                }
                repair = _validate_repair(
                    raw_repair,
                    bundle.case_id,
                    expected_gap_ids,
                )
                final_structured_data = _merge_structured_data(structured_data, repair)
                _write_json(repair_raw_path, repair)
                _write_json(repaired_structured_data_path, final_structured_data)
                repair_status = "generated"
            repaired_structured_data_items = len(final_structured_data["items"])
            _notify(
                progress_callback,
                "stage_completed",
                case_id=bundle.case_id,
                stage="repair",
                status=repair_status,
            )

        if request.publish_structured_data and bundle.existing_structured_data is not None:
            _write_json(bundle.existing_structured_data, final_structured_data)

        elapsed_total = round(time.monotonic() - started, 1)
        _write_json(
            output_dir / "run_manifest.json",
            {
                "schema": "openharness-data-quality-run/v1",
                "case_id": bundle.case_id,
                "project": bundle.project,
                "stages": list(request.stages),
                "model": request.model,
                "effort": request.effort,
                "structured_data_status": structured_data_status,
                "audit_status": audit_status,
                "repair_status": repair_status,
                "stage_elapsed_seconds": timings,
                "elapsed_seconds": elapsed_total,
                "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "artifacts": {
                    "structured_data": str(structured_data_path),
                    "audit": str(audit_path) if "audit" in request.stages else None,
                    "report": str(report_path) if "audit" in request.stages else None,
                    "structured_data_gaps": (
                        str(gaps_path) if "audit" in request.stages else None
                    ),
                    "repaired_structured_data": (
                        str(repaired_structured_data_path)
                        if "repair" in request.stages
                        else None
                    ),
                },
            },
        )
        result = DataQualityCaseResult(
            case_id=bundle.case_id,
            project=bundle.project,
            status="success",
            output_dir=str(output_dir),
            structured_data_status=structured_data_status,
            audit_status=audit_status,
            repair_status=repair_status,
            structured_data_items=len(structured_data["items"]),
            structured_data_gap_count=structured_data_gap_count,
            repaired_structured_data_items=repaired_structured_data_items,
            overall_score=overall_score,
            elapsed_seconds=elapsed_total,
        )
        _notify(
            progress_callback,
            "case_completed",
            case_id=bundle.case_id,
            status=result.status,
            overall_score=result.overall_score,
        )
        return result
    except DataQualityCancelled:
        raise
    except Exception as exc:  # noqa: BLE001
        result = DataQualityCaseResult(
            case_id=bundle.case_id,
            project=bundle.project,
            status="failed",
            output_dir=str(output_dir),
            structured_data_status=structured_data_status,
            audit_status=audit_status,
            repair_status=repair_status,
            structured_data_gap_count=structured_data_gap_count,
            elapsed_seconds=round(time.monotonic() - started, 1),
            error=str(exc),
        )
        _notify(
            progress_callback,
            "case_completed",
            case_id=bundle.case_id,
            status=result.status,
            error=result.error,
        )
        return result


def _batch_summary(result: DataQualityBatchResult) -> dict[str, Any]:
    scored = [item.overall_score for item in result.cases if item.overall_score is not None]
    return {
        **result.to_dict(),
        "average_case_score": (
            round(statistics.mean(scored), 1) if scored else None
        ),
        "median_case_score": (
            round(statistics.median(scored), 1) if scored else None
        ),
        "elapsed_seconds": round(
            sum(item.elapsed_seconds for item in result.cases),
            1,
        ),
    }


def run_data_quality(
    request: DataQualityRequest,
    progress_callback: ProgressCallback | None = None,
    should_cancel: CancelCallback | None = None,
) -> DataQualityBatchResult:
    """Run requested stages and return a serializable batch result."""

    _check_cancelled(should_cancel)
    required = [
        request.structured_data_skill,
        request.structured_data_schema,
    ]
    if "audit" in request.stages:
        required.extend(
            [
                request.audit_dimensions,
                request.audit_schema_doc,
                request.audit_schema,
            ]
        )
    if "repair" in request.stages:
        required.append(request.structured_data_repair_schema)
    for raw_path in required:
        path = raw_path.expanduser().resolve()
        if not path.is_file():
            raise DataQualityError(f"缺少工具资源: {path}")
    bundles = _load_bundles(request)
    request.output_root.expanduser().resolve().mkdir(parents=True, exist_ok=True)
    started_at = datetime.now().astimezone().isoformat(timespec="seconds")
    _notify(
        progress_callback,
        "workflow_started",
        case_count=len(bundles),
        stages=list(request.stages),
    )
    runner = CodexJsonRunner(request)
    cases: list[DataQualityCaseResult] = []
    workers = min(request.parallel, len(bundles))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(
                _process_case,
                bundle,
                request,
                runner,
                progress_callback,
                should_cancel,
            ): bundle.case_id
            for bundle in bundles
        }
        for future in as_completed(futures):
            try:
                cases.append(future.result())
            except DataQualityCancelled:
                for pending in futures:
                    pending.cancel()
                raise
    order = {bundle.case_id: index for index, bundle in enumerate(bundles)}
    cases.sort(key=lambda item: order[item.case_id])
    if all(item.status == "success" for item in cases):
        status = "success"
    elif any(item.status == "success" for item in cases):
        status = "partial"
    else:
        status = "failed"
    result = DataQualityBatchResult(
        status=status,
        output_root=str(request.output_root.expanduser().resolve()),
        started_at=started_at,
        finished_at=datetime.now().astimezone().isoformat(timespec="seconds"),
        cases=cases,
    )
    _write_json(
        request.output_root.expanduser().resolve() / "summary.json",
        _batch_summary(result),
    )
    _notify(
        progress_callback,
        "workflow_completed",
        status=result.status,
        output_root=result.output_root,
    )
    return result


__all__ = [
    "CodexJsonRunner",
    "DataQualityBatchResult",
    "DataQualityCancelled",
    "DataQualityCaseResult",
    "DataQualityError",
    "DataQualityRequest",
    "render_audit_markdown",
    "run_data_quality",
]
