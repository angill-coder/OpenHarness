"""Environment-backed runtime settings owned by Report Loop."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from model_config import DEFAULT_GENERATION_WB_MODEL, SUPPORTED_WB_MODELS


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError) as exc:
        raise ValueError("%s 必须是整数" % name) from exc


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError) as exc:
        raise ValueError("%s 必须是数字" % name) from exc


@dataclass(frozen=True)
class ReportLoopSettings:
    model: Optional[str] = DEFAULT_GENERATION_WB_MODEL
    models: tuple[str, ...] = SUPPORTED_WB_MODELS
    max_report_retries: int = 3
    timeout_seconds: float = 900.0
    stall_timeout_seconds: float = 180.0
    command: Optional[tuple[str, ...]] = None
    workbuddy_home: Optional[Path] = None
    product_config: Optional[Path] = None
    min_report_bytes: int = 500

    @classmethod
    def from_env(cls) -> "ReportLoopSettings":
        command_path = os.environ.get("OPENHARNESS_WB_CLI_PATH") or None
        return cls(
            model=os.environ.get("OPENHARNESS_WB_MODEL", DEFAULT_GENERATION_WB_MODEL) or None,
            max_report_retries=_env_int("OPENHARNESS_WB_MAX_REPORT_RETRIES", 3),
            timeout_seconds=_env_float("OPENHARNESS_WB_TIMEOUT", 900.0),
            stall_timeout_seconds=_env_float("OPENHARNESS_WB_STALL_TIMEOUT", 180.0),
            command=(command_path,) if command_path else None,
            workbuddy_home=(Path(os.environ["OPENHARNESS_WB_HOME"]) if os.environ.get("OPENHARNESS_WB_HOME") else None),
            product_config=(Path(os.environ["OPENHARNESS_WB_PRODUCT_CONFIG"]) if os.environ.get("OPENHARNESS_WB_PRODUCT_CONFIG") else None),
            min_report_bytes=_env_int("OPENHARNESS_WB_MIN_REPORT_BYTES", 500),
        )

    def validate(self) -> None:
        if not self.models:
            raise ValueError("报告生成模型列表不能为空")
        if self.model and self.model not in self.models:
            raise ValueError("默认报告生成模型不在支持列表中: %s" % self.model)
        if self.max_report_retries < 0:
            raise ValueError("max_report_retries 不能小于 0")
        if self.timeout_seconds <= 0 or self.stall_timeout_seconds <= 0:
            raise ValueError("timeout 必须大于 0")
        if self.min_report_bytes < 1:
            raise ValueError("min_report_bytes 必须大于 0")
