# -*- coding: utf-8 -*-
"""optimizer02.py — 策略 B:由 LLM 提出可验证的 instructions 结构化 patch。

与 harness/optimizer.py(翻开关搜索)并存,同一策略接口:
  propose(session, cur_skill, failures, context) -> proposal | None
  apply_proposal(cur_skill, proposal, new_version) -> SkillArtifact(freeform)

关键点:
  · 输入是"迭代记忆"(optimizer_pipeline.build_optimizer_context),把上一版的关键
    信息(当前最优全文/must_preserve/open_failures/history/tried_rejected/红线)全量
    传给 LLM,以避免回退。
  · 第一次 LLM 调用做 Diagnosis：阅读全量 Failure Inventory 和按 check
    平衡抽样的证据，输出全局归因并选择唯一主目标。
  · 第二次 LLM 调用做 Patch：只读主目标的 3–5 条可回放证据，
    只输出 add/replace/delete patch，平台在本地精确应用。
  · 总字符、净增长和 patch 操作数有硬预算；add 必须配对删除重复规则。
  · 根因属于 data/Judge/replay protocol/mixed 时直接阻断 Skill patch。
  · Patch LLM 必须逐条声明红线保留状态；平台校验所有值为 true。
    真实产物仍由 Gate 按 (case_id, check_id) 集合做最终新红线检查。
  · 采纳与否由异步 gate(optimizer_pipeline.evaluate_gate,在真实判分后)客观裁决,
    本模块只提议,不自采纳。
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from typing import Any, Dict, List, Optional

import llm_client
import optimizer_pipeline
import production_skill_policy


REQUIRES_ASYNC_GATE = True
IS_OFFLINE = False


# ---------------- 提议(改写) ----------------

def _call_rewrite_llm(
    prompt: str,
    llm_backend: str = "workbuddy",
    llm_model: str | None = None,
    llm_reasoning_effort: str | None = None,
    max_tokens=None,
) -> str:
    """Optimizer 两阶段共用的长超时、有限重试调用封装。"""
    if max_tokens is None:
        max_tokens = os.environ.get(
            "LLM_OPTIMIZER_MAX_TOKENS",
            "12000",
        )
    return llm_client.call_llm(
        prompt,
        timeout_seconds=os.environ.get(
            "LLM_REWRITE_TIMEOUT_SECONDS",
            "600",
        ),
        retries=os.environ.get("LLM_REWRITE_RETRIES", "2"),
        backend=llm_backend,
        model=llm_model,
        reasoning_effort=llm_reasoning_effort,
        max_tokens=max_tokens,
    )


def _compact_failure(item: Dict[str, Any]) -> Dict[str, Any]:
    """给 LLM 的 check 级摘要；完整 case 集合仍保留在本地 context/log。"""
    keys = (
        "check_id",
        "pattern_id",
        "dimension",
        "redline",
        "priority",
        "miss_count",
        "partial_count",
        "failure_mass",
        "affected_case_count",
        "replayable_evidence_count",
        "rounds_since_last_targeted",
        "consecutive_target_count",
        "recent_rejection_count",
        "cooldown_recommended",
    )
    return {key: item.get(key) for key in keys}


def _compact_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """限制无界历史增长，只传最近 12 轮的决策必要字段。"""
    compact = []
    for item in (history or [])[-12:]:
        reasons = item.get("verdict_reasons") or {}
        if isinstance(reasons, dict):
            reason_summary = {
                "message": reasons.get("message"),
                "new_redline_failure_count": len(
                    reasons.get("new_redline_failure_keys") or []
                ),
                "regressed_dims": reasons.get("regressed_dims") or [],
            }
        else:
            reason_summary = {"message": str(reasons or "")}
        compact.append({
            "version": item.get("version"),
            "parent": item.get("parent"),
            "change_summary": item.get("change_summary"),
            "targets": item.get("targets") or [],
            "candidate_state": item.get("candidate_state"),
            "adopted": item.get("adopted"),
            "verdict": item.get("verdict"),
            "overall_delta": item.get("overall_delta"),
            "verdict_summary": reason_summary,
        })
    return compact


def _render_diagnosis_prompt(context: Dict[str, Any]) -> str:
    inventory = [
        _compact_failure(item)
        for item in context.get("failure_inventory") or []
    ]
    candidate_keys = {
        (
            str(item.get("check_id") or ""),
            str(item.get("pattern_id") or ""),
        )
        for item in context.get("diagnosis_candidates") or []
    }
    diagnosis_context = {
        "requirement": context.get("requirement"),
        "rubric": context.get("rubric"),
        "current_best": context.get("current_best"),
        "must_preserve": context.get("must_preserve"),
        "failure_inventory": inventory,
        "diagnosis_candidates": [{
            key: item.get(key)
            for key in (
                "check_id",
                "pattern_id",
                "redline",
                "failure_mass",
                "affected_case_count",
                "replayable_evidence_count",
                "cooldown_recommended",
            )
        } for item in inventory
            if (str(item.get("check_id")), str(item.get("pattern_id")))
            in candidate_keys
        ],
        "diagnosis_evidence": context.get("diagnosis_evidence"),
        "history": _compact_history(context.get("history") or []),
        "guardrails": context.get("guardrails"),
        "root_cause_policy": context.get("root_cause_policy"),
    }
    ctx_json = json.dumps(
        diagnosis_context,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "\n".join([
        "你是调研洞察报告 Skill 的 Diagnosis LLM。",
        "你只负责全局诊断、跨 case 归因和选择本轮唯一主目标；不得输出 patch。",
        "",
        "failure_inventory 是全量 case 的 check 级失败汇总，不得只关注 T1。",
        "diagnosis_candidates 是有足够回放证据且已做冷却去偏的主要候选；selected_target 必须从中选且只选一个。",
        "diagnosis_evidence 已按 check 等额抽样。归因必须引用其中的 evidence_id，不得编造。",
        "",
        "归因规则：",
        "1. diagnoses 汇总所有主要候选，区分共性 Skill 缺陷与 data/judge/replay_protocol 问题。",
        "2. 孤立 case 不得升格为全局 Skill 根因；判定 skill 根因必须有 3条同 check 的可回放证据。",
        "3. 结合 history：连续修改却无收益或被拒的 check 不应再次垄断。",
        "4. selected_target.root_cause_type 非 skill 时，平台将停止，不进入 Patch LLM。",
        "5. deferred_failures 必须逐项列出 diagnosis_candidates 中除 selected_target 外的全部候选；check_id 和 pattern_id 必须逐字复制，不得改名、归并、遗漏或重复。",
        "",
        "## Diagnosis 上下文",
        ctx_json,
        "",
        "## 输出格式(务必严格遵守):",
        "把结果放进一个 ```json 代码块里,代码块内是**单个合法 JSON 对象**,代码块外不要有任何文字。",
        "```json",
        json.dumps({
            "diagnoses": [{
                "check_id": "T2",
                "pattern_id": "trace_faithfulness",
                "root_cause_type": "skill|data|judge|replay_protocol|mixed",
                "rationale": "<跨 case 根因>",
                "confidence": "high|medium|low",
                "evidence_ids": ["EXP-06", "EXP-07", "EXP-08"],
            }],
            "selected_target": {
                "check_id": "T2",
                "pattern_id": "trace_faithfulness",
                "root_cause_type": "skill|data|judge|replay_protocol|mixed",
                "rationale": "<为什么本轮优先该目标>",
                "confidence": "high|medium|low",
                "evidence_ids": ["EXP-06", "EXP-07", "EXP-08"],
            },
            "deferred_failures": [{
                "check_id": "T1",
                "pattern_id": "trace_evidence_fabrication",
                "reason": "<为什么本轮不修>",
            }],
        }, ensure_ascii=False),
        "```",
    ])


def _render_patch_prompt(
    context: Dict[str, Any],
    diagnosis: Dict[str, Any],
    target_evidence: List[Dict[str, Any]],
) -> str:
    selected = diagnosis.get("selected_target") or {}
    diagnosed_target = next(
        (
            item for item in diagnosis.get("diagnoses") or []
            if item.get("check_id") == selected.get("check_id")
            and item.get("pattern_id") == selected.get("pattern_id")
        ),
        {},
    )
    diagnosis_rationale = str(diagnosed_target.get("rationale") or "")
    if production_skill_policy.forbidden_metadata_hits(diagnosis_rationale):
        diagnosis_rationale = (
            "可回放样例显示当前生成规则未稳定满足选中的生产内容要求。"
        )
    redline_ids = [
        str(item.get("id"))
        for item in ((context.get("guardrails") or {}).get("redline_checks") or [])
        if item.get("id")
    ]
    patch_context = {
        "current_skill": {
            key: (context.get("current_best") or {}).get(key)
            for key in (
                "version",
                "requirement_contract",
                "instructions_text",
            )
        },
        "production_constraints": {
            "selected_quality_requirement": (
                context.get("production_requirement_by_check") or {}
            ).get(str(selected.get("check_id") or ""), ""),
            "mandatory_requirements": context.get(
                "mandatory_production_requirements"
            ) or [],
            "structure_frozen": (
                (context.get("guardrails") or {}).get("structure_frozen")
            ),
            "requirement_contract_frozen": (
                (context.get("guardrails") or {}).get(
                    "requirement_contract_frozen"
                )
            ),
            "no_reward_hack": (
                (context.get("guardrails") or {}).get("no_reward_hack")
            ),
            "forbidden_metadata": [
                "rubric/check/score/weight",
                "Gate/champion/holdout/overall",
                "候选采纳、拒绝或回退策略",
                "平台或评测系统名称",
            ],
        },
        "patch_constraints": context.get("patch_constraints"),
        "target_diagnosis": {
            "root_cause_type": diagnosed_target.get("root_cause_type"),
            "rationale": diagnosis_rationale,
            "confidence": diagnosed_target.get("confidence"),
            "evidence_ids": list(diagnosed_target.get("evidence_ids") or []),
        },
        "experiment_key": {
            "check_id": selected.get("check_id"),
            "pattern_id": selected.get("pattern_id"),
        },
        "harness_declarations": {
            "preservation_keys": redline_ids,
        },
        "target_evidence": target_evidence,
    }
    ctx_json = json.dumps(
        patch_context,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "\n".join([
        "你是调研洞察报告 Skill 的 Patch LLM。Diagnosis LLM 已选定唯一主目标。",
        "你不得重新选目标，只能针对 selected_quality_requirement 设计一个可证伪机制实验。",
        "",
        "硬约束：",
        "1. 只输出 patch.add/replace/delete，禁止输出改写后全文。",
        "2. experiment.examples 选用 target_evidence 中 3–5 条，必须包含 target_diagnosis.evidence_ids 的全部证据；五个证据字段必须逐字复制。",
        "3. examples 和 success_criteria 的 check_id 必须全部等于 experiment_key.check_id；这个键只用于 Harness 实验记录，不得写入 patch 文本。",
        "4. 只做局部增量改写；不堆规则、不 reward-hack、不复述需求原文。",
        "5. 严格遵守 patch_constraints。每个 add 必须配对删除一条重复规则。",
        "6. redline_preservation 只是 Harness 内部声明，必须包含全部键且值为 true：%s；这些键不得写入 patch 文本。" % ", ".join(redline_ids),
        "7. patch 的 add/replace 文本只能包含生产可执行内容，不得出现任何评分、权重、Gate、champion、holdout 或采纳策略。",
        "",
        "## Patch 上下文",
        ctx_json,
        "",
        "## 输出格式",
        "只输出一个 ```json 代码块，代码块内是单个合法 JSON 对象。",
        "```json",
        json.dumps({
            "experiment": {
                "hypothesis": "<单一可证伪假设>",
                "examples": [{
                    "evidence_id": "EXP-06",
                    "case_id": "<逐字复制>",
                    "check_id": "<逐字复制>",
                    "report_sentence": "<逐字复制>",
                    "evidence": "<逐字复制>",
                    "judge_verdict": "<逐字复制>",
                    "expected_change": "<patch 应如何改变该句>",
                }],
                "success_criteria": [{
                    "check_id": "<experiment_key.check_id>",
                    "expected": "<可测量变化>",
                }],
                "rollback_condition": "<证伪条件>",
            },
            "patch": {
                "add": [{
                    "after": "<唯一原文锚点或 __END__>",
                    "text": "<新规则>",
                    "paired_delete": "<本轮 delete 中的重复规则原文>",
                }],
                "replace": [{"old_text": "<唯一原文>", "new_text": "<替换文本>"}],
                "delete": [{"old_text": "<唯一原文>"}],
            },
            "change_summary": "<一两句>",
            "preserved": ["<保留的规则及原因>"],
            "redline_preservation": {check_id: True for check_id in redline_ids},
            "self_check_no_hack": True,
        }, ensure_ascii=False),
        "```",
    ])


def _reject(session, code: str, reason: str, **details) -> None:
    session.opt_history.append({
        "target": "instructions_patch",
        "result": "blocked",
        "error_code": code,
        "reason": reason,
        **details,
    })


_ROOT_CAUSE_TYPES = {"skill", "data", "judge", "replay_protocol", "mixed"}
_CONFIDENCE_LEVELS = {"high", "medium", "low"}


def _validate_diagnosis(
    parsed: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    """校验 Diagnosis 是否覆盖全部候选，且唯一主目标有同 check 证据。"""
    candidates = {
        (
            str(item.get("check_id") or ""),
            str(item.get("pattern_id") or ""),
        ): item
        for item in context.get("diagnosis_candidates") or []
        if item.get("check_id") and item.get("pattern_id")
    }
    if not candidates:
        raise ValueError("diagnosis_candidates 为空")
    available = {
        str(item.get("evidence_id")): item
        for item in context.get("diagnosis_evidence") or []
        if item.get("evidence_id")
    }

    def normalize(item: Any, label: str) -> Dict[str, Any]:
        if not isinstance(item, dict):
            raise ValueError("%s 必须是对象" % label)
        check_id = str(item.get("check_id") or "").strip()
        pattern_id = str(item.get("pattern_id") or "").strip()
        key = (check_id, pattern_id)
        if key not in candidates:
            raise ValueError("%s 必须来自 diagnosis_candidates" % label)
        root_type = str(item.get("root_cause_type") or "").strip()
        if root_type not in _ROOT_CAUSE_TYPES:
            raise ValueError("%s.root_cause_type 非法" % label)
        rationale = str(item.get("rationale") or "").strip()
        confidence = str(item.get("confidence") or "").strip()
        if not rationale or confidence not in _CONFIDENCE_LEVELS:
            raise ValueError("%s 必须含 rationale 和合法 confidence" % label)
        evidence_ids = [
            str(value) for value in item.get("evidence_ids") or [] if value
        ]
        if not evidence_ids or len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("%s.evidence_ids 不得为空或重复" % label)
        for evidence_id in evidence_ids:
            packet = available.get(evidence_id)
            if not packet:
                raise ValueError("%s 引用了未知 evidence_id" % label)
            if (
                str(packet.get("check_id") or "") != check_id
                or str(packet.get("pattern_id") or "") != pattern_id
            ):
                raise ValueError("%s 的证据必须与候选 check/pattern 一致" % label)
        return {
            "check_id": check_id,
            "pattern_id": pattern_id,
            "root_cause_type": root_type,
            "rationale": rationale,
            "confidence": confidence,
            "evidence_ids": evidence_ids,
        }

    raw_diagnoses = parsed.get("diagnoses")
    if not isinstance(raw_diagnoses, list) or not raw_diagnoses:
        raise ValueError("diagnoses 必须是非空数组")
    diagnoses = [
        normalize(item, "diagnoses[%d]" % index)
        for index, item in enumerate(raw_diagnoses)
    ]
    if len(diagnoses) != len(candidates):
        raise ValueError("diagnoses 不得遗漏或重复 diagnosis_candidates")
    diagnosed_keys = {
        (item["check_id"], item["pattern_id"]) for item in diagnoses
    }
    if diagnosed_keys != set(candidates):
        raise ValueError("diagnoses 必须逐项覆盖全部 diagnosis_candidates")

    selected = normalize(parsed.get("selected_target"), "selected_target")
    matching = [
        item for item in diagnoses
        if item["check_id"] == selected["check_id"]
        and item["pattern_id"] == selected["pattern_id"]
    ]
    if len(matching) != 1:
        raise ValueError("selected_target 必须对应唯一 diagnoses 项")
    diagnosed_target = matching[0]
    if selected["root_cause_type"] != diagnosed_target["root_cause_type"]:
        raise ValueError("selected_target 不得改变对应 diagnosis 的根因类型")
    if selected["confidence"] != diagnosed_target["confidence"]:
        raise ValueError("selected_target 不得改变对应 diagnosis 的置信度")
    if set(selected["evidence_ids"]) != set(diagnosed_target["evidence_ids"]):
        raise ValueError("selected_target 必须复用对应 diagnosis 的证据集合")
    minimum = int(
        (context.get("patch_constraints") or {}).get(
            "min_experiment_examples",
            3,
        )
    )
    if selected["root_cause_type"] == "skill" and len(selected["evidence_ids"]) < minimum:
        raise ValueError("skill 根因的 selected_target 至少需要 %d 条证据" % minimum)

    deferred = parsed.get("deferred_failures") or []
    if not isinstance(deferred, list):
        raise ValueError("deferred_failures 必须是数组")
    normalized_deferred = []
    for item in deferred:
        if not isinstance(item, dict):
            raise ValueError("deferred_failures 每项必须是对象")
        check_id = str(item.get("check_id") or "").strip()
        pattern_id = str(item.get("pattern_id") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not check_id or not pattern_id or not reason:
            raise ValueError(
                "deferred_failures 必须含 check_id/pattern_id/reason"
            )
        normalized_deferred.append({
            "check_id": check_id,
            "pattern_id": pattern_id,
            "reason": reason,
        })
    deferred_keys = {
        (item["check_id"], item["pattern_id"])
        for item in normalized_deferred
    }
    selected_key = (selected["check_id"], selected["pattern_id"])
    if (
        len(normalized_deferred) != len(candidates) - 1
        or deferred_keys != set(candidates) - {selected_key}
    ):
        raise ValueError("deferred_failures 必须解释除主目标外的全部候选")
    return {
        "diagnoses": diagnoses,
        "selected_target": selected,
        "deferred_failures": normalized_deferred,
    }


def _target_evidence(
    context: Dict[str, Any],
    selected: Dict[str, Any],
) -> List[Dict[str, Any]]:
    matching = [
        item for item in context.get("evidence_catalog") or []
        if str(item.get("check_id") or "") == selected["check_id"]
        and str(item.get("pattern_id") or "") == selected["pattern_id"]
    ]
    maximum = int(
        (context.get("patch_constraints") or {}).get(
            "max_experiment_examples",
            5,
        )
    )
    return matching[:maximum]


def _validate_redline_preservation(
    parsed: Dict[str, Any],
    rubric: Dict[str, Any],
) -> Dict[str, bool]:
    expected = [
        item["id"] for item in optimizer_pipeline._redline_checks(rubric)
    ]
    raw = parsed.get("redline_preservation")
    if not isinstance(raw, dict):
        raise ValueError("redline_preservation 必须是对象")
    invalid = [check_id for check_id in expected if raw.get(check_id) is not True]
    if invalid:
        raise ValueError("红线保留声明缺失或未通过: %s" % ", ".join(invalid))
    return {check_id: True for check_id in expected}


def _validate_root_cause(parsed: Dict[str, Any]) -> Dict[str, Any]:
    root = parsed.get("root_cause")
    if not isinstance(root, dict):
        raise ValueError("root_cause 必须是对象")
    root_type = str(root.get("type") or "").strip()
    if root_type not in _ROOT_CAUSE_TYPES:
        raise ValueError("root_cause.type 非法")
    if not str(root.get("rationale") or "").strip():
        raise ValueError("root_cause.rationale 不得为空")
    evidence_ids = root.get("evidence_ids")
    if not isinstance(evidence_ids, list) or not evidence_ids:
        raise ValueError("root_cause.evidence_ids 不得为空")
    return {
        "type": root_type,
        "rationale": str(root["rationale"]).strip(),
        "evidence_ids": [str(item) for item in evidence_ids if item],
    }


def _validate_experiment(
    parsed: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    experiment = parsed.get("experiment")
    if not isinstance(experiment, dict):
        raise ValueError("experiment 必须是对象")
    available = {
        str(item.get("evidence_id")): item
        for item in context.get("experiment_evidence") or []
        if item.get("evidence_id")
    }
    examples = experiment.get("examples")
    constraints = context.get("patch_constraints") or {}
    minimum = int(constraints.get("min_experiment_examples") or 3)
    maximum = int(constraints.get("max_experiment_examples") or 5)
    if not isinstance(examples, list) or not minimum <= len(examples) <= maximum:
        raise ValueError("experiment.examples 必须为 %d–%d 条" % (minimum, maximum))
    exact_fields = (
        "case_id",
        "check_id",
        "report_sentence",
        "evidence",
        "judge_verdict",
    )
    normalized = []
    seen = set()
    for example in examples:
        if not isinstance(example, dict):
            raise ValueError("experiment.examples 每项必须是对象")
        evidence_id = str(example.get("evidence_id") or "")
        packet = available.get(evidence_id)
        if not packet or evidence_id in seen:
            raise ValueError("experiment.examples 引用了未知或重复 evidence_id")
        for field in exact_fields:
            if str(example.get(field) or "") != str(packet.get(field) or ""):
                raise ValueError("%s.%s 未逐字复制证据包" % (evidence_id, field))
        expected_change = str(example.get("expected_change") or "").strip()
        if not expected_change:
            raise ValueError("%s.expected_change 不得为空" % evidence_id)
        normalized.append({
            "evidence_id": evidence_id,
            **{field: packet[field] for field in exact_fields},
            "expected_change": expected_change,
        })
        seen.add(evidence_id)
    hypothesis = str(experiment.get("hypothesis") or "").strip()
    rollback = str(experiment.get("rollback_condition") or "").strip()
    criteria = experiment.get("success_criteria")
    if not hypothesis or not rollback:
        raise ValueError("experiment 必须给出 hypothesis 和 rollback_condition")
    if not isinstance(criteria, list) or not criteria or len(criteria) > 3:
        raise ValueError("success_criteria 必须为 1–3 条")
    normalized_criteria = []
    for criterion in criteria:
        if not isinstance(criterion, dict):
            raise ValueError("success_criteria 每项必须是对象")
        check_id = str(criterion.get("check_id") or "").strip()
        expected = str(criterion.get("expected") or "").strip()
        if not check_id or not expected:
            raise ValueError("success_criteria 必须含 check_id/expected")
        normalized_criteria.append({"check_id": check_id, "expected": expected})
    return {
        "hypothesis": hypothesis,
        "examples": normalized,
        "success_criteria": normalized_criteria,
        "rollback_condition": rollback,
    }


def _unique_span(text: str, needle: str, label: str) -> tuple[int, int]:
    if not needle:
        raise ValueError("%s 不得为空" % label)
    if text.count(needle) != 1:
        raise ValueError("%s 必须在父版中唯一出现" % label)
    start = text.index(needle)
    return start, start + len(needle)


def _compile_patch(
    parent_text: str,
    patch: Dict[str, Any],
    constraints: Dict[str, Any],
) -> tuple[str, Dict[str, Any], Dict[str, Any]]:
    """校验并精确应用 add/replace/delete，返回候选全文、标准 patch 和预算。"""
    if not isinstance(patch, dict):
        raise ValueError("patch 必须是对象")
    additions = patch.get("add") or []
    replacements = patch.get("replace") or []
    deletions = patch.get("delete") or []
    if not all(isinstance(items, list) for items in (additions, replacements, deletions)):
        raise ValueError("patch.add/replace/delete 必须是数组")
    operation_count = len(additions) + len(replacements) + len(deletions)
    max_operations = int(constraints.get("max_patch_operations") or 6)
    if operation_count < 1 or operation_count > max_operations:
        raise ValueError("patch 操作数必须在 1–%d" % max_operations)

    normalized_delete = []
    delete_texts = set()
    edits = []
    for index, item in enumerate(deletions):
        if not isinstance(item, dict):
            raise ValueError("patch.delete[%d] 必须是对象" % index)
        old = str(item.get("old_text") or "")
        if old == parent_text:
            raise ValueError("patch 不得通过 delete 替换整个父版全文")
        start, end = _unique_span(parent_text, old, "delete.old_text")
        normalized_delete.append({"old_text": old})
        delete_texts.add(old)
        edits.append((start, end, "", "delete"))

    normalized_replace = []
    for index, item in enumerate(replacements):
        if not isinstance(item, dict):
            raise ValueError("patch.replace[%d] 必须是对象" % index)
        old = str(item.get("old_text") or "")
        new = str(item.get("new_text") or "")
        if old == parent_text:
            raise ValueError("patch 不得通过 replace 替换整个父版全文")
        if not new or new == old:
            raise ValueError("replace.new_text 必须非空且与原文不同")
        start, end = _unique_span(parent_text, old, "replace.old_text")
        normalized_replace.append({"old_text": old, "new_text": new})
        edits.append((start, end, new, "replace"))

    occupied = sorted((start, end, kind) for start, end, _, kind in edits)
    for left, right in zip(occupied, occupied[1:]):
        if right[0] < left[1]:
            raise ValueError("patch 删除/替换区间不得重叠")

    normalized_add = []
    add_positions = set()
    paired_deletes_used = set()
    for index, item in enumerate(additions):
        if not isinstance(item, dict):
            raise ValueError("patch.add[%d] 必须是对象" % index)
        after = str(item.get("after") or "")
        new = str(item.get("text") or "")
        paired = str(item.get("paired_delete") or "")
        if not new or new in parent_text:
            raise ValueError("add.text 必须是父版中尚不存在的新文本")
        if not paired or paired not in delete_texts:
            raise ValueError("add 必须用 paired_delete 配对本轮删除的重复规则")
        if paired in paired_deletes_used:
            raise ValueError("每条被删重复规则只能配对一个 add")
        if after == "__END__":
            position = len(parent_text)
        else:
            _, position = _unique_span(parent_text, after, "add.after")
        if position in add_positions:
            raise ValueError("多个 add 不得共用同一插入位置")
        if any(start < position < end for start, end, _, _ in edits):
            raise ValueError("add.after 不得位于被删除/替换区间内")
        add_positions.add(position)
        paired_deletes_used.add(paired)
        normalized_add.append({
            "after": after,
            "text": new,
            "paired_delete": paired,
        })
        edits.append((position, position, new, "add"))

    candidate = parent_text
    for start, end, replacement, _ in sorted(
        edits,
        key=lambda item: (item[0], item[1]),
        reverse=True,
    ):
        candidate = candidate[:start] + replacement + candidate[end:]
    if not candidate.strip() or candidate == parent_text:
        raise ValueError("patch 应用后没有有效变化")

    max_total = int(constraints.get("max_instruction_chars") or 8000)
    max_growth = int(constraints.get("max_net_growth_chars") or 200)
    net_growth = len(candidate) - len(parent_text)
    if len(candidate) > max_total:
        raise ValueError("候选总字符 %d 超过预算 %d" % (len(candidate), max_total))
    if net_growth > max_growth:
        raise ValueError("候选净增长 %d 超过预算 %d" % (net_growth, max_growth))
    normalized_patch = {
        "add": normalized_add,
        "replace": normalized_replace,
        "delete": normalized_delete,
    }
    budget = {
        "parent_chars": len(parent_text),
        "candidate_chars": len(candidate),
        "net_growth_chars": net_growth,
        "max_instruction_chars": max_total,
        "max_net_growth_chars": max_growth,
        "operation_count": operation_count,
        "max_patch_operations": max_operations,
    }
    return candidate, normalized_patch, budget


def propose(
    session,
    cur_skill,
    failures,
    context,
    llm_backend: str = "workbuddy",
    llm_model: str | None = None,
    llm_reasoning_effort: str | None = None,
) -> Optional[Dict[str, Any]]:
    """Diagnosis LLM 选唯一目标，Patch LLM 生成局部 patch。"""
    constraints = dict(context.get("patch_constraints") or {})
    diagnosis_evidence = list(context.get("diagnosis_evidence") or [])
    diagnosis_candidates = list(context.get("diagnosis_candidates") or [])
    minimum_examples = int(constraints.get("min_experiment_examples") or 3)
    deterministic_signals = (
        (context.get("root_cause_policy") or {}).get("deterministic_signals")
        or []
    )
    blocking_signals = [
        item for item in deterministic_signals
        if item.get("blocks_skill_patch")
    ]
    if blocking_signals:
        _reject(
            session,
            "non_skill_root_cause",
            "已有确定性 data/Judge/回放协议信号，禁止改 Skill",
            model_calls=0,
            stage="precheck",
            root_cause_signals=blocking_signals,
        )
        return None
    if not diagnosis_candidates or len(diagnosis_evidence) < minimum_examples:
        _reject(
            session,
            "insufficient_experiment_evidence",
            "没有主要 failure 同时具备至少 %d 条可回放证据，不允许盲改 Skill"
            % minimum_examples,
            model_calls=0,
            stage="precheck",
            evidence_count=len(diagnosis_evidence),
            diagnosis_candidate_count=len(diagnosis_candidates),
        )
        return None

    diagnosis_prompt = _render_diagnosis_prompt(context)
    diagnosis_max_tokens = os.environ.get(
        "LLM_DIAGNOSIS_MAX_TOKENS",
        "6000",
    )
    diagnosis_started = time.monotonic()
    try:
        diagnosis_raw = _call_rewrite_llm(
            diagnosis_prompt,
            llm_backend=llm_backend,
            llm_model=llm_model,
            llm_reasoning_effort=llm_reasoning_effort,
            max_tokens=diagnosis_max_tokens,
        )
    except llm_client.EmptyLLMResponseError as exc:
        diagnosis_duration_ms = int(
            (time.monotonic() - diagnosis_started) * 1000
        )
        diagnostic = {
            "target": "instructions_patch",
            "stage": "diagnosis",
            "result": "error",
            "reason": "Diagnosis LLM 返回空内容",
            "error_code": exc.error_code,
            "llm_backend": llm_backend,
            "llm_model": llm_model,
            "llm_reasoning_effort": llm_reasoning_effort,
            "model_calls": 1,
            "diagnosis_prompt_chars": len(diagnosis_prompt),
            "diagnosis_prompt_sha256": hashlib.sha256(
                diagnosis_prompt.encode("utf-8")
            ).hexdigest(),
            "diagnosis_duration_ms": diagnosis_duration_ms,
            "diagnosis_max_tokens": diagnosis_max_tokens,
            "llm_diagnostics": exc.diagnostics,
        }
        session.opt_history.append(diagnostic)
        exc.optimizer_trace = diagnostic
        raise
    diagnosis_duration_ms = int(
        (time.monotonic() - diagnosis_started) * 1000
    )
    diagnosis_trace = {
        "model_calls": 1,
        "stage": "diagnosis",
        "diagnosis_prompt_chars": len(diagnosis_prompt),
        "diagnosis_prompt_sha256": hashlib.sha256(
            diagnosis_prompt.encode("utf-8")
        ).hexdigest(),
        "diagnosis_response_chars": len(diagnosis_raw or ""),
        "diagnosis_response_sha256": hashlib.sha256(
            (diagnosis_raw or "").encode("utf-8")
        ).hexdigest(),
        "diagnosis_duration_ms": diagnosis_duration_ms,
        "diagnosis_max_tokens": diagnosis_max_tokens,
        "diagnosis_candidate_count": len(diagnosis_candidates),
        "diagnosis_inventory_count": len(
            context.get("failure_inventory") or []
        ),
    }
    diagnosis_parsed = llm_client.extract_json(diagnosis_raw)
    if not isinstance(diagnosis_parsed, dict):
        _reject(
            session,
            "invalid_failure_diagnosis",
            "Diagnosis LLM 必须输出结构化 JSON",
            **diagnosis_trace,
        )
        return None
    diagnosis_allowed_keys = {
        "diagnoses",
        "selected_target",
        "deferred_failures",
    }
    diagnosis_extra_keys = set(diagnosis_parsed) - diagnosis_allowed_keys
    if diagnosis_extra_keys:
        _reject(
            session,
            "invalid_failure_diagnosis",
            "Diagnosis LLM 不得输出 Patch/实验或其它额外字段: %s"
            % ", ".join(sorted(diagnosis_extra_keys)),
            diagnosis_parsed_keys=list(diagnosis_parsed),
            **diagnosis_trace,
        )
        return None
    try:
        diagnosis = _validate_diagnosis(diagnosis_parsed, context)
    except ValueError as exc:
        _reject(
            session,
            "invalid_failure_diagnosis",
            str(exc),
            diagnosis_parsed_keys=list(diagnosis_parsed),
            **diagnosis_trace,
        )
        return None

    selected = diagnosis["selected_target"]
    diagnosed_target = next(
        item for item in diagnosis["diagnoses"]
        if item["check_id"] == selected["check_id"]
        and item["pattern_id"] == selected["pattern_id"]
    )
    root_cause = {
        "type": diagnosed_target["root_cause_type"],
        "rationale": diagnosed_target["rationale"],
        "evidence_ids": list(diagnosed_target["evidence_ids"]),
    }
    diagnosis_trace.update({
        "diagnosis_selected_check_id": selected["check_id"],
        "diagnosis_selected_pattern_id": selected["pattern_id"],
        "diagnosis_root_cause_type": selected["root_cause_type"],
    })
    if root_cause["type"] != "skill":
        _reject(
            session,
            "non_skill_root_cause",
            "Diagnosis 判定根因属于 %s，禁止通过改 Skill 修复"
            % root_cause["type"],
            root_cause=root_cause,
            diagnosis=diagnosis,
            **diagnosis_trace,
        )
        return None

    target_evidence = _target_evidence(context, selected)
    if len(target_evidence) < minimum_examples:
        _reject(
            session,
            "insufficient_target_evidence",
            "Diagnosis 选定的 %s 可回放证据不足 %d 条"
            % (selected["check_id"], minimum_examples),
            diagnosis=diagnosis,
            evidence_count=len(target_evidence),
            **diagnosis_trace,
        )
        return None

    patch_prompt = _render_patch_prompt(context, diagnosis, target_evidence)
    patch_max_tokens = os.environ.get(
        "LLM_OPTIMIZER_MAX_TOKENS",
        "12000",
    )
    patch_started = time.monotonic()
    try:
        patch_raw = _call_rewrite_llm(
            patch_prompt,
            llm_backend=llm_backend,
            llm_model=llm_model,
            llm_reasoning_effort=llm_reasoning_effort,
            max_tokens=patch_max_tokens,
        )
    except llm_client.EmptyLLMResponseError as exc:
        patch_duration_ms = int(
            (time.monotonic() - patch_started) * 1000
        )
        diagnostic = {
            "target": "instructions_patch",
            "stage": "patch",
            "result": "error",
            "reason": "Patch LLM 返回空内容",
            "error_code": exc.error_code,
            "llm_backend": llm_backend,
            "llm_model": llm_model,
            "llm_reasoning_effort": llm_reasoning_effort,
            **diagnosis_trace,
            "model_calls": 2,
            "stage": "patch",
            "diagnosis": diagnosis,
            "patch_prompt_chars": len(patch_prompt),
            "patch_prompt_sha256": hashlib.sha256(
                patch_prompt.encode("utf-8")
            ).hexdigest(),
            "patch_duration_ms": patch_duration_ms,
            "patch_max_tokens": patch_max_tokens,
            "llm_diagnostics": exc.diagnostics,
        }
        session.opt_history.append(diagnostic)
        exc.optimizer_trace = diagnostic
        raise
    patch_duration_ms = int(
        (time.monotonic() - patch_started) * 1000
    )
    patch_trace = {
        **diagnosis_trace,
        "model_calls": 2,
        "stage": "patch",
        "patch_prompt_chars": len(patch_prompt),
        "patch_prompt_sha256": hashlib.sha256(
            patch_prompt.encode("utf-8")
        ).hexdigest(),
        "patch_response_chars": len(patch_raw or ""),
        "patch_response_sha256": hashlib.sha256(
            (patch_raw or "").encode("utf-8")
        ).hexdigest(),
        "patch_duration_ms": patch_duration_ms,
        "patch_max_tokens": patch_max_tokens,
    }
    parsed = llm_client.extract_json(patch_raw)
    forbidden_patch_keys = {
        "instructions_text",
        "diagnoses",
        "selected_target",
        "targets_failures",
        "root_cause",
    }
    returned_forbidden = (
        set(parsed) & forbidden_patch_keys
        if isinstance(parsed, dict) else set()
    )
    if not isinstance(parsed, dict) or returned_forbidden:
        _reject(
            session,
            "invalid_structured_patch",
            "Patch LLM 只能输出结构化 patch，不得返回全文或重新选择目标",
            raw_len=len(patch_raw or ""),
            parsed_keys=(list(parsed.keys()) if isinstance(parsed, dict) else None),
            forbidden_keys=sorted(returned_forbidden),
            diagnosis=diagnosis,
            **patch_trace,
        )
        return None

    patch_context = dict(context)
    patch_context["experiment_evidence"] = target_evidence
    try:
        experiment = _validate_experiment(parsed, patch_context)
        redline_preservation = _validate_redline_preservation(
            parsed,
            session.rubric,
        )
    except ValueError as exc:
        _reject(
            session,
            "invalid_experiment_design",
            str(exc),
            parsed_keys=list(parsed),
            diagnosis=diagnosis,
            **patch_trace,
        )
        return None
    example_ids = {item["evidence_id"] for item in experiment["examples"]}
    if any(
        item["check_id"] != selected["check_id"]
        for item in experiment["examples"]
    ):
        _reject(
            session,
            "patch_target_mismatch",
            "Patch 实验样例必须全部属于 Diagnosis 选定的 check",
            diagnosis=diagnosis,
            **patch_trace,
        )
        return None
    if any(
        item["check_id"] != selected["check_id"]
        for item in experiment["success_criteria"]
    ):
        _reject(
            session,
            "patch_target_mismatch",
            "Patch success_criteria 必须全部针对 Diagnosis 选定的 check",
            diagnosis=diagnosis,
            **patch_trace,
        )
        return None
    if not set(root_cause["evidence_ids"]).issubset(example_ids):
        _reject(
            session,
            "invalid_root_cause_evidence",
            "Diagnosis selected_target.evidence_ids 必须全部进入 Patch 实验样例",
            root_cause=root_cause,
            **patch_trace,
        )
        return None
    if not bool(parsed.get("self_check_no_hack")):
        _reject(
            session,
            "optimizer_self_check_failed",
            "self_check_no_hack 未通过",
            **patch_trace,
        )
        return None

    parent_text = production_skill_policy.sanitize_legacy_production_text(str(
        ((cur_skill.instructions or {}).get("prose") if cur_skill else "")
        or (context.get("current_best") or {}).get("instructions_text")
        or ""
    ))
    try:
        instructions_text, structured_patch, budget = _compile_patch(
            parent_text,
            parsed.get("patch"),
            constraints,
        )
    except ValueError as exc:
        _reject(
            session,
            "patch_validation_failed",
            str(exc),
            root_cause=root_cause,
            experiment_example_ids=sorted(example_ids),
            **patch_trace,
        )
        return None
    try:
        production_skill_policy.validate_production_text(instructions_text)
    except ValueError as exc:
        _reject(
            session,
            "production_metadata_leak",
            str(exc),
            root_cause=root_cause,
            experiment_example_ids=sorted(example_ids),
            **patch_trace,
        )
        return None

    targets = [selected["pattern_id"], selected["check_id"]]

    return {
        "target": "instructions_patch",
        "level": "L1_structured_patch",
        "patch": structured_patch,
        "diagnosis": diagnosis,
        "selected_target": selected,
        "root_cause": root_cause,
        "experiment": experiment,
        "redline_preservation": redline_preservation,
        "budget": budget,
        "_compiled_instructions_text": instructions_text,
        "change_summary": parsed.get("change_summary", ""),
        "targets_failures": targets,
        "preserved": parsed.get("preserved", []),
        "hypothesis": experiment["hypothesis"],
        "self_check_no_hack": bool(parsed.get("self_check_no_hack", False)),
        # 供 gate 用:把 targets 映射到受影响维度
        "affected_dims": _targets_to_dims(targets, failures),
        "change": "可验证结构化 patch: " + (parsed.get("change_summary", "") or "局部修改"),
        "_optimizer_trace": {
            **patch_trace,
            "llm_backend": llm_backend,
            "llm_model": llm_model,
            "llm_reasoning_effort": llm_reasoning_effort,
            # 保留 rewrite_* 别名，避免旧 UI/日志解析断裂。
            "rewrite_prompt_chars": len(patch_prompt),
            "rewrite_prompt_sha256": hashlib.sha256(
                patch_prompt.encode("utf-8")
            ).hexdigest(),
            "rewrite_response_chars": len(patch_raw or ""),
            "rewrite_response_sha256": hashlib.sha256(
                (patch_raw or "").encode("utf-8")
            ).hexdigest(),
            "rewrite_duration_ms": patch_duration_ms,
            "rewrite_max_tokens": patch_max_tokens,
            "root_cause_type": root_cause["type"],
            "experiment_example_count": len(experiment["examples"]),
            "experiment_evidence_ids": sorted(example_ids),
            "patch_add_count": len(structured_patch["add"]),
            "patch_replace_count": len(structured_patch["replace"]),
            "patch_delete_count": len(structured_patch["delete"]),
            "patch_operation_count": budget["operation_count"],
            "parent_instruction_chars": budget["parent_chars"],
            "candidate_instruction_chars": budget["candidate_chars"],
            "net_growth_chars": budget["net_growth_chars"],
            "max_instruction_chars": budget["max_instruction_chars"],
            "max_net_growth_chars": budget["max_net_growth_chars"],
        },
    }


def _targets_to_dims(targets, failures) -> List[str]:
    """把提议针对的 pattern_id/check_id 映射成受影响维度。"""
    dims = []
    by_id = {f.get("pattern_id"): f for f in (failures or [])}
    for t in targets or []:
        f = by_id.get(t)
        matched = [f] if f else [
            failure
            for failure in failures or []
            if any(
                str(evidence.get("check_id")) == str(t)
                for evidence in failure.get("evidence") or []
            )
        ]
        for failure in matched:
            for d in failure.get("affected_dims", []):
                if d and d not in dims:
                    dims.append(d)
    return dims


# ---------------- 应用(生成候选版本) ----------------

def apply_proposal(cur_skill, proposal: Dict[str, Any], new_version: str):
    """把已在本地校验并编译的 patch 落成候选版本。"""
    cand = copy.deepcopy(cur_skill)
    instr = dict(cand.instructions or {})
    instructions_text = proposal.get("_compiled_instructions_text")
    if not isinstance(instructions_text, str) or not instructions_text.strip():
        raise ValueError("proposal 缺少经校验的 _compiled_instructions_text")
    instr["prose"] = instructions_text
    instr["mode"] = "freeform"
    cand.instructions = instr
    cand.parent_version = cur_skill.version
    cand.version = new_version
    cand.changelog = "相对 %s: %s" % (cur_skill.version, proposal.get("change_summary", "LLM 改写"))
    return cand
