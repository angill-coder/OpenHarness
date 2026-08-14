# -*- coding: utf-8 -*-
"""optimizer_registry.py — 优化器策略注册表 + 统一接口。

策略层多份并存,每份实现同一接口:
  propose(session, cur_skill, failures, context) -> proposal | None
  apply_proposal(cur_skill, proposal, new_version) -> SkillArtifact

  switch_search —— 翻开关搜索(harness/optimizer.py)。为保证行为逐字不变,
    session_eval 的 switch_search 路径仍直接调 optimizer_mod、不经本注册表编排;
    这里的 _SwitchSearchAdapter 只是纯透传登记,供将来统一编排时使用。
  llm_rewrite   —— Diagnosis LLM 选目标 + Patch LLM 局部结构化修改
                  (app/optimizer02.py)。
"""

from __future__ import annotations

import os
import sys

HARNESS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "harness"
)
if HARNESS not in sys.path:
    sys.path.insert(0, HARNESS)

import optimizer as _switch_search_mod   # noqa: E402  harness/optimizer.py
import optimizer02 as _llm_rewrite_mod   # noqa: E402


class _SwitchSearchAdapter:
    """纯透传 harness/optimizer.py。REQUIRES_ASYNC_GATE=False(同步 dev gate)。"""

    REQUIRES_ASYNC_GATE = False
    IS_OFFLINE = True

    @staticmethod
    def propose(session, cur_skill, failures, context):
        return _switch_search_mod.propose(cur_skill, failures, session.opt_history)

    @staticmethod
    def apply_proposal(cur_skill, proposal, new_version):
        return _switch_search_mod.apply_proposal(cur_skill, proposal, new_version)


STRATEGY_REGISTRY = {
    "switch_search": _SwitchSearchAdapter,
    "llm_rewrite": _llm_rewrite_mod,
}

DEFAULT_MODE = "switch_search"


def get_strategy(mode):
    return STRATEGY_REGISTRY.get(mode or DEFAULT_MODE, _SwitchSearchAdapter)


def requires_async_gate(mode) -> bool:
    return bool(getattr(get_strategy(mode), "REQUIRES_ASYNC_GATE", False))
