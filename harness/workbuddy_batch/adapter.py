from __future__ import annotations

import os
import shutil
from pathlib import Path

from .models import BatchConfig, CaseSpec


MAC_WORKBUDDY_CLI = Path(
    "/Applications/WorkBuddy.app/Contents/Resources/"
    "app.asar.unpacked/cli/bin/codebuddy"
)


def discover_command(explicit: str | None = None) -> tuple[str, ...]:
    if explicit:
        return (str(Path(explicit).expanduser()),)
    from_environment = os.environ.get("WORKBUDDY_CLI")
    if from_environment:
        return (str(Path(from_environment).expanduser()),)
    workbuddy = shutil.which("workbuddy")
    if workbuddy:
        return (workbuddy,)
    if MAC_WORKBUDDY_CLI.exists():
        return (str(MAC_WORKBUDDY_CLI),)
    for name in ("codebuddy", "cbc"):
        candidate = shutil.which(name)
        if candidate:
            return (candidate,)
    raise FileNotFoundError(
        "找不到 WorkBuddy CLI。请设置 WORKBUDDY_CLI 或传入 --cli-path。"
    )


def infer_product_config(command: tuple[str, ...]) -> Path | None:
    if not command:
        return None
    cli = Path(command[0])
    if "WorkBuddy.app" not in str(cli):
        return None
    candidate = cli.parent.parent / "product.json"
    return candidate if candidate.exists() else None


def build_environment(
    config: BatchConfig, plugin_dirs: tuple[Path, ...] = ()
) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(config.environment)
    if config.workbuddy_home:
        environment["CODEBUDDY_CONFIG_DIR"] = str(config.workbuddy_home)
    if config.product_config:
        environment["ACC_PRODUCT_CONFIG_PATH"] = str(config.product_config)
    if plugin_dirs:
        existing = environment.get("CODEBUDDY_PLUGIN_DIRS", "")
        values = [*(item for item in existing.split(os.pathsep) if item)]
        values.extend(str(path) for path in plugin_dirs)
        environment["CODEBUDDY_PLUGIN_DIRS"] = os.pathsep.join(dict.fromkeys(values))
    return environment


def skill_system_prompt(skills: tuple[str, ...], custom: str) -> str:
    sections: list[str] = []
    if skills:
        joined = ", ".join(skills)
        sections.append(
            "Automated evaluation constraint: explicitly load and apply these "
            f"WorkBuddy Skills for this case: {joined}. Do not silently substitute "
            "a different skill. Follow their normal user-confirmation workflow."
        )
    if custom.strip():
        sections.append(custom.strip())
    return "\n\n".join(sections)


def build_round_command(
    config: BatchConfig,
    case: CaseSpec,
    session_id: str,
    round_index: int,
    prompt: str,
    skills: tuple[str, ...],
) -> list[str]:
    command = [*config.command, "-p", "--output-format", "stream-json"]
    if config.include_partial_messages:
        command.append("--include-partial-messages")
    if config.verbose:
        command.append("--verbose")
    if round_index == 0:
        command.extend(["--session-id", session_id])
    else:
        command.extend(["--resume", session_id])
    model = case.model or config.model
    if model:
        command.extend(["--model", model])
    effort = case.effort or config.effort
    if effort:
        command.extend(["--effort", effort])
    command.extend(["--permission-mode", "bypassPermissions"])
    if config.tools is not None:
        command.extend(["--tools", ",".join(config.tools)])
    if config.allowed_tools:
        command.append(f"--allowedTools={','.join(config.allowed_tools)}")
    if config.disallowed_tools:
        command.append(f"--disallowedTools={','.join(config.disallowed_tools)}")
    if config.max_turns is not None:
        command.extend(["--max-turns", str(config.max_turns)])
    if config.setting_sources:
        command.extend(["--setting-sources", config.setting_sources])
    system_prompt = skill_system_prompt(skills, config.append_system_prompt)
    if system_prompt:
        command.extend(["--append-system-prompt", system_prompt])
    command.append(prompt)
    return command
