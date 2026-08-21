from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Interaction:
    """One simulated user response after the initial task prompt."""

    input: str
    label: str = ""


@dataclass(frozen=True)
class InputFile:
    source: Path
    target: str = ""


@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    prompt: str
    prompt_label: str = "initial_prompt"
    interactions: tuple[Interaction, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    model: str | None = None
    effort: str | None = None
    skills: tuple[str, ...] = ()
    skill_paths: tuple[Path, ...] = ()
    plugin_dirs: tuple[Path, ...] = ()
    input_files: tuple[InputFile, ...] = ()
    artifact_globs: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def user_inputs(self) -> tuple[Interaction, ...]:
        return (Interaction(self.prompt, self.prompt_label), *self.interactions)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["turns"] = [
            {
                "round": index,
                "label": interaction.label,
                "prompt": interaction.input,
            }
            for index, interaction in enumerate(self.user_inputs)
        ]
        payload.pop("prompt")
        payload.pop("prompt_label")
        payload.pop("interactions")
        payload["skill_paths"] = [str(path) for path in self.skill_paths]
        payload["plugin_dirs"] = [str(path) for path in self.plugin_dirs]
        payload["input_files"] = [
            {"source": str(item.source), "target": item.target}
            for item in self.input_files
        ]
        return payload


@dataclass(frozen=True)
class BatchConfig:
    command: tuple[str, ...]
    output_root: Path
    workbuddy_home: Path | None = None
    product_config: Path | None = None
    model: str | None = None
    effort: str | None = None
    parallel: int = 20
    repetition: int = 1
    timeout_seconds: float = 900.0
    stall_timeout_seconds: float = 180.0
    launch_interval_seconds: float = 0.0
    tools: tuple[str, ...] | None = None
    allowed_tools: tuple[str, ...] = ()
    disallowed_tools: tuple[str, ...] = ()
    max_turns: int | None = None
    # 默认不载入用户、项目或本地设置，避免其中的 Memory 进入执行上下文。
    setting_sources: str | None = ""
    include_partial_messages: bool = True
    verbose: bool = True
    capture_native_session: bool = True
    require_skill: bool = True
    skills: tuple[str, ...] = ()
    skill_paths: tuple[Path, ...] = ()
    plugin_dirs: tuple[Path, ...] = ()
    append_system_prompt: str = ""
    artifact_globs: tuple[str, ...] = ()
    environment: dict[str, str] = field(default_factory=dict)
    flat_single_case: bool = False

    def __post_init__(self) -> None:
        if not self.command:
            raise ValueError("WorkBuddy CLI command 不能为空")
        if self.parallel < 1:
            raise ValueError("parallel 必须至少为 1")
        if self.repetition < 1:
            raise ValueError("repetition 必须至少为 1")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds 必须大于 0")
        if self.stall_timeout_seconds <= 0:
            raise ValueError("stall_timeout_seconds 必须大于 0")
        if self.launch_interval_seconds < 0:
            raise ValueError("launch_interval_seconds 不能小于 0")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["command"] = list(self.command)
        payload["output_root"] = str(self.output_root)
        payload["workbuddy_home"] = (
            str(self.workbuddy_home) if self.workbuddy_home else None
        )
        payload["product_config"] = (
            str(self.product_config) if self.product_config else None
        )
        payload["skill_paths"] = [str(path) for path in self.skill_paths]
        payload["plugin_dirs"] = [str(path) for path in self.plugin_dirs]
        # Values can contain credentials; only persist the overridden variable names.
        payload["environment"] = sorted(self.environment)
        return payload
