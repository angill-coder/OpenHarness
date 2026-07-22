# -*- coding: utf-8 -*-
"""
schemas.py — 核心数据模型 (对应架构文档「3 个核心数据模型」)

用轻量 dataclass 表达 Skill Artifact / Eval Record。Rubric 直接以 dict 从
artifacts/rubric.json 读入(见 rubric.py)。刻意只用 stdlib。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import copy


@dataclass
class SkillArtifact:
    """被版本化、被优化的东西。structure 冻结,其余由 optimizer 迭代。"""
    id: str
    version: str
    parent_version: Optional[str]
    structure: Dict[str, Any]              # 人工设定,MVP 不改
    instructions: Dict[str, Any]           # {prose, directives{...}} ← L1 优化
    few_shots: List[Any]                   # ← L2 优化
    memory_content: Dict[str, Any]         # ← L3 优化
    changelog: str = ""

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "SkillArtifact":
        return SkillArtifact(
            id=d["id"], version=d["version"], parent_version=d.get("parent_version"),
            structure=d["structure"], instructions=d["instructions"],
            few_shots=d.get("few_shots", []), memory_content=d.get("memory_content", {}),
            changelog=d.get("changelog", ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id, "version": self.version, "parent_version": self.parent_version,
            "structure": self.structure, "instructions": self.instructions,
            "few_shots": self.few_shots, "memory_content": self.memory_content,
            "changelog": self.changelog,
        }

    def directives(self) -> Dict[str, bool]:
        return self.instructions.get("directives", {})

    def clone_with_directive(self, name: str, value: bool, new_version: str, note: str) -> "SkillArtifact":
        """产出一个只改了一个 directive 的候选版本(L1 动作)。"""
        c = copy.deepcopy(self)
        c.directives()[name] = value
        c.parent_version = self.version
        c.version = new_version
        c.changelog = note
        return c

    def has_fewshot(self, kind: str) -> bool:
        """few_shots 里是否已注入某类范例(按 kind 判)。"""
        return any(isinstance(f, dict) and f.get("kind") == kind for f in self.few_shots)

    def clone_with_fewshot(self, kind: str, new_version: str, note: str) -> "SkillArtifact":
        """产出一个只多注入了一条 few-shot 范例的候选版本(L2 动作)。"""
        c = copy.deepcopy(self)
        c.few_shots = list(c.few_shots) + [{"kind": kind}]
        c.parent_version = self.version
        c.version = new_version
        c.changelog = note
        return c


@dataclass
class EvalRecord:
    """一条 trace 的评测结果。"""
    run_id: str
    skill_version: str
    dataset_split: str
    case_id: str
    input: Dict[str, Any]
    trace: Dict[str, Any]                  # 完整执行过程,聚类和归因靠它
    output: Dict[str, Any]
    scores: Dict[str, int] = field(default_factory=dict)
    judge_reasoning: Dict[str, str] = field(default_factory=dict)
    flagged: List[str] = field(default_factory=list)
    case_failed_gate: bool = False         # 命中红线
    human_label: Optional[Dict[str, int]] = None
