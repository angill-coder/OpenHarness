"""Single source of truth for Report Loop Judge model selection.

Why this file exists
--------------------
The Judge model is deliberately pinned rather than inherited from the host
session. Report scores must stay comparable across runs because the Memory
Curator uses them to decide whether a piece of user feedback is worth
distilling into an L2B Memory Rubric. If the Judge model drifted with whatever
model the user happened to pick, scores would become a moving target and the
memory system would lose its calibration baseline.

Pinning does not mean hardcoding in five places. When the platform retires a
model or ships a better one, update ``JUDGE_MODEL`` / ``CODEX_JUDGE_MODEL``
here and every consumer follows. Both the Python runner and the Node release
builder read these values, so there is exactly one place to edit.

Overrides are supported but deliberately explicit, in this precedence order:

1. Job field       -- ``judgeModel`` / ``judgeEffort`` in the submitted Job
2. Environment     -- ``RESEARCH_REPORT_LOOP_JUDGE_MODEL`` and friends
3. Pinned constant -- the values below

With no override supplied the behaviour is identical to the original pinned
configuration. Whatever model is actually used is recorded in the run metadata
and surfaced in the result JSON, so a changed scoring baseline is auditable
instead of silent.
"""

from __future__ import annotations

# Pinned Judge model for the WorkBuddy provider. Update on platform upgrade.
JUDGE_MODEL = "gpt-5.6-sol"
JUDGE_EFFORT = "medium"

# Pinned Judge model for the optional Codex provider.
CODEX_JUDGE_MODEL = "gpt-5.6-sol"
CODEX_JUDGE_EFFORT = "medium"

# Efforts accepted by the providers; validated before a run starts so an
# unusable value fails fast with a clear message instead of mid-loop.
SUPPORTED_EFFORTS = ("low", "medium", "high", "xhigh", "max", "ultra")

__all__ = [
    "JUDGE_MODEL",
    "JUDGE_EFFORT",
    "CODEX_JUDGE_MODEL",
    "CODEX_JUDGE_EFFORT",
    "SUPPORTED_EFFORTS",
]
