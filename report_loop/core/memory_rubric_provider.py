"""从 Markdown 记忆读取稳定的 L2B 快照。

只读：Report Loop 用它取本轮候选，从不写入记忆，也不启动任何服务。

记忆由 `report-memory-agent` 维护在用户主目录下可见的
`ReportAgentMemory/`（见 core/memory_store.py）。本模块把 `MEMORY.md` 里的
active L2B 转成 Resolution Judge 需要的快照结构，并按 ID 提供 L1 溯源。

`revision` 直接取自 `MEMORY.md`，是本轮冻结 Rubrics 的依据：同一 Loop 中
记忆即使更新，也不改变已经冻结的 compiled rubric；下一次新建 Loop 才读取
新 revision。
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .memory_store import (
    MemoryStore,
    MemoryStoreError,
    default_memory_root,
)

_SCOPE_PRIORITY = {"core": 0, "audience": 1, "project": 2}


def resolve_memory_root(explicit: Path | str | None = None) -> Path:
    """记忆根目录：显式传入 > 环境变量 > 主目录下的 ReportAgentMemory/。"""
    if explicit:
        return Path(explicit).expanduser()
    configured = os.environ.get("REPORT_AGENT_MEMORY_DIR")
    if configured:
        return Path(configured).expanduser()
    return default_memory_root()


class MemoryRubricProvider:
    """只读 L2B 快照提供者。"""

    def __init__(self, memory_data_dir: Path | str | None = None) -> None:
        self.memory_root = resolve_memory_root(memory_data_dir).resolve()
        self.store = MemoryStore(self.memory_root)

    # -- 溯源 ----------------------------------------------------------
    def load_sources(self, source_l1_ids: list[str]) -> list[dict[str, Any]]:
        """按 ID 读取 L1，供 Resolution Judge 首轮精确溯源。

        只返回请求的 Atom，不顺带返回其他内容；缺失的 ID 静默跳过，由调用方
        按需报告。
        """
        sources: list[dict[str, Any]] = []
        for atom_id in source_l1_ids or []:
            try:
                atom = self.store.read_atom(str(atom_id))
            except MemoryStoreError:
                continue
            if atom is None:
                continue
            sources.append({
                "id": atom["id"],
                "content": atom["content"],
                "scope": atom["scope"],
                "scopeValue": atom["scopeValue"] or None,
                "sourceEpisodeIds": atom["sourceEpisodeIds"],
            })
        return sources

    # -- 快照 ----------------------------------------------------------
    def load(self, *, audience: str = "", project: str = "") -> dict[str, Any]:
        """返回本轮 L2B 候选快照。

        记忆缺失、关闭或内容无法解析时都返回结构完整的快照，让 Report Loop
        退回 Base Rubrics 继续跑，而不是中断报告。
        """
        if not self.store.exists():
            return self._empty("empty")

        try:
            state = self.store.read_state()
        except MemoryStoreError as exc:
            # 内容损坏不阻断报告：只用 Base Rubrics，并把问题记进 warnings
            return self._empty("unavailable", warnings=[f"memory_unreadable:{exc}"])

        if not state.enabled:
            return self._empty("disabled", revision=str(state.revision))

        items: list[dict[str, Any]] = []
        warnings: list[str] = []
        for rubric in state.rubrics:
            if getattr(rubric, "scope", "") == "invalid":
                # 单条格式错误已在解析时跳过，这里只汇报
                warnings.append(f"memory_rubric_scope_value_missing:{rubric.id}")
                continue
            if rubric.scope != "core" and not rubric.scopeValue:
                warnings.append(f"memory_rubric_scope_value_missing:{rubric.id}")
                continue
            item: dict[str, Any] = {
                "id": rubric.id,
                "statement": rubric.statement,
                "scope": rubric.scope,
                "scopeValue": rubric.scopeValue or None,
                "sourceL1Ids": list(rubric.sourceL1Ids),
                "status": "active",
            }
            if rubric.dimensionCandidate:
                item["dimensionCandidate"] = dict(rubric.dimensionCandidate)
            items.append(item)

        # 同 ID 在不同 Scope 下并存时消歧，避免 compiled rubric 里 check 冲突
        counts: dict[str, int] = {}
        for item in items:
            counts[item["id"]] = counts.get(item["id"], 0) + 1
        for item in items:
            original = item["id"]
            if counts[original] <= 1:
                continue
            identity = "\0".join([
                original, str(item["scope"]), str(item["scopeValue"] or "")
            ])
            item["sourceMemoryId"] = original
            item["id"] = (
                f"{original}::{item['scope']}:"
                f"{hashlib.sha256(identity.encode('utf-8')).hexdigest()[:10]}"
            )

        items.sort(key=lambda item: (
            _SCOPE_PRIORITY.get(str(item["scope"]), 9),
            str(item["scopeValue"] or ""),
            str(item["id"]),
        ))

        return {
            "status": "loaded" if items else "empty",
            "revision": str(state.revision),
            "rubricSetVersion": f"memory/{state.revision}",
            "documents": [{
                "path": "MEMORY.md",
                "scope": "mixed",
                "scopeValue": None,
                "itemIds": [str(item["id"]) for item in items],
            }] if items else [],
            "items": items,
            "warnings": warnings,
        }

    @staticmethod
    def _empty(
        status: str,
        *,
        revision: str | None = None,
        warnings: list[str] | None = None,
    ) -> dict[str, Any]:
        return {
            "status": status,
            "revision": revision,
            "rubricSetVersion": None,
            "documents": [],
            "items": [],
            "warnings": list(warnings or []),
        }
