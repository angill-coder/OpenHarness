from __future__ import annotations

import fnmatch
import mimetypes
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io import sha256_file
from .models import InputFile


@dataclass(frozen=True)
class FileState:
    size: int
    sha256: str
    mtime_ns: int


def _excluded(relative: Path) -> bool:
    parts = relative.parts
    return bool(parts and parts[0] in {".codebuddy", ".git", "__pycache__"})


def snapshot_workspace(workspace: Path) -> dict[str, FileState]:
    result: dict[str, FileState] = {}
    for path in sorted(workspace.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(workspace)
        if _excluded(relative):
            continue
        stat = path.stat()
        result[relative.as_posix()] = FileState(
            size=stat.st_size,
            sha256=sha256_file(path),
            mtime_ns=stat.st_mtime_ns,
        )
    return result


def copy_inputs(inputs: tuple[InputFile, ...], workspace: Path) -> None:
    for item in inputs:
        source = item.source
        if not source.exists():
            raise FileNotFoundError(f"输入文件不存在: {source}")
        target_name = item.target or source.name
        target = (workspace / target_name).resolve()
        if workspace.resolve() not in target.parents and target != workspace.resolve():
            raise ValueError(f"input_files.target 不能逃出案例 workspace: {target_name}")
        if source.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def _skill_name(path: Path) -> str:
    skill_file = path / "SKILL.md" if path.is_dir() else path
    if not skill_file.exists():
        raise FileNotFoundError(f"Skill 路径缺少 SKILL.md: {path}")
    header = skill_file.read_text(encoding="utf-8", errors="replace")[:4096]
    match = re.search(r"(?m)^name:\s*['\"]?([^'\"\n]+)", header)
    return (match.group(1).strip() if match else skill_file.parent.name).replace("/", "-")


def stage_skills(skill_paths: tuple[Path, ...], workspace: Path) -> tuple[str, ...]:
    names: list[str] = []
    destination_root = workspace / ".codebuddy" / "skills"
    for supplied in skill_paths:
        path = supplied.expanduser().resolve()
        name = _skill_name(path)
        source = path if path.is_dir() else path.parent
        destination = destination_root / name
        if destination.exists():
            raise ValueError(f"重复的 Skill 名称: {name}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, destination)
        names.append(name)
    return tuple(names)


def collect_artifacts(
    workspace: Path,
    before: dict[str, FileState],
    destination: Path,
    globs: tuple[str, ...],
) -> list[dict[str, Any]]:
    after = snapshot_workspace(workspace)
    manifest: list[dict[str, Any]] = []
    patterns = tuple(globs)
    for relative, state in sorted(after.items()):
        old = before.get(relative)
        changed = old is None or old.sha256 != state.sha256
        matched = bool(patterns and any(fnmatch.fnmatch(relative, item) for item in patterns))
        if not changed and not matched:
            continue
        source = workspace / relative
        filename = Path(relative).name
        if filename == "manifest.json":
            filename = "manifest__artifact.json"
        target = destination / filename
        collision_index = 2
        while target.exists():
            target = destination / (
                f"{Path(filename).stem}__{collision_index}{Path(filename).suffix}"
            )
            collision_index += 1
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        mime_type, _ = mimetypes.guess_type(source.name)
        manifest.append(
            {
                "path": relative,
                "captured_path": str(target.relative_to(destination.parent)),
                "status": "created" if old is None else ("modified" if changed else "matched"),
                "size": state.size,
                "sha256": state.sha256,
                "mime_type": mime_type or "application/octet-stream",
                "mtime_ns": state.mtime_ns,
            }
        )
    for relative in sorted(set(before) - set(after)):
        manifest.append({"path": relative, "status": "deleted"})
    return manifest
