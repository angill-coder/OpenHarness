#!/usr/bin/env python3
"""Lightweight WorkBuddy hook guard for the Report Loop lifecycle."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPORT_TASK = re.compile(
    r"(?:写|撰写|生成|制作|改写|修改|完善).{0,18}(?:报告|汇报|调研|复盘)|"
    r"(?:研究报告|战略分析|高管汇报|调研洞察|复盘报告|storyline)",
    re.I,
)
SKILL_REF = re.compile(r"research-report-loop(?![\w-])", re.I)
WRITE_TOOLS = re.compile(r"^(?:Write|Edit|MultiEdit|ApplyPatch|apply_patch)$", re.I)
PRESENT_TOOLS = re.compile(r"^(?:present_files)$", re.I)


def _root() -> Path:
    configured = os.environ.get("RESEARCH_REPORT_LOOP_DIR", "~/.research-report-loop")
    return Path(configured).expanduser().resolve()


def _state_path(session_id: str) -> Path:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return _root() / "hook-state" / f"{digest}.json"


def _empty(session_id: str) -> dict[str, Any]:
    return {
        "version": 1,
        "sessionId": session_id,
        "active": False,
        "artifactWritten": False,
        "runStarted": False,
        "readyToFinish": False,
        "completed": False,
        "runId": None,
        "updatedAt": datetime.now(timezone.utc).isoformat(),
    }


def _load(session_id: str) -> dict[str, Any]:
    path = _state_path(session_id)
    try:
        return {**_empty(session_id), **json.loads(path.read_text(encoding="utf-8"))}
    except FileNotFoundError:
        return _empty(session_id)


def _save(state: dict[str, Any]) -> None:
    path = _state_path(state["sessionId"])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    state["updatedAt"] = datetime.now(timezone.utc).isoformat()
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _read_input() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    value = json.loads(raw)
    return value if isinstance(value, dict) else {}


def _session(input_data: dict[str, Any]) -> str:
    value = str(input_data.get("session_id") or "").strip()
    if not value:
        raise ValueError("missing session_id")
    return value


def _safe_json(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return ""


def _tool_result(input_data: dict[str, Any]) -> Any:
    return input_data.get("tool_response", input_data.get("tool_result"))


def _successful(result: Any) -> bool:
    text = _safe_json(result)
    return not re.search(
        r'"isError"\s*:\s*true|"status"\s*:\s*"error"|tool[_ ]error|execution failed',
        text,
        re.I,
    )


def _payload(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        structured = result.get("structuredContent")
        if isinstance(structured, dict):
            return structured
        if isinstance(result.get("status"), str):
            return result
    text = _safe_json(result)
    for match in re.finditer(r"\{.*\}", text, re.S):
        try:
            value = json.loads(match.group(0))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            if isinstance(value.get("structuredContent"), dict):
                return value["structuredContent"]
            return value
    return {}


def _print(payload: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _skill_activated(tool_name: str, tool_input: Any) -> bool:
    if tool_name.lower() in {"skill", "use_skill", "skillmanage"}:
        data = tool_input if isinstance(tool_input, dict) else {}
        fields = [data.get("skill"), data.get("command"), data.get("name")]
        return bool(SKILL_REF.search(" ".join(str(value) for value in fields if value)))
    data = tool_input if isinstance(tool_input, dict) else {}
    file_path = str(data.get("file_path") or "")
    return "/skills/research-report-loop/" in file_path.replace("\\", "/")


def on_prompt(input_data: dict[str, Any]) -> None:
    session_id = _session(input_data)
    state = _load(session_id)
    prompt = str(input_data.get("prompt") or input_data.get("user_prompt") or "")
    if REPORT_TASK.search(prompt) and (state.get("completed") or not state.get("active")):
        state = _empty(session_id)
        state["active"] = True
        _save(state)
    _print({
        "continue": True,
        "suppressOutput": True,
        **(
            {
                "systemMessage": (
                    "这是 Research Report Loop 任务。先完成需求澄清和初稿；写入报告文件后，"
                    "必须调用 report_loop_start 和 report_loop_submit，最终调用 report_loop_finish。"
                )
            }
            if state.get("active")
            else {}
        ),
    })


def on_pre_tool(input_data: dict[str, Any]) -> None:
    state = _load(_session(input_data))
    tool_name = str(input_data.get("tool_name") or "")
    if PRESENT_TOOLS.match(tool_name) and state.get("artifactWritten") and not state.get("completed"):
        reason = "报告已生成，但 Report Loop 尚未 finish；请先完成 Judge 循环并交付 bestArtifactPath。"
        _print({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            },
            "systemMessage": reason,
        })
        return
    _print({"continue": True, "suppressOutput": True})


def on_post_tool(input_data: dict[str, Any]) -> None:
    session_id = _session(input_data)
    state = _load(session_id)
    tool_name = str(input_data.get("tool_name") or "")
    tool_input = input_data.get("tool_input") or {}
    result = _tool_result(input_data)
    successful = _successful(result)
    message = ""
    if successful and _skill_activated(tool_name, tool_input):
        state["active"] = True
        message = "Research Report Loop Skill 已加载；完成初稿文件后立即启动并提交 Loop。"
    if successful and state.get("active") and WRITE_TOOLS.match(tool_name):
        file_path = str(tool_input.get("file_path") or tool_input.get("path") or "")
        if file_path.lower().endswith((".md", ".txt")):
            state["artifactWritten"] = True
    if successful and tool_name.endswith("report_loop_start"):
        payload = _payload(result)
        if payload.get("status") == "started":
            state.update({
                "active": True,
                "runStarted": True,
                "readyToFinish": False,
                "completed": False,
                "runId": payload.get("runId"),
            })
    if successful and tool_name.endswith("report_loop_submit"):
        payload = _payload(result)
        if payload.get("status") == "judged":
            state["readyToFinish"] = payload.get("nextAction") == "deliver"
            message = (
                "Judge 已要求继续修改；从 bestArtifactPath 修改并再次 submit。"
                if not state["readyToFinish"]
                else "Report Loop 已达到停止条件；现在调用 report_loop_finish 并交付最佳版本。"
            )
    if successful and tool_name.endswith("report_loop_finish"):
        payload = _payload(result)
        if payload.get("status") == "completed":
            state.update({"completed": True, "active": False, "readyToFinish": False})
    _save(state)
    _print({
        "continue": True,
        "suppressOutput": True,
        **({"systemMessage": message} if message else {}),
    })


def on_stop(input_data: dict[str, Any]) -> None:
    state = _load(_session(input_data))
    if input_data.get("stop_hook_active") is True:
        _print({
            "continue": True,
            "suppressOutput": True,
            "systemMessage": "Report Loop Guard 已避免重复阻断；未完成状态会保留到下一轮。",
        })
        return
    missing = ""
    if state.get("artifactWritten") and not state.get("runStarted"):
        missing = "初稿文件已经生成，但尚未调用 report_loop_start 和 report_loop_submit。"
    elif state.get("runStarted") and not state.get("completed"):
        missing = (
            "Loop 已达到停止条件，但尚未调用 report_loop_finish。"
            if state.get("readyToFinish")
            else "Report Loop 尚未完成；请根据最近一次 submit 结果继续修改或提交。"
        )
    if not missing:
        _print({
            "hookSpecificOutput": {
                "hookEventName": "Stop",
                "permissionDecision": "allow",
            }
        })
        return
    reason = (
        f"Research Report Loop checkpoint 未完成：{missing}"
        "仅补齐缺失的 Loop 调用，不要重复已经完成的报告正文或需求澄清。"
    )
    _print({
        "hookSpecificOutput": {
            "hookEventName": "Stop",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
        "reason": reason,
        "systemMessage": "Report Loop Guard 已阻止本轮提前结束。",
    })


def main() -> None:
    if os.environ.get("RESEARCH_REPORT_LOOP_GUARD") == "off":
        _print({"continue": True, "suppressOutput": True})
        return
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    input_data = _read_input()
    if mode == "prompt":
        on_prompt(input_data)
    elif mode == "pre-tool":
        on_pre_tool(input_data)
    elif mode == "post-tool":
        on_post_tool(input_data)
    elif mode == "stop":
        on_stop(input_data)
    else:
        raise ValueError(f"unsupported hook mode: {mode or '<empty>'}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[research-report-loop] hook failed: {exc}", file=sys.stderr)
        raise SystemExit(1)
