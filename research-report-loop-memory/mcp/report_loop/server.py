#!/usr/bin/env python3
"""Dependency-free stdio MCP server for Research Report Loop."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parents[2]
if str(PLUGIN_ROOT) not in sys.path:
    sys.path.insert(0, str(PLUGIN_ROOT))

from mcp.report_loop.core.runtime import ReportLoopError, ReportLoopRuntime  # noqa: E402


SERVER_NAME = "research-report-loop"
SERVER_VERSION = "1.0.0-mvp.10"
runtime = ReportLoopRuntime()


TOOLS = [
    {
        "name": "report_loop_start",
        "description": (
            "在宿主 Agent 写完初稿后创建 Report Loop，解析并冻结当前版本化 Rubric Set。"
            "当前 MVP 固定目标 5.0、最多评测 3 个版本。"
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["task"],
            "properties": {
                "task": {"type": "string", "minLength": 1, "maxLength": 4000},
                "audience": {"type": "string", "maxLength": 300},
                "project": {"type": "string", "maxLength": 300},
                "artifactPath": {"type": "string", "maxLength": 2000},
                "structuredDataPath": {"type": "string", "maxLength": 2000},
                "targetScore": {"type": "number", "enum": [5.0], "default": 5.0},
                "maxJudgedVersions": {"type": "integer", "enum": [3], "default": 3},
                "maxElapsedSeconds": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 86400,
                    "default": 3600,
                },
                "skillVersion": {"type": "string", "maxLength": 200},
            },
        },
    },
    {
        "name": "report_loop_submit",
        "description": (
            "提交一个 Markdown 报告版本，按冻结 Rubric 的 Dimension 并行调用独立 Judge，"
            "全部返回后完成评分汇总、版本采纳和停止判断。"
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["runId", "artifactPath"],
            "properties": {
                "runId": {"type": "string", "pattern": "^report-[A-Za-z0-9_-]+$"},
                "artifactPath": {"type": "string", "minLength": 1, "maxLength": 2000},
            },
        },
    },
    {
        "name": "report_loop_finish",
        "description": (
            "在 Loop 达到停止条件后返回最佳已采纳报告。"
            "Judge 不可用或用户取消时可显式提前结束，避免流程死锁。"
        ),
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["runId"],
            "properties": {
                "runId": {"type": "string", "pattern": "^report-[A-Za-z0-9_-]+$"},
                "reason": {
                    "type": "string",
                    "enum": ["judge_unavailable", "user_cancelled"],
                },
            },
        },
    },
    {
        "name": "report_loop_status",
        "description": "读取 Report Loop 当前状态；用于故障诊断，不替代 submit 或 finish。",
        "inputSchema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["runId"],
            "properties": {
                "runId": {"type": "string", "pattern": "^report-[A-Za-z0-9_-]+$"},
            },
        },
    },
]


def _tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(payload, ensure_ascii=False),
            }
        ],
        "structuredContent": payload,
        "isError": is_error,
    }


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        if name == "report_loop_start":
            return _tool_result(runtime.start(**arguments))
        if name == "report_loop_submit":
            return _tool_result(runtime.submit(**arguments))
        if name == "report_loop_finish":
            return _tool_result(runtime.finish(**arguments))
        if name == "report_loop_status":
            return _tool_result(runtime.status(**arguments))
        return _tool_result(
            {"status": "error", "reason": f"unknown_tool:{name}"},
            is_error=True,
        )
    except (ReportLoopError, TypeError, ValueError, OSError) as exc:
        return _tool_result(
            {"status": "error", "reason": str(exc), "tool": name},
            is_error=True,
        )
    except Exception as exc:  # fail as a tool error, never corrupt stdio transport
        return _tool_result(
            {"status": "error", "reason": f"internal_error:{exc}", "tool": name},
            is_error=True,
        )


def _handle(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "initialize":
        protocol = (request.get("params") or {}).get("protocolVersion") or "2024-11-05"
        result = {
            "protocolVersion": protocol,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": (
                "宿主 Agent 负责写作；本 MCP 只读版本化 Rubric Set，按 Scope 解析并冻结，负责隔离 Judge、"
                "版本采纳和停止判断。"
            ),
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        params = request.get("params") or {}
        result = _call_tool(str(params.get("name") or ""), params.get("arguments") or {})
    elif method in {"notifications/initialized", "notifications/cancelled"}:
        return None
    else:
        if request_id is None:
            return None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def main() -> None:
    for raw_line in sys.stdin:
        if not raw_line.strip():
            continue
        try:
            request = json.loads(raw_line)
            if not isinstance(request, dict):
                raise ValueError("request must be an object")
            response = _handle(request)
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": str(exc)},
            }
        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
