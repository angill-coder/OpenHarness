# -*- coding: utf-8 -*-
"""OpenHarness 前端与后端共用的模型配置。"""

SUPPORTED_API_MODELS = (
    "claude-opus-5",
    "claude-opus-4.8",
    "gpt-5.6-sol",
)

SUPPORTED_CODEX_MODELS = (
    "gpt-5.6-sol",
)

SUPPORTED_CODEX_REASONING_EFFORTS = (
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
    "ultra",
)

SUPPORTED_WB_MODELS = (
    "deepseek-v4-pro-ioa",
    "hy3-ioa",
    "deepseek-v4-flash-ioa",
    "claude-opus-5",
    "claude-opus-4.8-1m",
    "claude-opus-4.8",
    "claude-opus-4.7-1m",
    "claude-opus-4.7",
    "claude-opus-4.6-1m",
    "claude-opus-4.6",
    "claude-sonnet-5-1m",
    "claude-sonnet-5",
    "claude-sonnet-4.6-1m",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.3-codex",
    "gemini-3.5-flash",
    "glm-5.2-ioa",
    "glm-5v-turbo-ioa",
    "kimi-k3-ioa",
    "kimi-k2.7-ioa",
    "kimi-k2.6-ioa",
    "minimax-m3-ioa",
)

DEFAULT_GENERATION_WB_MODEL = "deepseek-v4-pro-ioa"
DEFAULT_EVALUATION_WB_MODEL = "claude-opus-4.8"
DEFAULT_EVALUATION_API_MODEL = "claude-opus-4.8"
DEFAULT_EVALUATION_CODEX_MODEL = "gpt-5.6-sol"
DEFAULT_CODEX_REASONING_EFFORT = "medium"
