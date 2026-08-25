"""WorkBuddy-only Judge provider shared by Report Loop runtime and metadata."""

from __future__ import annotations

import os
from dataclasses import dataclass

from .workbuddy_cli import DEFAULT_MODEL as DEFAULT_WORKBUDDY_MODEL
from .workbuddy_cli import call_workbuddy


PROVIDER_WORKBUDDY = "workbuddy"
DEFAULT_PROVIDER = PROVIDER_WORKBUDDY
DEFAULT_EFFORT = "medium"
LOCKED_REPORT_JUDGE_MODEL = "deepseek-v4-pro"
LOCKED_REPORT_JUDGE_EFFORT = "medium"


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
        "wb": PROVIDER_WORKBUDDY,
        "workbuddy_cli": PROVIDER_WORKBUDDY,
    }.get(provider, provider)
    if provider != PROVIDER_WORKBUDDY:
        raise JudgeProviderError(
            "Judge Provider 仅支持 workbuddy；Codex CLI 路径已删除"
        )
    return provider


def resolve_settings(
    *,
    provider: str | None = None,
    model: str | None = None,
    effort: str | None = None,
) -> JudgeSettings:
    selected_provider = normalize_provider(provider)
    selected_model = str(
        model
        or os.environ.get("RESEARCH_REPORT_LOOP_WB_MODEL")
        or os.environ.get("RESEARCH_REPORT_LOOP_JUDGE_MODEL")
        or DEFAULT_WORKBUDDY_MODEL
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


def locked_report_judge_settings(provider: str | None = None) -> JudgeSettings:
    """Return WorkBuddy-only, model-locked Runner settings."""
    return JudgeSettings(
        provider=normalize_provider(provider or PROVIDER_WORKBUDDY),
        model=LOCKED_REPORT_JUDGE_MODEL,
        effort=LOCKED_REPORT_JUDGE_EFFORT,
    )


def call_judge(
    prompt: str,
    *,
    settings: JudgeSettings,
    timeout_seconds: float | None = None,
) -> str:
    normalize_provider(settings.provider)
    return call_workbuddy(
        prompt,
        model=settings.model,
        effort=settings.effort,
        timeout_seconds=timeout_seconds,
    )
