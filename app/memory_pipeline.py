# -*- coding: utf-8 -*-
"""One-model-call L0 -> L1 -> L2 writing-memory transaction."""
from __future__ import annotations

import copy
import json
from typing import Any, Callable, Dict

import llm_client
from memory_client import ResearchReportMemoryClient


def _prompt(feedback, context, snapshot, policy, forced_decision=None):
    payload = {
        "feedback": feedback,
        "context": context,
        "memory_snapshot": snapshot,
    }
    instructions = [
        "你是 research-report 的独立 Memory Agent。一次完成 L0、L1、L2 的规划，不修改 Rubrics 或报告。",
        "严格沿用下方 Memory Policy。根据反馈决定 pending/store/ignore；明确长期偏好或跨任务稳定证据才进入 L1。",
        "如写 L1，必须同时为每个受影响的 scope × dimension 输出更新后的 L2 Profile。",
        "memories 每项只能有一个 dimension，action 仅限 store/update/merge/skip；新 L1 必须提供唯一 operationRef。",
        "profiles.sourceRefs 使用 existing:<L1 ID> 或 new:<operationRef>。L2只能归纳最终有效 L1，不能创造新规则。",
        "只输出 JSON：{decision,episode,memories,profiles,summary}。",
        "decision=pending 时 memories/profiles 为空；decision=ignore 时 episode 可省略。",
    ]
    if forced_decision == "store":
        instructions.append(
            "用户已明确选择保存为稳定偏好：decision 必须为 store，"
            "并产出对应的原子 L1 规则和完整 L2 Profile。"
        )
    return "\n".join(instructions + [
        "\n## Memory Policy\n" + str(policy or ""),
        "\n## 输入\n" + json.dumps(payload, ensure_ascii=False, indent=2),
    ])


class MemoryPipeline:
    def __init__(self, client=None, user="local"):
        self.client = client or ResearchReportMemoryClient(user=user)

    @staticmethod
    def _episode(context):
        return {
            "task": str(context.get("task") or "报告写作反馈")[:500],
            "externalSourceId": str(context["external_source_id"])[:500],
            "sessionId": str(context.get("session_id") or "")[:200],
            "topic": str(context.get("topic") or "")[:200],
            "audience": str(context.get("audience") or "总裁")[:120],
            "reportType": str(context.get("report_type") or "研究报告")[:120],
            "stage": "report_feedback",
            "contextBefore": str(context.get("context_before") or "")[:4000],
            "contextAfter": str(context.get("context_after") or "")[:4000],
            "finalArtifact": str(context.get("final_artifact") or "")[:1000],
        }

    @staticmethod
    def _result(result, decision, model_config, summary=""):
        if result.get("status") in {"error", "conflict"}:
            raise ValueError(
                "Memory 写入失败: %s"
                % (result.get("reason") or result["status"])
            )
        return {
            "status": result.get("status"),
            "decision": decision,
            "episode_id": result.get("episodeId"),
            "written_ids": result.get("writtenIds") or [],
            "profiles_written": result.get("profilesWritten", 0),
            "dirty_profile_keys": result.get("dirtyProfileKeys") or [],
            "idempotent": bool(result.get("idempotent")),
            "summary": str(summary or "").strip(),
            "model_config": copy.deepcopy(model_config),
        }

    def store_pending(self, feedback, context, model_config):
        snapshot = self.client.maintenance_snapshot()
        result = self.client.capture({
            "feedback": str(feedback.get("content") or ""),
            "decision": "pending",
            "mode": "feedback",
            "trustedWritingFeedback": True,
            "episode": self._episode(context),
            "memories": [],
            "profiles": [],
            "snapshotRevision": snapshot.get("snapshotRevision"),
        })
        return self._result(
            result, "pending", model_config, "已按用户选择保存为 L0 待观察。"
        )

    def process(
        self,
        feedback: Dict[str, Any],
        context: Dict[str, Any],
        model_config: Dict[str, Any],
        call_model: Callable[..., str] | None = None,
        forced_decision: str | None = None,
    ):
        snapshot = self.client.maintenance_snapshot()
        policy = self.client.policy().get("instructions", "")
        caller = call_model or llm_client.call_llm
        raw = caller(
            _prompt(feedback, context, snapshot, policy, forced_decision),
            timeout_seconds="600",
            retries="2",
            backend=model_config.get("llm_backend"),
            model=model_config.get("llm_model"),
            reasoning_effort=model_config.get("llm_reasoning_effort"),
        )
        plan = llm_client.extract_json(raw)
        if not isinstance(plan, dict):
            raise ValueError("Memory Agent 未返回有效计划")
        decision = str(plan.get("decision") or "")
        if decision not in {"store", "pending", "ignore"}:
            raise ValueError("Memory Agent decision 非法")
        if forced_decision and decision != forced_decision:
            raise ValueError("Memory Agent 未遵循用户选择: %s" % forced_decision)
        episode = copy.deepcopy(plan.get("episode") or {})
        if decision != "ignore":
            episode.update(self._episode(context))
        payload = {
            "feedback": str(feedback.get("content") or ""),
            "decision": decision,
            "mode": "feedback",
            # Feedback Router and the user have already confirmed this belongs
            # to report-writing Memory; do not reject it a second time with a
            # narrower keyword gate in the storage runtime.
            "trustedWritingFeedback": True,
            "episode": episode or None,
            "memories": plan.get("memories") or [],
            "profiles": plan.get("profiles") or [],
            "snapshotRevision": snapshot.get("snapshotRevision"),
        }
        result = self.client.capture(payload)
        return self._result(
            result, decision, model_config, plan.get("summary")
        )
