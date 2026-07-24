# -*- coding: utf-8 -*-
"""OpenHarness 对 WorkBuddy 最终报告的独立验收。"""

from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from external_run_models import ReportArtifact, ReportOutputContract
from workbuddy_batch.io import sha256_file


@dataclass(frozen=True)
class ArtifactValidationResult:
    status: str
    error: Optional[str] = None
    report: Optional[ReportArtifact] = None

    @property
    def valid(self) -> bool:
        return self.report is not None


def validate_report_artifact(
    case_dir: Path,
    contract: ReportOutputContract,
) -> ArtifactValidationResult:
    """按 manifest 和文件内容判断一个 case 是否真的产出合格报告。"""

    manifest_path = case_dir / "artifacts" / "manifest.json"
    if not manifest_path.exists():
        return ArtifactValidationResult(
            status="artifact_missing",
            error=f"artifact manifest 不存在: {manifest_path}",
        )
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return ArtifactValidationResult(
            status="artifact_invalid",
            error=f"artifact manifest 无法读取: {exc}",
        )
    if not isinstance(manifest, list):
        return ArtifactValidationResult(
            status="artifact_invalid",
            error="artifact manifest 必须是数组",
        )

    candidates = [
        item
        for item in manifest
        if isinstance(item, dict)
        and item.get("status") != "deleted"
        and fnmatch.fnmatch(
            str(item.get("path", "")),
            contract.required_glob,
        )
    ]
    if not candidates:
        return ArtifactValidationResult(
            status="artifact_missing",
            error=f"未找到 required report: {contract.required_glob}",
        )
    if len(candidates) > contract.max_files:
        return ArtifactValidationResult(
            status="artifact_invalid",
            error=(
                f"required report 候选数为 {len(candidates)}，"
                f"超过 max_files={contract.max_files}"
            ),
        )

    item = candidates[0]
    original_path = str(item.get("path", ""))
    captured_relative = str(item.get("captured_path", ""))
    if not captured_relative:
        return ArtifactValidationResult(
            status="artifact_invalid",
            error="manifest 缺少 captured_path",
        )
    captured_path = (case_dir / captured_relative).resolve()
    try:
        captured_path.relative_to(case_dir.resolve())
    except ValueError:
        return ArtifactValidationResult(
            status="artifact_invalid",
            error=f"captured_path 逃出 case 目录: {captured_relative}",
        )
    if not captured_path.is_file():
        return ArtifactValidationResult(
            status="artifact_invalid",
            error=f"manifest 指向的报告文件不存在: {captured_relative}",
        )

    extension = captured_path.suffix.lower()
    if extension not in contract.allowed_extensions:
        return ArtifactValidationResult(
            status="artifact_invalid",
            error=(
                f"报告扩展名 {extension or '<none>'} 不在允许范围 "
                f"{contract.allowed_extensions}"
            ),
        )
    size = captured_path.stat().st_size
    if size < contract.min_bytes:
        return ArtifactValidationResult(
            status="artifact_invalid",
            error=f"报告大小 {size} bytes，小于最小值 {contract.min_bytes}",
        )
    try:
        text = captured_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        return ArtifactValidationResult(
            status="artifact_invalid",
            error=f"报告无法解析为 UTF-8 文本: {exc}",
        )
    if not text.strip():
        return ArtifactValidationResult(
            status="artifact_invalid",
            error="报告正文为空",
        )

    return ArtifactValidationResult(
        status="generated",
        report=ReportArtifact(
            original_workspace_path=original_path,
            captured_path=str(captured_path),
            sha256=sha256_file(captured_path),
            size=size,
            mime_type=str(item.get("mime_type") or "text/markdown"),
            text=text,
        ),
    )
