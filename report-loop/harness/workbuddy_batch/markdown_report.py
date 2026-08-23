from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .models import CaseSpec


def _fenced(value: Any, language: str = "") -> str:
    if isinstance(value, str):
        rendered = value
    else:
        rendered = json.dumps(value, ensure_ascii=False, indent=2)
    fence = "```"
    while fence in rendered:
        fence += "`"
    return f"{fence}{language}\n{rendered}\n{fence}"


def _tool_section(operation: dict[str, Any]) -> list[str]:
    name = str(operation.get("name") or "unknown")
    lines = [f"**tool: {name}**", ""]
    facts = [f"status: `{operation.get('status', 'unknown')}`"]
    if operation.get("duration_ms") is not None:
        facts.append(f"duration: `{operation['duration_ms']} ms`")
    lines.extend(["- " + " · ".join(facts), ""])
    if operation.get("input") is not None:
        lines.extend(["input:", "", _fenced(operation["input"], "json"), ""])
    if operation.get("result") is not None:
        lines.extend(["result:", "", _fenced(operation["result"]), ""])
    return lines


def _token(usage: dict[str, Any], name: str) -> str:
    value = usage.get(name)
    return f"{value:,}" if isinstance(value, int) else "—"


def render_case_markdown(
    *,
    case: CaseSpec,
    summary: dict[str, Any],
    assistant_messages: list[dict[str, Any]],
    operations: list[dict[str, Any]],
    model: str | None,
    skills: tuple[str, ...],
) -> str:
    """Render a chronological, human-readable conversation and tool trace."""

    rounds = summary.get("rounds", [])
    latest_usage: dict[str, Any] = {}
    for round_result in reversed(rounds):
        usage = round_result.get("usage")
        if isinstance(usage, dict) and usage:
            latest_usage = usage
            break
    lines = [
        f"# WorkBuddy 对话记录：{case.case_id}",
        "",
        f"- status: `{summary.get('status', 'unknown')}`",
        f"- configured model: `{model or 'default'}`",
        "- observed models: "
        + (
            ", ".join(f"`{name}`" for name in summary.get("observed_models", []))
            or "—"
        ),
        f"- skills: {', '.join(f'`{name}`' for name in skills) or '—'}",
        f"- rounds: `{summary.get('rounds_completed', 0)}/{summary.get('rounds_planned', 0)}`",
        f"- duration: `{summary.get('duration_ms', 0)} ms`",
        f"- case timeout: `{summary.get('timeout_seconds', 900):g} s`",
        f"- stall timeout: `{summary.get('stall_timeout_seconds', 180):g} s`",
        f"- session: `{summary.get('session_id', '')}`",
        "- latest reported tokens: "
        f"input `{_token(latest_usage, 'input_tokens')}` · "
        f"output `{_token(latest_usage, 'output_tokens')}` · "
        f"cache creation `{_token(latest_usage, 'cache_creation_input_tokens')}` · "
        f"cache read `{_token(latest_usage, 'cache_read_input_tokens')}`",
        "- token note: CLI `result.usage`; `--resume` 时可能是会话累计值",
        "",
    ]
    if summary.get("repetition_total", 1) > 1:
        lines[2:2] = [
            f"- source case: `{summary.get('source_case_id', case.case_id)}`",
            f"- repetition: `{summary.get('repetition_index')}/{summary.get('repetition_total')}`",
        ]
    if summary.get("error"):
        lines.insert(3, f"- error: `{summary['error']}`")
    operations_by_id = {
        str(item.get("tool_use_id")): item
        for item in operations
        if item.get("tool_use_id")
    }
    seen_tools: set[str] = set()
    for round_index, interaction in enumerate(case.user_inputs):
        lines.extend(
            [
                f"## 第 {round_index + 1} 轮 · {interaction.label}",
                "",
                "**user:**",
                "",
                interaction.input,
                "",
            ]
        )
        workbuddy_texts: list[str] = []
        for message in assistant_messages:
            if message.get("round_index") != round_index:
                continue
            for item in message.get("content", []):
                if item.get("type") == "text" and item.get("text"):
                    text = str(item["text"]).strip()
                    workbuddy_texts.append(text)
                    lines.extend(
                        ["**workbuddy:**", "", text, ""]
                    )
                elif item.get("type") == "tool_use":
                    tool_id = str(item.get("id") or "")
                    operation = operations_by_id.get(
                        tool_id,
                        {
                            "tool_use_id": tool_id,
                            "name": item.get("name"),
                            "input": item.get("input"),
                            "status": "unknown",
                        },
                    )
                    lines.extend(_tool_section(operation))
                    if tool_id:
                        seen_tools.add(tool_id)

        for operation in operations:
            tool_id = str(operation.get("tool_use_id") or "")
            if operation.get("round_index") != round_index or tool_id in seen_tools:
                continue
            lines.extend(_tool_section(operation))
            if tool_id:
                seen_tools.add(tool_id)

        if round_index < len(rounds):
            fallback = rounds[round_index].get("final_output")
            fallback_text = str(fallback).strip() if fallback else ""
            if fallback_text and fallback_text not in "\n".join(workbuddy_texts):
                lines.extend(["**workbuddy:**", "", fallback_text, ""])

            round_result = rounds[round_index]
            observed_models = (
                ", ".join(
                    f"`{name}`" for name in round_result.get("observed_models", [])
                )
                or "—"
            )
            usage = round_result.get("usage")
            usage = usage if isinstance(usage, dict) else {}
            lines.extend(
                [
                    f"_round status: `{round_result.get('status', 'unknown')}` · "
                    f"duration: `{round_result.get('duration_ms', 0)} ms`_",
                    "",
                    "**本轮运行信息**",
                    "",
                    f"- 实际观测模型：{observed_models}",
                    "- Token："
                    f"input `{_token(usage, 'input_tokens')}` · "
                    f"output `{_token(usage, 'output_tokens')}` · "
                    f"cache creation `{_token(usage, 'cache_creation_input_tokens')}` · "
                    f"cache read `{_token(usage, 'cache_read_input_tokens')}`",
                    "",
                ]
            )

    return "\n".join(lines).rstrip() + "\n"


def render_run_markdown(
    *,
    run_id: str,
    summaries: list[dict[str, Any]],
    report_paths: dict[str, str],
) -> str:
    """Render a compact batch index linking to each readable case report."""

    lines = [
        f"# WorkBuddy 批次结果：{run_id}",
        "",
        f"共 `{len(summaries)}` 个案例。完整对话和工具操作请打开对应的 `conversation.md`。",
        "",
        "| case | repetition | status | 配置模型 | 实际观测模型 | rounds | duration | 最终输出 | 对话记录 |",
        "|---|---:|---|---|---|---:|---:|---|---|",
    ]
    for summary in summaries:
        case_id = str(summary.get("case_id", "unknown"))
        status = str(summary.get("status", "unknown"))
        completed = summary.get("rounds_completed", 0)
        planned = summary.get("rounds_planned", 0)
        duration = summary.get("duration_ms", 0)
        final_output = str(summary.get("final_output") or summary.get("error") or "—")
        final_output = final_output.replace("|", "\\|").replace("\n", "<br>")
        if len(final_output) > 240:
            final_output = final_output[:237] + "..."
        report_path = report_paths.get(case_id)
        report_link = f"[查看]({report_path})" if report_path else "—"
        repetition = (
            f"{summary.get('repetition_index', 1)}/"
            f"{summary.get('repetition_total', 1)}"
        )
        configured_model = str(summary.get("configured_model") or "default")
        observed_models = ", ".join(summary.get("observed_models", [])) or "—"
        lines.append(
            f"| {case_id} | {repetition} | `{status}` | {configured_model} | "
            f"{observed_models} | {completed}/{planned} | "
            f"{duration} ms | {final_output} | {report_link} |"
        )
    lines.extend(
        [
            "",
            "## Token 使用量",
            "",
            "以下数值来自每轮 CLI 最终 `result.usage`。使用 `--resume` 时可能是会话累计值，因此不要直接累加各轮。",
            "",
            "| case | round | 实际观测模型 | input | output | cache creation | cache read |",
            "|---|---:|---|---:|---:|---:|---:|",
        ]
    )
    token_rows = 0
    for summary in summaries:
        case_id = str(summary.get("case_id", "unknown"))
        for index, round_result in enumerate(summary.get("rounds", [])):
            usage = round_result.get("usage")
            if not isinstance(usage, dict) or not usage:
                continue
            token_rows += 1
            round_number = int(round_result.get("round_index", index)) + 1
            label = str(round_result.get("label") or "")
            round_name = f"{round_number} · {label}" if label else str(round_number)
            observed = ", ".join(round_result.get("observed_models", [])) or "—"

            lines.append(
                f"| {case_id} | {round_name} | {observed} | "
                f"{_token(usage, 'input_tokens')} | {_token(usage, 'output_tokens')} | "
                f"{_token(usage, 'cache_creation_input_tokens')} | "
                f"{_token(usage, 'cache_read_input_tokens')} |"
            )
    if not token_rows:
        lines.append("| — | — | — | — | — | — | — |")
    return "\n".join(lines) + "\n"


def write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
