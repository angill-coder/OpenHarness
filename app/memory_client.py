# -*- coding: utf-8 -*-
"""Thin bridge to research-report-memory-v1.

OpenHarness owns orchestration and LLM calls. The sibling memory project remains
the only implementation of L0/L1/L2 storage and validation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import uuid


class MemoryClientError(RuntimeError):
    pass


ROOT = Path(__file__).resolve().parents[1]
SUPPORTED_MEMORY_USERS = ("local", "tianliu", "angill", "sijing", "zoe")
DEFAULT_MEMORY_ROOT = (
    ROOT.parent / "Memory-test" / "research-report-memory-v1"
)


def normalize_memory_user(value):
    user = str(value or "local").strip().lower()
    if user not in SUPPORTED_MEMORY_USERS:
        raise MemoryClientError("不支持的 Memory 用户: %s" % user)
    return user


class ResearchReportMemoryClient:
    def __init__(
        self, runtime_root=None, data_dir=None, timeout=90, user="local"
    ):
        self.user = normalize_memory_user(user)
        self.runtime_root = Path(
            runtime_root
            or os.environ.get("OPENHARNESS_MEMORY_RUNTIME_ROOT")
            or DEFAULT_MEMORY_ROOT
        ).expanduser().resolve()
        if data_dir:
            selected_data_dir = data_dir
        elif os.environ.get("OPENHARNESS_MEMORY_DATA_ROOT"):
            selected_data_dir = (
                Path(os.environ["OPENHARNESS_MEMORY_DATA_ROOT"])
                / self.user
            )
        elif os.environ.get("OPENHARNESS_MEMORY_DATA_DIR"):
            legacy = Path(os.environ["OPENHARNESS_MEMORY_DATA_DIR"])
            selected_data_dir = legacy.parent / self.user
        else:
            selected_data_dir = (
                ROOT / "app" / "sessions" / "_memory" / self.user
            )
        self.data_dir = Path(selected_data_dir).expanduser().resolve()
        self.timeout = float(timeout)

    def available(self):
        return bool(
            (self.runtime_root / "scripts" / "run-node.sh").is_file()
            and (self.runtime_root / "node_modules" / "tsx").is_dir()
            and (
                self.runtime_root
                / "integration"
                / "openharness-worker.ts"
            ).is_file()
        )

    def call(self, method, params=None):
        if not self.available():
            raise MemoryClientError(
                "research-report-memory-v1 未就绪；请设置 "
                "OPENHARNESS_MEMORY_RUNTIME_ROOT 并安装其 Node 依赖"
            )
        request_id = "mem-" + uuid.uuid4().hex[:12]
        request = {
            "id": request_id,
            "method": method,
            "params": params or {},
        }
        env = os.environ.copy()
        env["RESEARCH_REPORT_MEMORY_DIR"] = str(self.data_dir)
        command = [
            "/bin/sh",
            str(self.runtime_root / "scripts" / "run-node.sh"),
            "--import",
            "tsx",
            "integration/openharness-worker.ts",
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(self.runtime_root),
                env=env,
                input=json.dumps(request, ensure_ascii=False) + "\n",
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise MemoryClientError("Memory Runtime 调用失败: %s" % exc) from exc
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        if completed.returncode != 0 or not lines:
            detail = completed.stderr.strip() or "未返回响应"
            raise MemoryClientError("Memory Runtime 异常: %s" % detail)
        try:
            response = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise MemoryClientError("Memory Runtime 返回了非法 JSON") from exc
        if response.get("id") != request_id or not response.get("ok"):
            raise MemoryClientError(
                str(response.get("error") or "Memory Runtime 请求失败")
            )
        return response.get("result") or {}

    def handshake(self):
        return self.call("handshake")

    def policy(self):
        return self.call("policy")

    def maintenance_snapshot(self, limit=100):
        return self.call("maintenance_snapshot", {"limit": limit})

    def capture(self, payload):
        return self.call("capture", payload)

    def recall(self, payload):
        return self.call("recall", payload)

    def forget(self, memory_id="", query="", include_episodes=False):
        payload = {"includeEpisodes": bool(include_episodes)}
        if memory_id:
            payload["id"] = str(memory_id)
        if query:
            payload["query"] = str(query)
        return self.call("forget", payload)

    def inspect(self, limit=200):
        """Return a read-only L0/L1/L2 view for the OpenHarness UI."""
        snapshot = self.maintenance_snapshot(limit)
        active = self.recall({
            "task": "查看已保存的报告写作 Memory",
            "audience": "总裁",
            "reportType": "研究报告",
            "limit": limit,
        })
        return {
            "status": "ok",
            "user": self.user,
            "writing_episodes": snapshot.get("writingEpisodes") or [],
            "pending_episodes": snapshot.get("pendingEpisodes") or [],
            "l1_memories": snapshot.get("l1Memories") or [],
            "l2_profiles": [
                item for item in active.get("memories") or []
                if item.get("layer") == "L2"
            ],
        }
