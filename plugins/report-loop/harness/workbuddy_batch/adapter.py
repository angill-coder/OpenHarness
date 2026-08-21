from __future__ import annotations

import os
import shutil
from pathlib import Path

from .models import BatchConfig, CaseSpec


STRUCTURED_DATA_TARGET = "materials/00_structured_data.json"
EVIDENCE_SOURCE_TARGET = "materials/source"


def structured_data_first_system_prompt(case: CaseSpec) -> str:
    """Return the evidence-reading contract for structured-data-first datasets."""
    targets = {
        str(item.target or "").replace("\\", "/").rstrip("/")
        for item in case.input_files
    }
    if STRUCTURED_DATA_TARGET not in targets:
        return ""
    source_note = (
        f"原始资料位于 `{EVIDENCE_SOURCE_TARGET}/`。"
        if EVIDENCE_SOURCE_TARGET in targets
        else "如 workspace 中另有原始资料，只把它作为辅助核验材料。"
    )
    return "\n".join(
        [
            "OpenHarness evidence-first reading contract:",
            f"1. 开始分析时必须先完整读取 `{STRUCTURED_DATA_TARGET}`；"
            "其中 `items` 是本 case 的主证据索引，报告事实、数据和核心论断优先以其为依据。",
            f"2. {source_note}仅在核验 `source_ref`、补充语境，或 structured data 明确标记"
            " unresolved/证据不足时再读取；不得跳过 structured data 直接通读 source 后自行另建事实集。",
            "3. 原始资料与 structured data 冲突时必须显式指出，不得静默覆盖；"
            "structured data 未收录的新事实不得直接升级为核心确定性结论。",
            "4. 只读取当前 workspace 内的 Skill、structured data 和 materials；"
            "不要向上探索运行目录、case.json、trace 或其他 OpenHarness 文件。",
        ]
    )


MAC_WORKBUDDY_CLI = Path(
    "/Applications/WorkBuddy.app/Contents/Resources/"
    "app.asar.unpacked/cli/bin/codebuddy"
)


def _windows_desktop_command(path: Path | None = None) -> tuple[str, ...] | None:
    """Return the CLI embedded in a Windows WorkBuddy desktop install."""
    if os.name != "nt":
        return None
    candidates = []
    if path:
        expanded = path.expanduser()
        if expanded.name.lower() == "workbuddy.exe":
            candidates.append(expanded.parent)
        elif expanded.name.lower() == "codebuddy":
            try:
                candidates.append(expanded.parents[4])
            except IndexError:
                pass
    local_app_data = os.environ.get("LOCALAPPDATA")
    candidates.append(Path.home() / "WorkBuddy")
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "WorkBuddy")
    for root in candidates:
        executable = root / "WorkBuddy.exe"
        cli = (
            root
            / "resources"
            / "app.asar.unpacked"
            / "cli"
            / "bin"
            / "codebuddy"
        )
        if executable.is_file() and cli.is_file():
            return (str(executable), str(cli))
    return None


def _explicit_command(value: str) -> tuple[str, ...]:
    path = Path(value).expanduser()
    desktop = _windows_desktop_command(path)
    return desktop or (str(path),)


def discover_command(explicit: str | None = None) -> tuple[str, ...]:
    if explicit:
        return _explicit_command(explicit)
    from_environment = os.environ.get("WORKBUDDY_CLI")
    if from_environment:
        return _explicit_command(from_environment)
    workbuddy = shutil.which("workbuddy")
    if workbuddy:
        return (workbuddy,)
    if MAC_WORKBUDDY_CLI.exists():
        return (str(MAC_WORKBUDDY_CLI),)
    windows_desktop = _windows_desktop_command()
    if windows_desktop:
        return windows_desktop
    for name in ("codebuddy", "cbc"):
        candidate = shutil.which(name)
        if candidate:
            return (candidate,)
    raise FileNotFoundError(
        "找不到 WorkBuddy CLI。请设置 OPENHARNESS_WB_CLI_PATH/WORKBUDDY_CLI，或传入 --cli-path。"
    )


def infer_product_config(command: tuple[str, ...]) -> Path | None:
    if not command:
        return None
    cli = Path(command[0])
    if len(command) > 1 and Path(command[1]).name.lower() == "codebuddy":
        candidate = Path(command[1]).parent.parent / "product.json"
        return candidate if candidate.exists() else None
    if "WorkBuddy.app" not in str(cli):
        return None
    candidate = cli.parent.parent / "product.json"
    return candidate if candidate.exists() else None


def build_environment(
    config: BatchConfig, plugin_dirs: tuple[Path, ...] = ()
) -> dict[str, str]:
    environment = dict(os.environ)
    environment.update(config.environment)
    # Runner 只使用本次 case 的 workspace / 显式 Skill，不读取或写入
    # WorkBuddy 用户、项目及团队 Memory。
    environment["CODEBUDDY_DISABLE_AUTO_MEMORY"] = "1"
    environment["CODEBUDDY_MEMORY_RELEVANCE_DISABLED"] = "1"
    environment["CODEBUDDY_MEMORY_EXTRACTION_DISABLED"] = "1"
    environment["CODEBUDDY_TEAM_MEMORY_ENABLED"] = "0"
    if (
        len(config.command) > 1
        and Path(config.command[0]).name.lower() == "workbuddy.exe"
        and Path(config.command[1]).name.lower() == "codebuddy"
    ):
        environment["ELECTRON_RUN_AS_NODE"] = "1"
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
    # 空字符串也必须显式传入；若省略参数，CLI 会回退到
    # user,project,local 默认来源。
    if config.setting_sources is not None:
        command.extend(["--setting-sources", config.setting_sources])
    system_prompt = skill_system_prompt(skills, config.append_system_prompt)
    structured_data_prompt = structured_data_first_system_prompt(case)
    if structured_data_prompt:
        system_prompt = "\n\n".join(
            item for item in (system_prompt, structured_data_prompt) if item
        )
    if system_prompt:
        command.extend(["--append-system-prompt", system_prompt])
    command.append(prompt)
    return command
