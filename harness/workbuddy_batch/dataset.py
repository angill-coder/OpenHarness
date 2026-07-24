from __future__ import annotations

import csv
import json
import re
from pathlib import Path
from typing import Any, Iterable

from .models import CaseSpec, InputFile, Interaction


MARKER = re.compile(r"{{\s*([A-Za-z0-9_.-]+)\s*}}")


def _safe_id(value: Any, index: int) -> str:
    candidate = re.sub(r"[^0-9A-Za-z._-]+", "_", str(value or "")).strip("._-")
    return candidate or f"case_{index:04d}"


def _as_list(value: Any) -> list[Any]:
    if value is None or value == "":
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        if stripped.startswith("["):
            parsed = json.loads(stripped)
            return parsed if isinstance(parsed, list) else [parsed]
        return [item.strip() for item in stripped.split(";") if item.strip()]
    return [value]


def _resolve(context: dict[str, Any], key: str) -> Any:
    current: Any = context
    for part in key.split("."):
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"模板变量不存在: {key}")
        current = current[part]
    return current


def render_template(template: str, context: dict[str, Any]) -> str:
    """Render small, dependency-free ``{{ dotted.path }}`` templates."""

    def replace(match: re.Match[str]) -> str:
        value = _resolve(context, match.group(1))
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, indent=2)
        return str(value)

    return MARKER.sub(replace, template)


def _parse_interactions(value: Any, row: dict[str, Any]) -> list[Any]:
    if value not in (None, ""):
        return _as_list(value)
    numbered: list[Any] = []
    for index in range(1, 100):
        item = row.get(f"interaction_{index}")
        if item in (None, ""):
            if index > 3:
                break
            continue
        numbered.append(item)
    return numbered


def _interaction(value: Any, context: dict[str, Any], index: int) -> Interaction:
    if isinstance(value, dict):
        text = value.get("input", value.get("text", ""))
        label = str(value.get("label", f"interaction_{index}"))
    else:
        text = value
        label = f"interaction_{index}"
    rendered = render_template(str(text), context).strip()
    if not rendered:
        raise ValueError(f"第 {index} 轮交互输入为空")
    return Interaction(rendered, label)


def _turn(value: Any, context: dict[str, Any], index: int) -> Interaction:
    if isinstance(value, dict):
        try:
            declared_round = int(value.get("round", index))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"turns 第 {index} 项的 round 必须是整数") from exc
        if declared_round != index:
            raise ValueError(
                f"turns 必须从 0 连续编号；第 {index} 项声明为 round={declared_round}"
            )
        text = value.get("prompt", value.get("input", value.get("text", "")))
        default_label = "initial_prompt" if index == 0 else f"round_{index}"
        label = str(value.get("label", default_label))
    else:
        text = value
        label = "initial_prompt" if index == 0 else f"round_{index}"
    rendered = render_template(str(text), context).strip()
    if not rendered:
        raise ValueError(f"turns 第 {index} 轮 prompt 为空")
    return Interaction(rendered, label)


def _path(value: Any, base_dir: Path) -> Path:
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else (base_dir / path).resolve()


def _input_file(value: Any, base_dir: Path) -> InputFile:
    if isinstance(value, dict):
        return InputFile(
            source=_path(value.get("source", ""), base_dir),
            target=str(value.get("target", "")),
        )
    return InputFile(source=_path(value, base_dir))


def _merge(defaults: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    result = dict(defaults)
    for key, value in row.items():
        if value not in (None, ""):
            result[key] = value
    return result


def _build_case(
    raw: dict[str, Any],
    defaults: dict[str, Any],
    index: int,
    base_dir: Path,
    fallback_prompt: str | None,
) -> CaseSpec:
    merged = _merge(defaults, raw)
    case_id = _safe_id(merged.get("id", merged.get("case_id")), index)
    data = merged.get("data")
    if not isinstance(data, dict):
        data = {
            key: value
            for key, value in raw.items()
            if key
            not in {
                "id",
                "case_id",
                "prompt",
                "interactions",
                "turns",
                "model",
                "effort",
                "skills",
                "skill_paths",
                "plugin_dirs",
                "input_files",
                "artifact_globs",
                "metadata",
            }
            and not key.startswith("interaction_")
        }
    context = dict(raw)
    context.update({"data": data, "case_id": case_id, "dataset_json": data})
    turn_values = _as_list(merged.get("turns"))
    if turn_values:
        parsed_turns = tuple(
            _turn(value, context, turn_index)
            for turn_index, value in enumerate(turn_values)
        )
        prompt = parsed_turns[0].input
        prompt_label = parsed_turns[0].label
        interactions = parsed_turns[1:]
    else:
        prompt_template = str(merged.get("prompt") or fallback_prompt or "").strip()
        if not prompt_template:
            raise ValueError(
                f"案例 {case_id} 缺少 turns/prompt，且未提供全局 prompt 模板"
            )
        prompt = render_template(prompt_template, context).strip()
        prompt_label = "initial_prompt"
        interactions = tuple(
            _interaction(value, context, interaction_index)
            for interaction_index, value in enumerate(
                _parse_interactions(merged.get("interactions"), raw), start=1
            )
        )
    return CaseSpec(
        case_id=case_id,
        prompt=prompt,
        prompt_label=prompt_label,
        interactions=interactions,
        data=data,
        model=str(merged["model"]) if merged.get("model") else None,
        effort=str(merged["effort"]) if merged.get("effort") else None,
        skills=tuple(str(item) for item in _as_list(merged.get("skills"))),
        skill_paths=tuple(
            _path(item, base_dir) for item in _as_list(merged.get("skill_paths"))
        ),
        plugin_dirs=tuple(
            _path(item, base_dir) for item in _as_list(merged.get("plugin_dirs"))
        ),
        input_files=tuple(
            _input_file(item, base_dir)
            for item in _as_list(merged.get("input_files"))
        ),
        artifact_globs=tuple(
            str(item) for item in _as_list(merged.get("artifact_globs"))
        ),
        metadata=(
            dict(merged.get("metadata", {}))
            if isinstance(merged.get("metadata", {}), dict)
            else {"value": merged.get("metadata")}
        ),
    )


def _load_rows(path: Path) -> tuple[dict[str, Any], Iterable[dict[str, Any]]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            return {}, list(csv.DictReader(handle))
    if suffix == ".jsonl":
        rows = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8-sig").splitlines(), start=1
        ):
            if not line.strip():
                continue
            value = json.loads(line)
            if not isinstance(value, dict):
                raise ValueError(f"JSONL 第 {line_number} 行不是对象")
            rows.append(value)
        return {}, rows
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    if isinstance(payload, list):
        return {}, payload
    if not isinstance(payload, dict) or not isinstance(payload.get("cases"), list):
        raise ValueError("JSON 数据集必须是案例数组，或包含 cases 数组的对象")
    defaults = payload.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ValueError("dataset.defaults 必须是对象")
    return defaults, payload["cases"]


def load_cases(path: Path, prompt_template: str | None = None) -> list[CaseSpec]:
    defaults, rows = _load_rows(path)
    cases: list[CaseSpec] = []
    seen: set[str] = set()
    for index, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"第 {index} 个案例不是对象")
        case = _build_case(raw, defaults, index, path.parent, prompt_template)
        if case.case_id in seen:
            raise ValueError(f"案例 ID 重复: {case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    if not cases:
        raise ValueError("数据集中没有可运行案例")
    return cases
