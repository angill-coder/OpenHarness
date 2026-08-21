# -*- coding: utf-8 -*-
"""OpenHarness 前端与后端共用的模型配置。"""

import json
import os
from pathlib import Path

SUPPORTED_API_MODELS = (
    "claude-opus-5",
    "claude-opus-4-8",
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

_ENTERPRISE_WB_MODELS = (
    "deepseek-v4-pro-ioa",
    "hy3-ioa",
    "deepseek-v4-flash-ioa",
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

def _custom_workbuddy_models():
    """Read user-defined WorkBuddy model IDs without copying credentials."""
    configured = os.environ.get("OPENHARNESS_WB_CUSTOM_MODELS")
    if configured:
        return tuple(item.strip() for item in configured.split(",") if item.strip())
    path = Path.home() / ".workbuddy" / "models.json"
    if not path.is_file():
        return ()
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return ()
    items = document if isinstance(document, list) else document.get("models", [])
    return tuple(
        str(item["id"]).strip()
        for item in items
        if isinstance(item, dict)
        and str(item.get("id") or "").strip()
        and not item.get("hidden")
        and not item.get("disabled")
    )


def _installed_workbuddy_models():
    """Read text/tool-capable models from the installed WorkBuddy CLI."""
    candidates = [
        os.environ.get("OPENHARNESS_WB_PRODUCT_CONFIG"),
        os.environ.get("ACC_PRODUCT_CONFIG_PATH"),
    ]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidates.append(
            str(
                Path(local_app_data)
                / "Programs"
                / "WorkBuddy"
                / "resources"
                / "app.asar.unpacked"
                / "cli"
                / "product.json"
            )
        )
    candidates.append(
        str(
            Path.home()
            / "WorkBuddy"
            / "resources"
            / "app.asar.unpacked"
            / "cli"
            / "product.json"
        )
    )
    for value in candidates:
        if not value:
            continue
        path = Path(value).expanduser()
        if not path.is_file():
            continue
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        return tuple(
            str(item["id"])
            for item in document.get("models", [])
            if isinstance(item, dict)
            and item.get("id")
            and item.get("supportsToolCall") is True
            and not item.get("hidden")
            and not item.get("disabled")
        )
    return ()


_DISCOVERED_WB_MODELS = tuple(
    dict.fromkeys((*_custom_workbuddy_models(), *_installed_workbuddy_models()))
)
SUPPORTED_WB_MODELS = _DISCOVERED_WB_MODELS or _ENTERPRISE_WB_MODELS
_DEFAULT_WB_MODEL = "default" if "default" in SUPPORTED_WB_MODELS else SUPPORTED_WB_MODELS[0]
DEFAULT_GENERATION_WB_MODEL = _DEFAULT_WB_MODEL
DEFAULT_EVALUATION_WB_MODEL = _DEFAULT_WB_MODEL
DEFAULT_EVALUATION_API_MODEL = "claude-opus-4-8"
DEFAULT_EVALUATION_CODEX_MODEL = "gpt-5.6-sol"
DEFAULT_CODEX_REASONING_EFFORT = "medium"
