#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Private dataset-preparation engine used by :mod:`data_workflow`."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Sequence

try:
    from .workbuddy_batch.dataset import load_cases
except ImportError:  # Allow direct script execution through data_workflow.py.
    from workbuddy_batch.dataset import load_cases


DEFAULT_TASK_TEMPLATE = (
    "请你做一份主题为「{topic}」的战略研究报告，"
    "最终产出文档将直接面向腾讯总办汇报。"
)
DEFAULT_BACKGROUND_TEMPLATE = (
    "围绕「{topic}」开展系统研究，为相关战略判断和业务规划提供事实依据。"
)
DEFAULT_HYPO = "暂无预设假设，请基于素材形成并验证核心判断。"
DEFAULT_MATERIAL_FOCUS = "都是重点素材。"
DEFAULT_REPORT_PAGES = 3
REPORT_CHARS_PER_PAGE = 1000
DEFAULT_FILENAME_REGEX = re.compile(r"^(?P<index>\d+)_(?P<topic>.+)$")
INTAKE_PROMPT_VERSION = "human_report-intake-v2"
INTAKE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "research_background": {"type": "string", "minLength": 1},
        "hypo": {"type": "string", "minLength": 1},
        "hypo_type": {
            "type": "string",
            "enum": ["explicit", "reconstructed", "none"],
        },
        "confidence": {
            "type": "string",
            "enum": ["high", "medium", "low"],
        },
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "page": {"type": "integer", "minimum": 1},
                    "basis": {"type": "string", "minLength": 1},
                },
                "required": ["page", "basis"],
            },
        },
        "leakage_risk": {
            "type": "string",
            "enum": ["low", "medium", "high"],
        },
        "notes": {"type": "string"},
    },
    "required": [
        "research_background",
        "hypo",
        "hypo_type",
        "confidence",
        "evidence",
        "leakage_risk",
        "notes",
    ],
}
INTAKE_PROMPT = """\
你是研究数据标注员，不是报告作者。

请阅读当前工作目录中的 human_report.txt。该文件由一份已经完成的战略研究报告
提取而来，使用 `===== PAGE N =====` 标记原 PDF 页码。你的任务是反向还原
报告在研究启动阶段可能对应的 intake answers，而不是总结最终报告。

当前研究主题：{topic}

请生成：

1. research_background
- 说明为什么开展这项研究、需要解决什么业务或战略问题、服务于什么决策。
- 优先参考标题、项目背景、议程和研究范围。
- 控制在 1—3 句话。
- 不得写入报告的最终结论、结果数据或战略建议。

2. hypo
- 优先提取报告明确表达的研究假设、待验证判断、研究问题或分析框架。
- 不得将最终发现、数据结果、结论或战略建议倒写成事前假设。
- 如果没有明确预设假设，写“暂无明确预设 hypo”，并可补充需要验证的核心问题。
- 如果根据报告结构反向推断，hypo_type 必须是 reconstructed。
- 如果报告明确表述了事前假设，hypo_type 才能是 explicit。
- 如果无法可靠得到假设，hypo_type 使用 none。
- 控制在 1—3 条。
- hypo 字符串内部不要添加“1.”“2.”“3.”等数字编号；多条之间使用分号或换行短句。

3. 审核信息
- evidence 只列出支持研究背景或前置研究问题的关键页码，不摘录最终答案。
- confidence 反映还原可靠程度。
- leakage_risk 评估输出是否可能泄漏 human_report 最终结论。
- notes 简要说明判断边界；没有补充则输出空字符串。

严格按给定 JSON Schema 输出。不要修改任何文件。
"""


class CaseDatasetError(ValueError):
    """Raised when source materials or case datasets are invalid."""


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise CaseDatasetError(f"JSON 解析失败 {path}: {exc}") from exc


def _write_plain_json_atomic(
    path: Path,
    payload: Any,
    *,
    force: bool,
    validator: Any = None,
) -> None:
    path = path.resolve()
    if path.exists() and not force:
        raise CaseDatasetError(f"输出已存在；如需覆盖请添加 --force: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary = Path(handle.name)
    try:
        with handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        if validator is not None:
            validator(temporary)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_json_atomic(path: Path, payload: Any, *, force: bool) -> None:
    _write_plain_json_atomic(
        path,
        payload,
        force=force,
        validator=_validate_dataset_file,
    )


def _validate_dataset_file(path: Path) -> None:
    try:
        cases = load_cases(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise CaseDatasetError(f"生成的数据集无法被 WorkBuddy 读取: {exc}") from exc
    for case in cases:
        for item in case.input_files:
            if not item.source.exists():
                raise CaseDatasetError(
                    f"case {case.case_id} 的输入素材不存在: {item.source}"
                )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative_path(source: Path, base_dir: Path) -> str:
    relative = os.path.relpath(source.resolve(), base_dir.resolve())
    value = Path(relative).as_posix()
    return value if value.startswith(".") else f"./{value}"


def _safe_ascii(value: str) -> str:
    return re.sub(r"[^0-9A-Za-z._-]+", "-", value).strip("._-").lower()


def _stable_token(path: Path) -> str:
    return hashlib.sha256(path.name.encode("utf-8")).hexdigest()[:10]


def _collection_token(value: str) -> str:
    readable = _safe_ascii(value)
    if readable:
        return readable[:40]
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def _format_id(prefix: str, token: str) -> str:
    return f"{prefix.rstrip('-')}-{token}" if prefix else token


def _format_template(template: str, context: dict[str, Any], field: str) -> str:
    try:
        return template.format_map(context).strip()
    except KeyError as exc:
        raise CaseDatasetError(
            f"{field} 模板引用了未知变量: {exc.args[0]}"
        ) from exc


def _load_overrides(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None:
        return {}
    payload = _read_json(path.resolve())
    if not isinstance(payload, dict):
        raise CaseDatasetError("intake overrides 必须是 JSON 对象")
    if "cases" in payload:
        payload = payload["cases"]
        if not isinstance(payload, dict):
            raise CaseDatasetError("intake overrides.cases 必须是 JSON 对象")
    result: dict[str, dict[str, Any]] = {}
    for key, value in payload.items():
        if not isinstance(value, dict):
            raise CaseDatasetError(f"intake override {key!r} 必须是对象")
        result[str(key)] = value
    return result


def _override_for(
    overrides: dict[str, dict[str, Any]],
    *,
    source: Path,
    source_relative: str,
    source_index: int | None,
    case_id: str,
) -> dict[str, Any]:
    keys = [case_id, source_relative, source.name, source.stem]
    if source_index is not None:
        keys.append(str(source_index))
    matches = [(key, overrides[key]) for key in keys if key in overrides]
    if not matches:
        return {}
    first_key, first = matches[0]
    conflicts = [key for key, value in matches[1:] if value != first]
    if conflicts:
        joined = ", ".join([first_key, *conflicts])
        raise CaseDatasetError(
            f"{source.name} 同时命中内容不同的 overrides: {joined}"
        )
    return dict(first)


def _discover_materials(
    materials_dir: Path,
    patterns: Sequence[str],
    *,
    recursive: bool,
    source_kind: str,
) -> list[Path]:
    root = materials_dir.resolve()
    if not root.is_dir():
        raise CaseDatasetError(f"素材目录不存在: {root}")
    found: set[Path] = set()
    for pattern in patterns:
        iterator = root.rglob(pattern) if recursive else root.glob(pattern)
        for path in iterator:
            if source_kind == "file":
                accepted = path.is_file() and not path.name.endswith(".case.json")
            else:
                accepted = path.is_dir()
            if accepted:
                found.add(path.resolve())
    materials = sorted(found, key=lambda item: item.relative_to(root).as_posix())
    if not materials:
        raise CaseDatasetError(
            f"没有找到素材文件: {root}，匹配规则={list(patterns)}"
        )
    return materials


def _source_identity(
    source: Path,
    *,
    filename_regex: re.Pattern[str],
    ordinal: int,
    id_prefix: str,
    openharness_id_prefix: str,
) -> tuple[int | None, str, str, str]:
    match = filename_regex.fullmatch(source.stem)
    if match:
        groups = match.groupdict()
        raw_index = groups.get("index")
        source_index = int(raw_index) if raw_index else None
        topic = str(groups.get("topic") or source.stem).strip()
        supplied_id = str(groups.get("id") or "").strip()
    else:
        source_index = None
        topic = source.stem.strip()
        supplied_id = ""
    if not topic:
        raise CaseDatasetError(f"无法从文件名得到 topic: {source.name}")
    if supplied_id:
        token = _safe_ascii(supplied_id)
    elif source_index is not None:
        token = f"{source_index:03d}"
    else:
        token = _safe_ascii(source.stem) or _stable_token(source)
    if not token:
        token = f"{ordinal:03d}"
    return (
        source_index,
        topic,
        _format_id(id_prefix, token),
        _format_id(openharness_id_prefix, token),
    )


def _intake_values(
    *,
    topic: str,
    override: dict[str, Any],
    mode: str,
    background_template: str,
    hypo_default: str,
    material_focus_default: str,
) -> tuple[str, str, str, str]:
    background = str(override.get("research_background") or "").strip()
    hypo = str(override.get("hypo") or "").strip()
    material_focus = str(
        override.get("material_focus") or material_focus_default
    ).strip()
    supplied = int(bool(background)) + int(bool(hypo))
    if mode == "strict" and supplied < 2:
        missing = []
        if not background:
            missing.append("research_background")
        if not hypo:
            missing.append("hypo")
        raise CaseDatasetError(
            f"topic={topic!r} 缺少严格模式必填项: {', '.join(missing)}"
        )
    if not background:
        if mode == "placeholder":
            background = "TODO：请补充研究背景。"
        else:
            background = _format_template(
                background_template,
                {"topic": topic},
                "research background",
            )
    if not hypo:
        hypo = "TODO：请补充研究 hypo。" if mode == "placeholder" else hypo_default
    if not material_focus:
        raise CaseDatasetError(f"topic={topic!r} 的 material_focus 不能为空")
    declared_status = str(override.get("intake_status") or "").strip()
    if declared_status:
        status = declared_status
    elif supplied == 2:
        status = "reviewed"
    elif supplied == 1:
        status = "partial"
    else:
        status = "placeholder" if mode == "placeholder" else "neutral"
    return background, hypo, material_focus, status


def _delivery_constraints(override: dict[str, Any]) -> dict[str, Any]:
    """Normalize the user-confirmed report length for generated cases."""
    raw_pages = override.get("report_pages", DEFAULT_REPORT_PAGES)
    try:
        max_pages = int(raw_pages)
    except (TypeError, ValueError) as exc:
        raise CaseDatasetError("report_pages 必须是正整数") from exc
    if max_pages < 1:
        raise CaseDatasetError("report_pages 必须是正整数")
    raw_chars = override.get(
        "report_max_chars",
        max_pages * REPORT_CHARS_PER_PAGE,
    )
    try:
        max_chars = int(raw_chars)
    except (TypeError, ValueError) as exc:
        raise CaseDatasetError("report_max_chars 必须是正整数") from exc
    if max_chars < 1:
        raise CaseDatasetError("report_max_chars 必须是正整数")
    return {
        "max_pages": max_pages,
        "max_chars": max_chars,
        "chars_per_page": REPORT_CHARS_PER_PAGE,
        "counting_rule": (
            "中文可见字符；表格单元格文字计入，Markdown 标记和空白不计"
        ),
    }


def _delivery_prompt(constraints: dict[str, Any]) -> str:
    return "4. 报告篇幅：控制在%d页以内。" % constraints["max_pages"]


def build_atomic_cases(
    *,
    materials_dir: Path,
    output_dir: Path | None,
    patterns: Sequence[str],
    recursive: bool,
    source_kind: str,
    overrides_path: Path | None,
    generated_overrides: dict[str, dict[str, Any]] | None,
    intake_mode: str,
    filename_regex: str,
    id_prefix: str | None,
    openharness_id_prefix: str | None,
    split: str,
    skill: str,
    source_collection: str | None,
    task_template: str,
    background_template: str,
    hypo_default: str,
    material_focus_default: str,
) -> list[tuple[Path, dict[str, Any]]]:
    materials_root = materials_dir.resolve()
    destination = (output_dir or materials_root).resolve()
    manual_overrides = _load_overrides(overrides_path)
    generated_overrides = generated_overrides or {}
    try:
        compiled_regex = re.compile(filename_regex)
    except re.error as exc:
        raise CaseDatasetError(f"filename regex 无效: {exc}") from exc
    if "topic" not in compiled_regex.groupindex:
        raise CaseDatasetError("filename regex 必须包含命名分组 (?P<topic>...)")
    materials = _discover_materials(
        materials_root,
        patterns,
        recursive=recursive,
        source_kind=source_kind,
    )
    collection = source_collection or materials_root.name
    collection_id = _collection_token(collection)
    effective_id_prefix = id_prefix or f"case-{collection_id}"
    effective_openharness_id_prefix = (
        openharness_id_prefix or f"rr-{collection_id}"
    )
    seen_case_ids: set[str] = set()
    seen_openharness_ids: set[str] = set()
    outputs: list[tuple[Path, dict[str, Any]]] = []
    for ordinal, source in enumerate(materials, start=1):
        source_index, topic, case_id, openharness_id = _source_identity(
            source,
            filename_regex=compiled_regex,
            ordinal=ordinal,
            id_prefix=effective_id_prefix,
            openharness_id_prefix=effective_openharness_id_prefix,
        )
        inferred_override = _override_for(
            generated_overrides,
            source=source,
            source_relative=source.relative_to(materials_root).as_posix(),
            source_index=source_index,
            case_id=case_id,
        )
        manual_override = _override_for(
            manual_overrides,
            source=source,
            source_relative=source.relative_to(materials_root).as_posix(),
            source_index=source_index,
            case_id=case_id,
        )
        override = {**inferred_override, **manual_override}
        topic = str(override.get("topic") or topic).strip()
        case_id = str(override.get("id") or case_id).strip()
        openharness_id = str(
            override.get("openharness_case_id") or openharness_id
        ).strip()
        case_split = str(override.get("split") or split).strip()
        if not all((topic, case_id, openharness_id, case_split)):
            raise CaseDatasetError(f"{source.name} 生成了空的必填字段")
        if case_id in seen_case_ids:
            raise CaseDatasetError(f"case ID 重复: {case_id}")
        if openharness_id in seen_openharness_ids:
            raise CaseDatasetError(
                f"OpenHarness case ID 重复: {openharness_id}"
            )
        seen_case_ids.add(case_id)
        seen_openharness_ids.add(openharness_id)
        background, hypo, material_focus, intake_status = _intake_values(
            topic=topic,
            override=override,
            mode=intake_mode,
            background_template=background_template,
            hypo_default=hypo_default,
            material_focus_default=material_focus_default,
        )
        delivery_constraints = _delivery_constraints(override)
        metadata = {
            "openharness_case_id": openharness_id,
            "split": case_split,
            "topic": topic,
            "source_collection": collection,
            "source_file": source.name,
            "source_kind": source_kind,
            "intake_status": intake_status,
        }
        if source_index is not None:
            metadata["source_index"] = source_index
        custom_metadata = override.get("metadata", {})
        if custom_metadata:
            if not isinstance(custom_metadata, dict):
                raise CaseDatasetError(
                    f"{source.name} 的 override.metadata 必须是对象"
                )
            metadata.update(custom_metadata)
        output_name = f"{source.stem}.case.json"
        output_path = destination / source.relative_to(materials_root).parent / output_name
        context = {
            "topic": topic,
            "case_id": case_id,
            "source_file": source.name,
            "source_collection": collection,
        }
        task_prompt = _format_template(task_template, context, "task")
        intake_prompt = (
            f"1. 研究背景：{background.replace(chr(10), chr(10) + '   ')}\n"
            f"2. hypo：{hypo.replace(chr(10), chr(10) + '   ')}\n"
            f"3. 素材重点分布："
            f"{material_focus.replace(chr(10), chr(10) + '   ')}"
        )
        payload = {
            "defaults": {"skills": [skill]} if skill else {},
            "cases": [
                {
                    "id": case_id,
                    "metadata": metadata,
                    "delivery_constraints": delivery_constraints,
                    "input_files": [
                        {
                            "source": _relative_path(source, output_path.parent),
                            "target": (
                                f"materials/{source.name}"
                                if source_kind == "file"
                                else "materials"
                            ),
                        }
                    ],
                    "turns": [
                        {
                            "round": 0,
                            "label": "task",
                            "prompt": task_prompt,
                        },
                        {
                            "round": 1,
                            "label": "intake_answers",
                            "prompt": intake_prompt + "\n" + _delivery_prompt(
                                delivery_constraints
                            ),
                        },
                    ],
                }
            ],
        }
        outputs.append((output_path, payload))
    return outputs


def _materialize_defaults(
    defaults: dict[str, Any],
    raw_case: dict[str, Any],
) -> dict[str, Any]:
    result = dict(raw_case)
    for key, value in defaults.items():
        if key in {"id", "case_id"}:
            continue
        if key not in result or result[key] in (None, ""):
            result[key] = value
    return result


def _rebase_input_files(
    case: dict[str, Any],
    *,
    source_dataset: Path,
    output_dataset: Path,
) -> None:
    values = case.get("input_files", [])
    if values in (None, ""):
        return
    if not isinstance(values, list):
        values = [values]
    rebased: list[Any] = []
    for value in values:
        if isinstance(value, dict):
            item = dict(value)
            if not item.get("source"):
                raise CaseDatasetError(
                    f"{source_dataset} 中 input_files.source 为空"
                )
            source = Path(str(item["source"])).expanduser()
            if not source.is_absolute():
                source = (source_dataset.parent / source).resolve()
            item["source"] = _relative_path(source, output_dataset.parent)
            rebased.append(item)
        else:
            source = Path(str(value)).expanduser()
            if not source.is_absolute():
                source = (source_dataset.parent / source).resolve()
            rebased.append(_relative_path(source, output_dataset.parent))
    case["input_files"] = rebased


def _load_dataset_payload(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    payload = _read_json(path)
    if isinstance(payload, list):
        defaults: dict[str, Any] = {}
        cases = payload
    elif isinstance(payload, dict) and isinstance(payload.get("cases"), list):
        defaults = payload.get("defaults", {})
        cases = payload["cases"]
    else:
        raise CaseDatasetError(
            f"{path} 必须是 case 数组，或包含 cases 数组的对象"
        )
    if not isinstance(defaults, dict):
        raise CaseDatasetError(f"{path} 的 defaults 必须是对象")
    if not all(isinstance(item, dict) for item in cases):
        raise CaseDatasetError(f"{path} 的 cases 必须全部是对象")
    return defaults, cases


def _manifest_paths(path: Path) -> list[Path]:
    result: list[Path] = []
    for line_number, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(),
        start=1,
    ):
        value = line.strip()
        if not value or value.startswith("#"):
            continue
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            candidate = path.parent / candidate
        if not candidate.is_file():
            raise CaseDatasetError(
                f"manifest {path} 第 {line_number} 行文件不存在: {candidate}"
            )
        result.append(candidate.resolve())
    return result


def discover_case_files(
    inputs: Sequence[Path],
    manifests: Sequence[Path],
    pattern: str,
) -> list[Path]:
    found: set[Path] = set()
    for supplied in inputs:
        path = supplied.expanduser().resolve()
        if path.is_file():
            found.add(path)
        elif path.is_dir():
            found.update(item.resolve() for item in path.rglob(pattern) if item.is_file())
        else:
            raise CaseDatasetError(f"输入路径不存在: {path}")
    for manifest in manifests:
        found.update(_manifest_paths(manifest.expanduser().resolve()))
    files = sorted(found, key=lambda item: item.as_posix())
    if not files:
        raise CaseDatasetError("没有找到可合并的 case JSON")
    return files


def _parse_include(value: str | None) -> set[int] | None:
    if not value:
        return None
    result: set[int] = set()
    for part in value.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            try:
                start, end = int(start_text), int(end_text)
            except ValueError as exc:
                raise CaseDatasetError(f"--include 范围无效: {token}") from exc
            if start > end:
                raise CaseDatasetError(f"--include 范围起点大于终点: {token}")
            result.update(range(start, end + 1))
        else:
            try:
                result.add(int(token))
            except ValueError as exc:
                raise CaseDatasetError(f"--include 编号无效: {token}") from exc
    return result


def _resolve_field(value: dict[str, Any], dotted_key: str) -> Any:
    current: Any = value
    for part in dotted_key.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _parse_filters(values: Sequence[str]) -> list[tuple[str, str]]:
    result = []
    for value in values:
        if "=" not in value:
            raise CaseDatasetError(f"--filter 必须是 KEY=VALUE: {value}")
        key, expected = value.split("=", 1)
        if not key.strip():
            raise CaseDatasetError(f"--filter 的 KEY 不能为空: {value}")
        result.append((key.strip(), expected))
    return result


def merge_case_datasets(
    *,
    case_files: Sequence[Path],
    output: Path,
    include: set[int] | None,
    filters: Sequence[tuple[str, str]],
) -> dict[str, Any]:
    output = output.resolve()
    merged: list[dict[str, Any]] = []
    seen_case_ids: dict[str, Path] = {}
    seen_openharness_ids: dict[str, Path] = {}
    for case_file in case_files:
        defaults, raw_cases = _load_dataset_payload(case_file)
        for raw_case in raw_cases:
            case = _materialize_defaults(defaults, raw_case)
            metadata = case.get("metadata", {})
            metadata = metadata if isinstance(metadata, dict) else {}
            if include is not None:
                try:
                    source_index = int(metadata.get("source_index"))
                except (TypeError, ValueError):
                    continue
                if source_index not in include:
                    continue
            if any(
                str(_resolve_field(case, key)) != expected
                for key, expected in filters
            ):
                continue
            case_id = str(case.get("id", case.get("case_id", ""))).strip()
            if not case_id:
                raise CaseDatasetError(f"{case_file} 中存在无 ID 的 case")
            openharness_id = str(metadata.get("openharness_case_id") or case_id)
            if case_id in seen_case_ids:
                raise CaseDatasetError(
                    f"case ID 重复: {case_id}，来源为 "
                    f"{seen_case_ids[case_id]} 和 {case_file}"
                )
            if openharness_id in seen_openharness_ids:
                raise CaseDatasetError(
                    f"OpenHarness case ID 重复: {openharness_id}，来源为 "
                    f"{seen_openharness_ids[openharness_id]} 和 {case_file}"
                )
            seen_case_ids[case_id] = case_file
            seen_openharness_ids[openharness_id] = case_file
            _rebase_input_files(
                case,
                source_dataset=case_file,
                output_dataset=output,
            )
            merged.append(case)
    if not merged:
        raise CaseDatasetError("筛选后没有可输出的 case")
    def sort_key(item: dict[str, Any]) -> tuple[str, int, str]:
        raw_index = _resolve_field(item, "metadata.source_index")
        try:
            source_index = int(raw_index)
        except (TypeError, ValueError):
            source_index = sys.maxsize
        return (
            str(_resolve_field(item, "metadata.source_collection") or ""),
            source_index,
            str(item.get("id", item.get("case_id", ""))),
        )

    merged.sort(key=sort_key)
    return {"cases": merged}


def _validate_intake_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CaseDatasetError("Codex intake 输出必须是 JSON 对象")
    required_strings = {
        "research_background",
        "hypo",
        "hypo_type",
        "confidence",
        "leakage_risk",
        "notes",
    }
    missing = sorted(set(INTAKE_OUTPUT_SCHEMA["required"]) - set(value))
    if missing:
        raise CaseDatasetError(f"Codex intake 输出缺少字段: {', '.join(missing)}")
    for key in required_strings:
        if not isinstance(value.get(key), str):
            raise CaseDatasetError(f"Codex intake 字段 {key} 必须是字符串")
    if not value["research_background"].strip() or not value["hypo"].strip():
        raise CaseDatasetError("Codex intake 的 research_background/hypo 不能为空")
    allowed = {
        "hypo_type": {"explicit", "reconstructed", "none"},
        "confidence": {"high", "medium", "low"},
        "leakage_risk": {"low", "medium", "high"},
    }
    for key, choices in allowed.items():
        if value[key] not in choices:
            raise CaseDatasetError(
                f"Codex intake 字段 {key} 值无效: {value[key]!r}"
            )
    evidence = value.get("evidence")
    if not isinstance(evidence, list):
        raise CaseDatasetError("Codex intake 字段 evidence 必须是数组")
    for index, item in enumerate(evidence, start=1):
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("page"), int)
            or item["page"] < 1
            or not isinstance(item.get("basis"), str)
            or not item["basis"].strip()
        ):
            raise CaseDatasetError(f"Codex intake evidence 第 {index} 项无效")
    return {
        "research_background": value["research_background"].strip(),
        "hypo": value["hypo"].strip(),
        "hypo_type": value["hypo_type"],
        "confidence": value["confidence"],
        "evidence": evidence,
        "leakage_risk": value["leakage_risk"],
        "notes": value["notes"].strip(),
    }


def _extract_pdf_text(
    pdf: Path,
    *,
    pdftotext_cli: str,
    timeout_seconds: float,
    minimum_characters: int,
) -> tuple[str, int]:
    try:
        completed = subprocess.run(
            [pdftotext_cli, "-layout", str(pdf), "-"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        raise CaseDatasetError(f"找不到 pdftotext: {pdftotext_cli}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CaseDatasetError(f"PDF 文本提取超时: {pdf}") from exc
    if completed.returncode != 0:
        error = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CaseDatasetError(f"PDF 文本提取失败 {pdf}: {error}")
    raw_text = completed.stdout.decode("utf-8", errors="replace").replace("\x00", "")
    pages = raw_text.split("\f")
    if pages and not pages[-1].strip():
        pages.pop()
    numbered = "\n\n".join(
        f"===== PAGE {index} =====\n{page.strip()}"
        for index, page in enumerate(pages, start=1)
    )
    meaningful = re.sub(r"\s+", "", raw_text)
    if len(meaningful) < minimum_characters:
        raise CaseDatasetError(
            f"PDF 可提取文本过少: {pdf}，仅 {len(meaningful)} 个非空白字符"
        )
    return numbered, len(pages)


def _natural_number(value: str) -> int:
    match = re.search(r"(\d+)", value)
    return int(match.group(1)) if match else sys.maxsize


def _extract_pptx_text(
    pptx: Path,
    *,
    minimum_characters: int,
) -> tuple[str, int]:
    try:
        with zipfile.ZipFile(pptx) as archive:
            slide_names = sorted(
                (
                    name
                    for name in archive.namelist()
                    if re.fullmatch(r"ppt/slides/slide\d+\.xml", name)
                ),
                key=_natural_number,
            )
            slides: list[str] = []
            for slide_name in slide_names:
                root = ET.fromstring(archive.read(slide_name))
                texts = [
                    element.text.strip()
                    for element in root.iter()
                    if element.tag.endswith("}t")
                    and element.text
                    and element.text.strip()
                ]
                slides.append("\n".join(texts))
    except (OSError, zipfile.BadZipFile, ET.ParseError) as exc:
        raise CaseDatasetError(f"PPTX 文本提取失败 {pptx}: {exc}") from exc
    raw_text = "\n".join(slides)
    meaningful = re.sub(r"\s+", "", raw_text)
    if len(meaningful) < minimum_characters:
        raise CaseDatasetError(
            f"PPTX 可提取文本过少: {pptx}，仅 {len(meaningful)} 个非空白字符"
        )
    numbered = "\n\n".join(
        f"===== PAGE {index} =====\n{text}"
        for index, text in enumerate(slides, start=1)
    )
    return numbered, len(slides)


def _extract_human_report_text(
    path: Path,
    *,
    pdftotext_cli: str,
    timeout_seconds: float,
    minimum_characters: int,
) -> tuple[str, int]:
    if path.suffix.lower() == ".pdf":
        return _extract_pdf_text(
            path,
            pdftotext_cli=pdftotext_cli,
            timeout_seconds=timeout_seconds,
            minimum_characters=minimum_characters,
        )
    if path.suffix.lower() == ".pptx":
        return _extract_pptx_text(
            path,
            minimum_characters=minimum_characters,
        )
    raise CaseDatasetError(f"不支持的 human_report 格式: {path}")


def _human_report_map(
    root: Path,
    extensions: Sequence[str],
) -> dict[str, Path]:
    human_report_root = root.resolve()
    if not human_report_root.is_dir():
        raise CaseDatasetError(f"human_report 目录不存在: {human_report_root}")
    normalized = {
        item.lower() if item.startswith(".") else f".{item.lower()}"
        for item in extensions
    }
    result: dict[str, Path] = {}
    for path in human_report_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in normalized:
            continue
        key = path.relative_to(human_report_root).with_suffix("").as_posix()
        if key in result:
            raise CaseDatasetError(
                f"human_report stem 重复: {key}，文件为 {result[key]} 和 {path}"
            )
        result[key] = path.resolve()
    if not result:
        raise CaseDatasetError(
            f"human_report 目录中没有匹配扩展名 {sorted(normalized)} 的文件"
        )
    return result


def _inference_jobs(
    outputs: Sequence[tuple[Path, dict[str, Any]]],
    *,
    materials_root: Path,
    human_report_root: Path,
    extensions: Sequence[str],
) -> list[dict[str, Any]]:
    human_report = _human_report_map(human_report_root, extensions)
    jobs: list[dict[str, Any]] = []
    missing: list[str] = []
    for output_path, payload in outputs:
        case = payload["cases"][0]
        source_value = case["input_files"][0]["source"]
        source = (output_path.parent / source_value).resolve()
        if not source.is_file():
            raise CaseDatasetError(
                "human_report intake 推断当前只支持“一文件一 case”"
            )
        relative = source.relative_to(materials_root.resolve())
        key = relative.with_suffix("").as_posix()
        matched = human_report.get(key)
        if matched is None:
            missing.append(relative.as_posix())
            continue
        jobs.append(
            {
                "case_id": str(case["id"]),
                "topic": str(case["metadata"]["topic"]),
                "source": source,
                "source_relative": relative.as_posix(),
                "human_report": matched,
                "human_report_relative": matched.relative_to(
                    human_report_root.resolve()
                ).as_posix(),
            }
        )
    if missing:
        preview = "\n".join(f"  - {item}" for item in missing[:20])
        suffix = f"\n  ... 另有 {len(missing) - 20} 个" if len(missing) > 20 else ""
        raise CaseDatasetError(
            f"{len(missing)} 个素材缺少同相对路径、同 stem 的 human_report:\n"
            f"{preview}{suffix}"
        )
    return jobs


def _codex_version(codex_cli: str) -> str:
    try:
        completed = subprocess.run(
            [codex_cli, "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise CaseDatasetError(f"无法执行 Codex CLI: {codex_cli}") from exc
    if completed.returncode != 0:
        raise CaseDatasetError(
            f"Codex CLI --version 失败: {completed.stderr.strip()}"
        )
    return completed.stdout.strip() or completed.stderr.strip() or "unknown"


def _run_codex_intake(
    job: dict[str, Any],
    *,
    codex_cli: str,
    codex_model: str | None,
    codex_ignore_user_config: bool,
    pdftotext_cli: str,
    pdf_timeout_seconds: float,
    codex_timeout_seconds: float,
    minimum_characters: int,
    retries: int,
    codex_version: str,
) -> dict[str, Any]:
    started = time.monotonic()
    pdf = Path(job["human_report"])
    pdf_hash = _sha256_file(pdf)
    text, page_count = _extract_human_report_text(
        pdf,
        pdftotext_cli=pdftotext_cli,
        timeout_seconds=pdf_timeout_seconds,
        minimum_characters=minimum_characters,
    )
    prompt = INTAKE_PROMPT.format(topic=job["topic"])
    last_error = ""
    stdout_tail = ""
    stderr_tail = ""
    for attempt in range(1, retries + 2):
        with tempfile.TemporaryDirectory(
            prefix=f"intake-{_safe_ascii(job['case_id'])}-"
        ) as temporary_dir:
            workspace = Path(temporary_dir)
            (workspace / "human_report.txt").write_text(text, encoding="utf-8")
            schema_path = workspace / "output_schema.json"
            schema_path.write_text(
                json.dumps(INTAKE_OUTPUT_SCHEMA, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            result_path = workspace / "result.json"
            command = [
                codex_cli,
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--skip-git-repo-check",
                "--color",
                "never",
                "-c",
                'approval_policy="never"',
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(result_path),
                "-C",
                str(workspace),
            ]
            if codex_ignore_user_config:
                command.append("--ignore-user-config")
            if codex_model:
                command.extend(["--model", codex_model])
            command.append("-")
            try:
                completed = subprocess.run(
                    command,
                    input=prompt,
                    text=True,
                    capture_output=True,
                    timeout=codex_timeout_seconds,
                )
                stdout_tail = completed.stdout[-20000:]
                stderr_tail = completed.stderr[-20000:]
                if completed.returncode != 0:
                    raise CaseDatasetError(
                        f"Codex CLI exit={completed.returncode}: "
                        f"{completed.stderr.strip()[-2000:]}"
                    )
                if not result_path.is_file():
                    raise CaseDatasetError("Codex CLI 未生成 result.json")
                parsed = _validate_intake_result(_read_json(result_path))
                return {
                    "status": "success",
                    "research_background": parsed["research_background"],
                    "hypo": parsed["hypo"],
                    "material_focus": DEFAULT_MATERIAL_FOCUS,
                    "intake_status": "codex_inferred",
                    "metadata": {
                        "intake_source": "human_report_codex",
                        "human_report_file": job["human_report_relative"],
                        "hypo_type": parsed["hypo_type"],
                        "intake_confidence": parsed["confidence"],
                        "intake_leakage_risk": parsed["leakage_risk"],
                    },
                    "_inference": {
                        "status": "success",
                        "prompt_version": INTAKE_PROMPT_VERSION,
                        "prompt_sha256": hashlib.sha256(
                            prompt.encode("utf-8")
                        ).hexdigest(),
                        "human_report_file": job["human_report_relative"],
                        "human_report_sha256": pdf_hash,
                        "human_report_pages": page_count,
                        "codex_cli_version": codex_version,
                        "codex_model": codex_model or "<configured-default>",
                        "attempts": attempt,
                        "duration_ms": int((time.monotonic() - started) * 1000),
                        "hypo_type": parsed["hypo_type"],
                        "confidence": parsed["confidence"],
                        "evidence": parsed["evidence"],
                        "leakage_risk": parsed["leakage_risk"],
                        "notes": parsed["notes"],
                        "stdout_tail": stdout_tail,
                        "stderr_tail": stderr_tail,
                    },
                }
            except (
                CaseDatasetError,
                OSError,
                subprocess.TimeoutExpired,
            ) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                if attempt > retries:
                    break
    return {
        "status": "failed",
        "_inference": {
            "status": "failed",
            "prompt_version": INTAKE_PROMPT_VERSION,
            "human_report_file": job["human_report_relative"],
            "human_report_sha256": pdf_hash,
            "codex_cli_version": codex_version,
            "codex_model": codex_model or "<configured-default>",
            "attempts": retries + 1,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "error": last_error,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        },
    }


def _load_inference_cache(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "_meta": {
                "schema_version": 1,
                "prompt_version": INTAKE_PROMPT_VERSION,
            },
            "cases": {},
        }
    payload = _read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), dict):
        raise CaseDatasetError(f"Codex intake cache 格式无效: {path}")
    return payload


def _cache_entry_valid(
    entry: Any,
    *,
    job: dict[str, Any],
    codex_model: str | None,
    codex_version: str,
) -> bool:
    if not isinstance(entry, dict) or entry.get("status") != "success":
        return False
    inference = entry.get("_inference")
    if not isinstance(inference, dict) or inference.get("status") != "success":
        return False
    return bool(
        inference.get("prompt_version") == INTAKE_PROMPT_VERSION
        and inference.get("human_report_sha256")
        == _sha256_file(Path(job["human_report"]))
        and inference.get("codex_model")
        == (codex_model or "<configured-default>")
        and inference.get("codex_cli_version") == codex_version
    )


def infer_intake_overrides(
    *,
    jobs: Sequence[dict[str, Any]],
    cache_path: Path,
    codex_cli: str,
    codex_model: str | None,
    codex_ignore_user_config: bool,
    pdftotext_cli: str,
    pdf_timeout_seconds: float,
    codex_timeout_seconds: float,
    minimum_characters: int,
    retries: int,
    parallel: int,
    refresh: bool,
) -> dict[str, dict[str, Any]]:
    codex_version = _codex_version(codex_cli)
    cache = _load_inference_cache(cache_path)
    cached_cases = cache["cases"]
    pending: list[dict[str, Any]] = []
    reused = 0
    for job in jobs:
        entry = cached_cases.get(job["case_id"])
        if not refresh and _cache_entry_valid(
            entry,
            job=job,
            codex_model=codex_model,
            codex_version=codex_version,
        ):
            reused += 1
        else:
            pending.append(job)
    print(
        f"[Codex intake] total={len(jobs)} cached={reused} pending={len(pending)} "
        f"parallel={parallel}",
        flush=True,
    )
    write_lock = threading.Lock()
    completed_count = 0

    def persist() -> None:
        cache["_meta"] = {
            "schema_version": 1,
            "prompt_version": INTAKE_PROMPT_VERSION,
            "codex_cli_version": codex_version,
            "codex_model": codex_model or "<configured-default>",
        }
        _write_plain_json_atomic(cache_path, cache, force=True)

    if pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
            future_map = {
                executor.submit(
                    _run_codex_intake,
                    job,
                    codex_cli=codex_cli,
                    codex_model=codex_model,
                    codex_ignore_user_config=codex_ignore_user_config,
                    pdftotext_cli=pdftotext_cli,
                    pdf_timeout_seconds=pdf_timeout_seconds,
                    codex_timeout_seconds=codex_timeout_seconds,
                    minimum_characters=minimum_characters,
                    retries=retries,
                    codex_version=codex_version,
                ): job
                for job in pending
            }
            for future in concurrent.futures.as_completed(future_map):
                job = future_map[future]
                try:
                    result = future.result()
                except Exception as exc:  # preserve other completed inferences
                    result = {
                        "status": "failed",
                        "_inference": {
                            "status": "failed",
                            "prompt_version": INTAKE_PROMPT_VERSION,
                            "human_report_file": job["human_report_relative"],
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    }
                with write_lock:
                    cached_cases[job["case_id"]] = result
                    completed_count += 1
                    persist()
                    print(
                        f"[Codex intake {completed_count}/{len(pending)}] "
                        f"case={job['case_id']} status={result['status']}",
                        flush=True,
                    )
    failures = [
        case_id
        for case_id, value in cached_cases.items()
        if case_id in {job["case_id"] for job in jobs}
        and (
            not isinstance(value, dict)
            or value.get("status") != "success"
        )
    ]
    if failures:
        preview = ", ".join(failures[:10])
        suffix = " ..." if len(failures) > 10 else ""
        raise CaseDatasetError(
            f"{len(failures)} 个 case 的 Codex intake 推断失败: "
            f"{preview}{suffix}；成功结果已保存到 {cache_path}"
        )
    return {
        job["case_id"]: dict(cached_cases[job["case_id"]])
        for job in jobs
    }


def _discover_projects(
    projects_dir: Path,
    *,
    filename_regex: str,
    id_prefix: str,
    openharness_id_prefix: str,
    human_report_extensions: Sequence[str],
) -> list[dict[str, Any]]:
    root = projects_dir.resolve()
    if not root.is_dir():
        raise CaseDatasetError(f"项目目录不存在: {root}")
    try:
        compiled_regex = re.compile(filename_regex)
    except re.error as exc:
        raise CaseDatasetError(f"project filename regex 无效: {exc}") from exc
    if "topic" not in compiled_regex.groupindex:
        raise CaseDatasetError(
            "project filename regex 必须包含命名分组 (?P<topic>...)"
        )
    extensions = {
        item.lower() if item.startswith(".") else f".{item.lower()}"
        for item in human_report_extensions
    }
    projects: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for ordinal, project in enumerate(
        sorted(
            (
                path
                for path in root.iterdir()
                if path.is_dir() and not path.name.startswith(".")
            ),
            key=lambda item: item.name,
        ),
        start=1,
    ):
        source_dirs = [
            path
            for path in project.iterdir()
            if path.is_dir() and not path.name.startswith(".")
        ]
        if len(source_dirs) != 1:
            raise CaseDatasetError(
                f"项目 {project.name} 应有且仅有一个素材目录，实际为 "
                f"{[item.name for item in source_dirs]}"
            )
        human_report_files = [
            path
            for path in project.iterdir()
            if path.is_file()
            and not path.name.startswith(".")
            and path.suffix.lower() in extensions
        ]
        if len(human_report_files) != 1:
            raise CaseDatasetError(
                f"项目 {project.name} 应有且仅有一个 human_report "
                f"{sorted(extensions)}，实际为 "
                f"{[item.name for item in human_report_files]}"
            )
        source_index, topic, case_id, openharness_id = _source_identity(
            project,
            filename_regex=compiled_regex,
            ordinal=ordinal,
            id_prefix=id_prefix,
            openharness_id_prefix=openharness_id_prefix,
        )
        if case_id in seen_case_ids:
            raise CaseDatasetError(f"项目 case ID 重复: {case_id}")
        seen_case_ids.add(case_id)
        projects.append(
            {
                "project": project.resolve(),
                "project_name": project.name,
                "source": source_dirs[0].resolve(),
                "human_report": human_report_files[0].resolve(),
                "source_index": source_index,
                "topic": topic,
                "case_id": case_id,
                "openharness_case_id": openharness_id,
            }
        )
    if not projects:
        raise CaseDatasetError(f"项目目录中没有 case: {root}")
    return projects


def _copy_project_source(source: Path, destination: Path) -> None:
    def ignore(_directory: str, names: list[str]) -> set[str]:
        return {
            name
            for name in names
            if name.startswith(".") or name.startswith("~$")
        }

    def copy_writable(source_file: str, destination_file: str) -> str:
        source_path = Path(source_file)
        destination_path = Path(destination_file)
        if destination_path.exists():
            if (
                source_path.stat().st_size == destination_path.stat().st_size
                and _sha256_file(source_path) == _sha256_file(destination_path)
            ):
                return str(destination_path)
            destination_path.chmod(
                destination_path.stat().st_mode | stat.S_IWUSR
            )
        shutil.copy2(source_path, destination_path)
        destination_path.chmod(
            destination_path.stat().st_mode | stat.S_IWUSR
        )
        return str(destination_path)

    shutil.copytree(
        source,
        destination,
        dirs_exist_ok=True,
        ignore=ignore,
        copy_function=copy_writable,
    )


def _project_case_payload(
    project: dict[str, Any],
    *,
    output_path: Path,
    source_collection: str,
    split: str,
    skill: str,
    task_template: str,
    background_template: str,
    hypo_default: str,
    material_focus_default: str,
    intake_mode: str,
    manual_overrides: dict[str, dict[str, Any]],
    generated_overrides: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    inferred_override = _override_for(
        generated_overrides,
        source=Path(project["project"]),
        source_relative=project["project_name"],
        source_index=project["source_index"],
        case_id=project["case_id"],
    )
    manual_override = _override_for(
        manual_overrides,
        source=Path(project["project"]),
        source_relative=project["project_name"],
        source_index=project["source_index"],
        case_id=project["case_id"],
    )
    override = {**inferred_override, **manual_override}
    topic = str(override.get("topic") or project["topic"]).strip()
    case_id = str(override.get("id") or project["case_id"]).strip()
    openharness_id = str(
        override.get("openharness_case_id")
        or project["openharness_case_id"]
    ).strip()
    case_split = str(override.get("split") or split).strip()
    background, hypo, material_focus, intake_status = _intake_values(
        topic=topic,
        override=override,
        mode=intake_mode,
        background_template=background_template,
        hypo_default=hypo_default,
        material_focus_default=material_focus_default,
    )
    delivery_constraints = _delivery_constraints(override)
    metadata = {
        "openharness_case_id": openharness_id,
        "split": case_split,
        "topic": topic,
        "source_collection": source_collection,
        "source_file": project["project_name"],
        "source_kind": "directory",
        "case_type": "real_project",
        "intake_status": intake_status,
    }
    if project["source_index"] is not None:
        metadata["source_index"] = project["source_index"]
    custom_metadata = override.get("metadata", {})
    if custom_metadata:
        if not isinstance(custom_metadata, dict):
            raise CaseDatasetError(
                f"{project['project_name']} 的 override.metadata 必须是对象"
            )
        metadata.update(custom_metadata)
    context = {
        "topic": topic,
        "case_id": case_id,
        "source_file": project["project_name"],
        "source_collection": source_collection,
    }
    return {
        "defaults": {"skills": [skill]} if skill else {},
        "cases": [
            {
                "id": case_id,
                "metadata": metadata,
                "delivery_constraints": delivery_constraints,
                "input_files": [
                    {
                        "source": "./source",
                        "target": "materials",
                    }
                ],
                "turns": [
                    {
                        "round": 0,
                        "label": "task",
                        "prompt": _format_template(
                            task_template,
                            context,
                            "task",
                        ),
                    },
                    {
                        "round": 1,
                        "label": "intake_answers",
                        "prompt": (
                            f"1. 研究背景："
                            f"{background.replace(chr(10), chr(10) + '   ')}\n"
                            f"2. hypo："
                            f"{hypo.replace(chr(10), chr(10) + '   ')}\n"
                            f"3. 素材重点分布："
                            f"{material_focus.replace(chr(10), chr(10) + '   ')}\n"
                            f"{_delivery_prompt(delivery_constraints)}"
                        ),
                    },
                ],
            }
        ],
    }


def _generate_projects_command(args: argparse.Namespace) -> int:
    projects = _discover_projects(
        args.projects_dir,
        filename_regex=args.filename_regex,
        id_prefix=args.id_prefix,
        openharness_id_prefix=args.openharness_id_prefix,
        human_report_extensions=args.human_report_extension,
    )
    output_root = args.output_dir.resolve()
    manual_overrides = _load_overrides(args.intake_overrides)
    output_paths = {
        project["case_id"]: (
            output_root
            / project["project_name"]
            / f"{project['project_name']}.case.json"
        )
        for project in projects
    }
    existing = [path for path in output_paths.values() if path.exists()]
    if existing and not args.force and not args.dry_run:
        raise CaseDatasetError(
            f"{len(existing)} 个项目 case 已存在；如需覆盖请添加 --force"
        )
    print(
        f"[项目发现] projects={len(projects)} "
        f"pdf={sum(Path(item['human_report']).suffix.lower() == '.pdf' for item in projects)} "
        f"pptx={sum(Path(item['human_report']).suffix.lower() == '.pptx' for item in projects)}",
        flush=True,
    )
    if args.dry_run:
        for project in projects:
            print(
                f"{project['case_id']} | {project['project_name']} | "
                f"source={Path(project['source']).name} | "
                f"human_report={Path(project['human_report']).name}"
            )
        return 0
    for project in projects:
        project_output = output_root / project["project_name"]
        project_output.mkdir(parents=True, exist_ok=True)
        _copy_project_source(
            Path(project["source"]),
            project_output / "source",
        )
    codex_cli = args.codex_cli or shutil.which("codex")
    if not codex_cli:
        raise CaseDatasetError("找不到 Codex CLI；请设置 --codex-cli")
    pdftotext_cli = args.pdftotext_cli or shutil.which("pdftotext")
    if not pdftotext_cli:
        raise CaseDatasetError(
            "找不到 pdftotext；请安装 Poppler 或设置 --pdftotext-cli"
        )
    jobs = [
        {
            "case_id": project["case_id"],
            "topic": project["topic"],
            "source": project["source"],
            "source_relative": project["project_name"],
            "human_report": project["human_report"],
            "human_report_relative": (
                f"{project['project_name']}/"
                f"{Path(project['human_report']).name}"
            ),
        }
        for project in projects
    ]
    cache_path = (
        args.intake_cache
        or output_root / "intake_answers.codex.json"
    ).resolve()
    generated_overrides = infer_intake_overrides(
        jobs=jobs,
        cache_path=cache_path,
        codex_cli=str(codex_cli),
        codex_model=args.codex_model,
        codex_ignore_user_config=args.codex_ignore_user_config,
        pdftotext_cli=str(pdftotext_cli),
        pdf_timeout_seconds=args.pdf_timeout,
        codex_timeout_seconds=args.codex_timeout,
        minimum_characters=args.minimum_extracted_characters,
        retries=args.codex_retries,
        parallel=args.codex_parallel,
        refresh=args.refresh_intake,
    )
    for project in projects:
        output_path = output_paths[project["case_id"]]
        payload = _project_case_payload(
            project,
            output_path=output_path,
            source_collection=args.source_collection,
            split=args.split,
            skill=args.skill,
            task_template=args.task_template,
            background_template=args.background_template,
            hypo_default=args.hypo_default,
            material_focus_default=args.material_focus_default,
            intake_mode="strict",
            manual_overrides=manual_overrides,
            generated_overrides=generated_overrides,
        )
        _write_json_atomic(output_path, payload, force=args.force)
    print(
        f"已生成 {len(projects)} 个真实项目 case 到 {output_root}",
        flush=True,
    )
    return 0


def _generate_command(args: argparse.Namespace) -> int:
    base_outputs = build_atomic_cases(
        materials_dir=args.materials_dir,
        output_dir=args.output_dir,
        patterns=args.material_glob,
        recursive=args.recursive,
        source_kind=args.source_kind,
        overrides_path=args.intake_overrides,
        generated_overrides=None,
        intake_mode=("neutral" if args.human_report_dir else args.intake_mode),
        filename_regex=args.filename_regex,
        id_prefix=args.id_prefix,
        openharness_id_prefix=args.openharness_id_prefix,
        split=args.split,
        skill=args.skill,
        source_collection=args.source_collection,
        task_template=args.task_template,
        background_template=args.background_template,
        hypo_default=args.hypo_default,
        material_focus_default=args.material_focus_default,
    )
    existing = [path for path, _ in base_outputs if path.exists()]
    if existing and not args.force and not args.dry_run:
        preview = "\n".join(f"  - {path}" for path in existing[:10])
        suffix = f"\n  ... 另有 {len(existing) - 10} 个" if len(existing) > 10 else ""
        raise CaseDatasetError(
            f"{len(existing)} 个输出已存在；如需覆盖请添加 --force:\n"
            f"{preview}{suffix}"
        )
    generated_overrides: dict[str, dict[str, Any]] | None = None
    if args.human_report_dir:
        jobs = _inference_jobs(
            base_outputs,
            materials_root=args.materials_dir,
            human_report_root=args.human_report_dir,
            extensions=args.human_report_extension,
        )
        print(
            f"[human_report 配对] materials={len(base_outputs)} matched={len(jobs)}",
            flush=True,
        )
        if not args.dry_run:
            codex_cli = args.codex_cli or shutil.which("codex")
            if not codex_cli:
                raise CaseDatasetError(
                    "找不到 Codex CLI；请设置 --codex-cli"
                )
            pdftotext_cli = args.pdftotext_cli or shutil.which("pdftotext")
            if not pdftotext_cli:
                raise CaseDatasetError(
                    "找不到 pdftotext；请安装 Poppler 或设置 --pdftotext-cli"
                )
            cache_path = (
                args.intake_cache
                or args.human_report_dir.resolve().parent
                / "intake_answers.codex.json"
            )
            generated_overrides = infer_intake_overrides(
                jobs=jobs,
                cache_path=cache_path.resolve(),
                codex_cli=str(codex_cli),
                codex_model=args.codex_model,
                codex_ignore_user_config=args.codex_ignore_user_config,
                pdftotext_cli=str(pdftotext_cli),
                pdf_timeout_seconds=args.pdf_timeout,
                codex_timeout_seconds=args.codex_timeout,
                minimum_characters=args.minimum_extracted_characters,
                retries=args.codex_retries,
                parallel=args.codex_parallel,
                refresh=args.refresh_intake,
            )
            base_outputs = build_atomic_cases(
                materials_dir=args.materials_dir,
                output_dir=args.output_dir,
                patterns=args.material_glob,
                recursive=args.recursive,
                source_kind=args.source_kind,
                overrides_path=args.intake_overrides,
                generated_overrides=generated_overrides,
                intake_mode="strict",
                filename_regex=args.filename_regex,
                id_prefix=args.id_prefix,
                openharness_id_prefix=args.openharness_id_prefix,
                split=args.split,
                skill=args.skill,
                source_collection=args.source_collection,
                task_template=args.task_template,
                background_template=args.background_template,
                hypo_default=args.hypo_default,
                material_focus_default=args.material_focus_default,
            )
    outputs = base_outputs
    statuses: dict[str, int] = {}
    for _, payload in outputs:
        status = payload["cases"][0]["metadata"]["intake_status"]
        statuses[status] = statuses.get(status, 0) + 1
    if not args.dry_run:
        for path, payload in outputs:
            _write_json_atomic(path, payload, force=args.force)
    action = "将生成" if args.dry_run else "已生成"
    status_text = ", ".join(f"{key}={value}" for key, value in sorted(statuses.items()))
    print(f"{action} {len(outputs)} 个原子 case；{status_text}")
    if args.dry_run:
        for path, _ in outputs[:10]:
            print(path)
        if len(outputs) > 10:
            print(f"... 另有 {len(outputs) - 10} 个")
    return 0


def _merge_command(args: argparse.Namespace) -> int:
    case_files = discover_case_files(
        args.input,
        args.manifest,
        args.pattern,
    )
    payload = merge_case_datasets(
        case_files=case_files,
        output=args.output,
        include=_parse_include(args.include),
        filters=_parse_filters(args.filter),
    )
    if args.dry_run:
        print(
            f"将从 {len(case_files)} 个 JSON 合并 "
            f"{len(payload['cases'])} 个 case 到 {args.output.resolve()}"
        )
        return 0
    _write_json_atomic(args.output, payload, force=args.force)
    print(
        f"已从 {len(case_files)} 个 JSON 合并 "
        f"{len(payload['cases'])} 个 case 到 {args.output.resolve()}"
    )
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="生成可复用的原子 WorkBuddy case，并按需合并为 dataset"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser(
        "generate",
        help="为每份素材生成一个可独立运行的 .case.json",
    )
    generate.add_argument("--materials-dir", type=Path, required=True)
    generate.add_argument(
        "--output-dir",
        type=Path,
        help="输出根目录；默认与素材相邻，并保留素材的相对子目录",
    )
    generate.add_argument(
        "--material-glob",
        action="append",
        default=None,
        help="素材 glob，可重复；默认 *.md",
    )
    generate.add_argument(
        "--source-kind",
        choices=("file", "directory"),
        default="file",
        help="一个 case 对应一个文件或一个目录；默认 file",
    )
    generate.add_argument("--recursive", action="store_true")
    generate.add_argument("--intake-overrides", type=Path)
    generate.add_argument(
        "--intake-mode",
        choices=("neutral", "placeholder", "strict"),
        default="neutral",
    )
    generate.add_argument(
        "--filename-regex",
        default=DEFAULT_FILENAME_REGEX.pattern,
        help=(
            "从文件 stem 提取 topic 的正则，必须包含命名分组 topic，"
            "可选 index/id"
        ),
    )
    generate.add_argument(
        "--id-prefix",
        help="case ID 前缀；默认根据 source collection 生成稳定前缀",
    )
    generate.add_argument(
        "--openharness-id-prefix",
        help="OpenHarness case ID 前缀；默认根据 source collection 生成稳定前缀",
    )
    generate.add_argument("--split", default="dev")
    generate.add_argument("--skill", default="research-report")
    generate.add_argument("--source-collection")
    generate.add_argument("--task-template", default=DEFAULT_TASK_TEMPLATE)
    generate.add_argument(
        "--background-template",
        default=DEFAULT_BACKGROUND_TEMPLATE,
    )
    generate.add_argument("--hypo-default", default=DEFAULT_HYPO)
    generate.add_argument(
        "--material-focus-default",
        default=DEFAULT_MATERIAL_FOCUS,
    )
    generate.add_argument(
        "--human-report-dir",
        type=Path,
        help=(
            "human_report 根目录；设置后按相对路径和同 stem 配对，"
            "调用 Codex 自动补齐 round 1"
        ),
    )
    generate.add_argument(
        "--human-report-extension",
        action="append",
        default=None,
        help="human_report 扩展名，可重复；默认 .pdf",
    )
    generate.add_argument(
        "--intake-cache",
        type=Path,
        help=(
            "Codex 推断缓存；默认 human_report-dir 的父目录/"
            "intake_answers.codex.json"
        ),
    )
    generate.add_argument("--codex-cli", help="Codex CLI 路径；默认从 PATH 查找")
    generate.add_argument("--codex-model", help="Codex 模型；默认使用 CLI 配置")
    generate.add_argument(
        "--codex-ignore-user-config",
        action="store_true",
        help="推断时不加载用户 config.toml；认证信息仍由 Codex CLI 使用",
    )
    generate.add_argument("--codex-parallel", type=int, default=2)
    generate.add_argument("--codex-timeout", type=float, default=900)
    generate.add_argument("--codex-retries", type=int, default=1)
    generate.add_argument(
        "--pdftotext-cli",
        help="pdftotext 路径；默认从 PATH 查找",
    )
    generate.add_argument("--pdf-timeout", type=float, default=120)
    generate.add_argument(
        "--minimum-extracted-characters",
        type=int,
        default=500,
    )
    generate.add_argument(
        "--refresh-intake",
        action="store_true",
        help="忽略有效缓存，重新调用 Codex",
    )
    generate.add_argument("--dry-run", action="store_true")
    generate.add_argument("--force", action="store_true")
    generate.set_defaults(handler=_generate_command)

    projects = subparsers.add_parser(
        "generate-projects",
        help=(
            "处理“一个项目目录 + 一个素材子目录 + 一个 human_report”结构，"
            "生成完整原子 case"
        ),
    )
    projects.add_argument("--projects-dir", type=Path, required=True)
    projects.add_argument("--output-dir", type=Path, required=True)
    projects.add_argument(
        "--filename-regex",
        default=r"^(?P<index>\d{6,8})_(?P<topic>.+)$",
    )
    projects.add_argument("--id-prefix", default="case-realproject")
    projects.add_argument(
        "--openharness-id-prefix",
        default="rr-realproject",
    )
    projects.add_argument("--source-collection", default="real-project")
    projects.add_argument("--split", default="dev")
    projects.add_argument("--skill", default="research-report")
    projects.add_argument("--task-template", default=DEFAULT_TASK_TEMPLATE)
    projects.add_argument(
        "--background-template",
        default=DEFAULT_BACKGROUND_TEMPLATE,
    )
    projects.add_argument("--hypo-default", default=DEFAULT_HYPO)
    projects.add_argument(
        "--material-focus-default",
        default=DEFAULT_MATERIAL_FOCUS,
    )
    projects.add_argument("--intake-overrides", type=Path)
    projects.add_argument(
        "--human-report-extension",
        action="append",
        default=None,
        help="默认同时支持 .pdf 和 .pptx",
    )
    projects.add_argument("--intake-cache", type=Path)
    projects.add_argument("--codex-cli")
    projects.add_argument("--codex-model")
    projects.add_argument("--codex-ignore-user-config", action="store_true")
    projects.add_argument("--codex-parallel", type=int, default=2)
    projects.add_argument("--codex-timeout", type=float, default=900)
    projects.add_argument("--codex-retries", type=int, default=1)
    projects.add_argument("--pdftotext-cli")
    projects.add_argument("--pdf-timeout", type=float, default=120)
    projects.add_argument(
        "--minimum-extracted-characters",
        type=int,
        default=500,
    )
    projects.add_argument("--refresh-intake", action="store_true")
    projects.add_argument("--dry-run", action="store_true")
    projects.add_argument("--force", action="store_true")
    projects.set_defaults(handler=_generate_projects_command)

    merge = subparsers.add_parser(
        "merge",
        help="选择并合并原子 case，自动重写素材相对路径",
    )
    merge.add_argument(
        "--input",
        type=Path,
        action="append",
        default=[],
        help="case JSON 或目录，可重复",
    )
    merge.add_argument(
        "--manifest",
        type=Path,
        action="append",
        default=[],
        help="每行一个 case JSON 路径，可重复",
    )
    merge.add_argument("--pattern", default="*.case.json")
    merge.add_argument("--output", type=Path, required=True)
    merge.add_argument(
        "--include",
        help="按 metadata.source_index 选择，例如 1,2,5,20-30",
    )
    merge.add_argument(
        "--filter",
        action="append",
        default=[],
        help="按 case 字段筛选，例如 metadata.split=dev，可重复",
    )
    merge.add_argument("--dry-run", action="store_true")
    merge.add_argument("--force", action="store_true")
    merge.set_defaults(handler=_merge_command)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "generate" and args.material_glob is None:
        args.material_glob = ["*.md" if args.source_kind == "file" else "*"]
    if args.command in {"generate", "generate-projects"}:
        if args.human_report_extension is None:
            args.human_report_extension = (
                [".pdf"]
                if args.command == "generate"
                else [".pdf", ".pptx"]
            )
        if (
            args.command == "generate"
            and args.human_report_dir
            and args.source_kind != "file"
        ):
            raise CaseDatasetError(
                "--human-report-dir 当前仅支持 --source-kind file"
            )
        if args.codex_parallel < 1:
            raise CaseDatasetError("--codex-parallel 必须至少为 1")
        if args.codex_timeout <= 0 or args.pdf_timeout <= 0:
            raise CaseDatasetError("Codex/PDF timeout 必须大于 0")
        if args.codex_retries < 0:
            raise CaseDatasetError("--codex-retries 不能小于 0")
        if args.minimum_extracted_characters < 1:
            raise CaseDatasetError(
                "--minimum-extracted-characters 必须至少为 1"
            )
    if args.command == "merge" and not args.input and not args.manifest:
        raise CaseDatasetError("merge 至少需要一个 --input 或 --manifest")
    return int(args.handler(args))


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CaseDatasetError as exc:
        print(f"错误: {exc}", file=sys.stderr)
        raise SystemExit(2)
