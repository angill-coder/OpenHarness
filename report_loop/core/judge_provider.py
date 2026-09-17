"""Judge provider selection shared by Report Loop runtime and metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .codex_cli import DEFAULT_MODEL as DEFAULT_CODEX_MODEL
from .codex_cli import call_codex
from .judge_model import (
    CODEX_JUDGE_EFFORT,
    CODEX_JUDGE_MODEL,
    JUDGE_EFFORT,
    JUDGE_MODEL,
    SUPPORTED_EFFORTS,
)
from .workbuddy_cli import DEFAULT_MODEL as DEFAULT_WORKBUDDY_MODEL
from .workbuddy_cli import call_workbuddy


PROVIDER_CODEX = "codex"
PROVIDER_WORKBUDDY = "workbuddy"
DEFAULT_PROVIDER = PROVIDER_WORKBUDDY
DEFAULT_EFFORT = JUDGE_EFFORT
LOCKED_REPORT_JUDGE_MODEL = JUDGE_MODEL
LOCKED_REPORT_JUDGE_EFFORT = JUDGE_EFFORT
LOCKED_CODEX_JUDGE_MODEL = CODEX_JUDGE_MODEL
LOCKED_CODEX_JUDGE_EFFORT = CODEX_JUDGE_EFFORT


class JudgeProviderError(RuntimeError):
    """Invalid Judge provider configuration."""


@dataclass(frozen=True)
class JudgeSettings:
    provider: str
    model: str
    effort: str


def normalize_provider(value: str | None = None) -> str:
    provider = str(
        value
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_PROVIDER")
        or DEFAULT_PROVIDER
    ).strip().lower()
    provider = {
        "codex_cli": PROVIDER_CODEX,
        "wb": PROVIDER_WORKBUDDY,
        "workbuddy_cli": PROVIDER_WORKBUDDY,
    }.get(provider, provider)
    if provider not in {PROVIDER_WORKBUDDY, PROVIDER_CODEX}:
        raise JudgeProviderError("Judge Provider 仅支持 workbuddy 或 codex")
    return provider


def resolve_settings(
    *,
    provider: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> JudgeSettings:
    selected_provider = normalize_provider(provider)
    default_model = (
        DEFAULT_CODEX_MODEL
        if selected_provider == PROVIDER_CODEX
        else DEFAULT_WORKBUDDY_MODEL
    )
    provider_model = (
        os.environ.get("RESEARCH_REPORT_LOOP_CODEX_MODEL")
        if selected_provider == PROVIDER_CODEX
        else os.environ.get("RESEARCH_REPORT_LOOP_WB_MODEL")
    )
    selected_model = str(
        model
        or provider_model
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_MODEL")
        or default_model
    ).strip()
    if not selected_model:
        raise JudgeProviderError("Judge 模型不能为空")
    selected_effort = str(
        effort
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_EFFORT")
        or DEFAULT_EFFORT
    ).strip().lower()
    if not selected_effort:
        raise JudgeProviderError("Judge 推理力度不能为空")
    return JudgeSettings(
        provider=selected_provider,
        model=selected_model,
        effort=selected_effort,
    )


def locked_report_judge_settings(
    provider: str | None = None,
    *,
    model: str | None = None,
    effort: str | None = None,
) -> JudgeSettings:
    """Return the pinned Judge settings, allowing an explicit override.

    The Judge model is pinned by default so report scores stay comparable
    across runs -- the Memory Curator relies on that comparability when
    deciding whether feedback is worth distilling into an L2B rubric.

    Precedence, highest first:

    1. ``model`` / ``effort`` arguments (supplied via the Job)
    2. ``RESEARCH_REPORT_LOOP_{WB,CODEX}_MODEL`` / ``..._JUDGE_MODEL``
       / ``..._JUDGE_EFFORT`` environment variables
    3. The pinned constants in ``judge_model.py``

    With no override this returns exactly the pinned configuration, so the
    default behaviour is unchanged. The resolved values are recorded in the run
    metadata so a shifted scoring baseline is auditable rather than silent.
    """
    selected_provider = normalize_provider(provider)
    is_codex = selected_provider == PROVIDER_CODEX

    pinned_model = LOCKED_CODEX_JUDGE_MODEL if is_codex else LOCKED_REPORT_JUDGE_MODEL
    pinned_effort = LOCKED_CODEX_JUDGE_EFFORT if is_codex else LOCKED_REPORT_JUDGE_EFFORT
    provider_model_env = (
        os.environ.get("RESEARCH_REPORT_LOOP_CODEX_MODEL")
        if is_codex
        else os.environ.get("RESEARCH_REPORT_LOOP_WB_MODEL")
    )

    selected_model = str(
        model
        or provider_model_env
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_MODEL")
        or pinned_model
    ).strip()
    if not selected_model:
        raise JudgeProviderError("Judge 模型不能为空")

    selected_effort = str(
        effort
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_EFFORT")
        or pinned_effort
    ).strip().lower()
    if not selected_effort:
        raise JudgeProviderError("Judge 推理力度不能为空")
    if selected_effort not in SUPPORTED_EFFORTS:
        raise JudgeProviderError(
            "Judge 推理力度 %s 不受支持；可选: %s"
            % (selected_effort, ", ".join(SUPPORTED_EFFORTS))
        )

    return JudgeSettings(
        provider=selected_provider,
        model=selected_model,
        effort=selected_effort,
    )


def judge_settings_are_pinned(settings: JudgeSettings) -> bool:
    """Report whether these settings still match the pinned defaults.

    Used to flag runs whose scores are not comparable to historical scores.
    """
    is_codex = normalize_provider(settings.provider) == PROVIDER_CODEX
    pinned_model = LOCKED_CODEX_JUDGE_MODEL if is_codex else LOCKED_REPORT_JUDGE_MODEL
    pinned_effort = LOCKED_CODEX_JUDGE_EFFORT if is_codex else LOCKED_REPORT_JUDGE_EFFORT
    return settings.model == pinned_model and settings.effort == pinned_effort


def call_judge(
    prompt: str,
    *,
    settings: JudgeSettings,
    timeout_seconds: float | None = None,
) -> str:
    if normalize_provider(settings.provider) == PROVIDER_CODEX:
        return call_codex(
            prompt,
            model=settings.model,
            effort=settings.effort,
            timeout_seconds=timeout_seconds,
        )
    return call_workbuddy(
        prompt,
        model=settings.model,
        effort=settings.effort,
        timeout_seconds=timeout_seconds,
    )
