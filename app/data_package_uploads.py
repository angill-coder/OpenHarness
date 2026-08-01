# -*- coding: utf-8 -*-
"""Safe folder/ZIP ingestion for raw real-project datasets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import threading
import time
import uuid
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Optional

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARNESS = ROOT / "harness"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from _data_prepare import (  # noqa: E402
    CaseDatasetError,
    DEFAULT_FILENAME_REGEX,
    _discover_projects,
    _extract_groundtruth_text,
)


class DataPackageUploadError(ValueError):
    """Invalid upload or unsupported raw project structure."""


@dataclass
class DataPackageUpload:
    upload_id: str
    session_id: str
    name: str
    kind: str
    root: str
    created_at: float
    file_count: int = 0
    total_bytes: int = 0
    status: str = "uploading"
    dataset_path: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_relative_path(value: str) -> Path:
    raw = (value or "").replace("\\", "/").strip("/")
    pure = PurePosixPath(raw)
    if (
        not raw
        or pure.is_absolute()
        or any(part in {"", ".", ".."} for part in pure.parts)
        or "\x00" in raw
    ):
        raise DataPackageUploadError("非法上传路径: %s" % value)
    return Path(*pure.parts)


def _ignored_path(path: Path) -> bool:
    return any(
        part.startswith(".")
        or part == "__MACOSX"
        or part.startswith("~$")
        for part in path.parts
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class DataPackageUploadService:
    def __init__(
        self,
        upload_root: Optional[Path] = None,
        max_files: int = 5000,
        max_total_bytes: int = 2 * 1024 * 1024 * 1024,
        max_file_bytes: int = 512 * 1024 * 1024,
    ):
        self.upload_root = (
            upload_root or ROOT / "data" / "uploads"
        ).expanduser().resolve()
        self.max_files = max_files
        self.max_total_bytes = max_total_bytes
        self.max_file_bytes = max_file_bytes
        self._lock = threading.RLock()
        self._uploads: dict[str, DataPackageUpload] = {}
        self._uploaded_paths: dict[str, set[str]] = {}

    def start(self, session_id: str, name: str, kind: str) -> DataPackageUpload:
        if kind not in {"folder", "zip"}:
            raise DataPackageUploadError("上传类型必须是 folder 或 zip")
        clean_name = Path(name or "project-package").name
        upload_id = "upload-" + uuid.uuid4().hex[:12]
        root = self.upload_root / session_id / upload_id
        (root / "incoming").mkdir(parents=True, exist_ok=False)
        upload = DataPackageUpload(
            upload_id=upload_id,
            session_id=session_id,
            name=clean_name,
            kind=kind,
            root=str(root),
            created_at=time.time(),
        )
        with self._lock:
            self._uploads[upload_id] = upload
            self._uploaded_paths[upload_id] = set()
        return upload

    def get(self, upload_id: str, session_id: str) -> DataPackageUpload:
        with self._lock:
            upload = self._uploads.get(upload_id)
        if upload is None or upload.session_id != session_id:
            raise DataPackageUploadError("上传任务不存在")
        return upload

    def upload_file(
        self,
        upload_id: str,
        session_id: str,
        relative_path: str,
        stream: BinaryIO,
        content_length: int,
    ) -> DataPackageUpload:
        upload = self.get(upload_id, session_id)
        if upload.status != "uploading":
            raise DataPackageUploadError("上传任务已结束")
        relative = _safe_relative_path(relative_path)
        if _ignored_path(relative):
            return upload
        if content_length < 0 or content_length > self.max_file_bytes:
            raise DataPackageUploadError("单文件超过上传上限")
        path_key = relative.as_posix()
        with self._lock:
            if path_key in self._uploaded_paths[upload_id]:
                raise DataPackageUploadError(
                    "上传包包含重复路径: %s" % relative_path
                )
            if upload.file_count + 1 > self.max_files:
                raise DataPackageUploadError("上传文件数量超过上限")
            if upload.total_bytes + content_length > self.max_total_bytes:
                raise DataPackageUploadError("上传包总体积超过上限")
            self._uploaded_paths[upload_id].add(path_key)
            upload.file_count += 1
            upload.total_bytes += content_length
        destination = Path(upload.root) / "incoming" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        remaining = content_length
        try:
            with destination.open("xb") as handle:
                while remaining:
                    chunk = stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise DataPackageUploadError("上传文件内容不完整")
                    handle.write(chunk)
                    remaining -= len(chunk)
        except Exception:
            destination.unlink(missing_ok=True)
            with self._lock:
                self._uploaded_paths[upload_id].discard(path_key)
                upload.file_count -= 1
                upload.total_bytes -= content_length
            raise
        return upload

    def _extract_zip(self, upload: DataPackageUpload) -> Path:
        incoming = Path(upload.root) / "incoming"
        archives = [
            item
            for item in incoming.rglob("*")
            if item.is_file() and item.suffix.lower() == ".zip"
        ]
        if len(archives) != 1 or upload.file_count != 1:
            raise DataPackageUploadError("ZIP 模式必须且只能上传一个 .zip 文件")
        destination = Path(upload.root) / "extracted"
        destination.mkdir()
        total = 0
        count = 0
        try:
            with zipfile.ZipFile(archives[0]) as archive:
                for info in archive.infolist():
                    relative = _safe_relative_path(info.filename)
                    if _ignored_path(relative) or info.is_dir():
                        continue
                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise DataPackageUploadError("ZIP 不允许包含符号链接")
                    count += 1
                    total += info.file_size
                    if count > self.max_files:
                        raise DataPackageUploadError("ZIP 文件数量超过上限")
                    if info.file_size > self.max_file_bytes:
                        raise DataPackageUploadError("ZIP 单文件超过上限")
                    if total > self.max_total_bytes:
                        raise DataPackageUploadError("ZIP 解压体积超过上限")
                    target = destination / relative
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output, 1024 * 1024)
        except zipfile.BadZipFile as exc:
            raise DataPackageUploadError("ZIP 文件损坏") from exc
        return destination

    @staticmethod
    def _is_project(path: Path) -> bool:
        if not path.is_dir():
            return False
        directories = [
            item
            for item in path.iterdir()
            if item.is_dir() and not item.name.startswith(".")
        ]
        groundtruth = [
            item
            for item in path.iterdir()
            if item.is_file()
            and item.suffix.lower() in {".pdf", ".pptx"}
            and not item.name.startswith(".")
        ]
        return len(directories) == 1 and len(groundtruth) == 1

    def _find_projects_root(self, root: Path, upload: DataPackageUpload) -> Path:
        if self._is_project(root):
            wrapped = Path(upload.root) / "projects" / (
                Path(upload.name).stem or "uploaded-project"
            )
            wrapped.parent.mkdir()
            shutil.copytree(root, wrapped)
            return wrapped.parent
        candidates = [root]
        candidates.extend(
            item
            for item in root.rglob("*")
            if item.is_dir()
            and len(item.relative_to(root).parts) <= 3
            and not _ignored_path(item.relative_to(root))
        )
        for candidate in candidates:
            children = [
                item
                for item in candidate.iterdir()
                if item.is_dir() and not item.name.startswith(".")
            ]
            if children and all(self._is_project(item) for item in children):
                return candidate
        raise DataPackageUploadError(
            "未识别到项目结构；每个项目需包含一个 source 素材目录，"
            "以及一个 PDF/PPTX groundtruth"
        )

    def _build_dataset(
        self,
        projects_root: Path,
        destination: Path,
    ) -> tuple[Path, int]:
        projects = _discover_projects(
            projects_root,
            filename_regex=DEFAULT_FILENAME_REGEX.pattern,
            id_prefix="case-upload",
            openharness_id_prefix="rr-upload",
            groundtruth_extensions=(".pdf", ".pptx"),
        )
        has_pdf = any(
            Path(project["groundtruth"]).suffix.lower() == ".pdf"
            for project in projects
        )
        pdftotext = shutil.which("pdftotext")
        if has_pdf and not pdftotext:
            raise DataPackageUploadError("找不到 pdftotext，无法读取 PDF groundtruth")
        cases = []
        for project in projects:
            groundtruth = Path(project["groundtruth"])
            try:
                gt_text, pages = _extract_groundtruth_text(
                    groundtruth,
                    pdftotext_cli=pdftotext,
                    timeout_seconds=120,
                    minimum_characters=100,
                )
            except CaseDatasetError as exc:
                raise DataPackageUploadError(str(exc)) from exc
            topic = str(project["topic"])
            case_id = str(project["openharness_case_id"])
            source = Path(project["source"])
            background = "围绕「%s」开展系统研究。" % topic
            intake = (
                "1. 研究背景：%s\n"
                "2. hypo：暂无预设假设，请基于素材形成并验证核心判断。\n"
                "3. 素材重点分布：都是重点素材。"
            ) % background
            cases.append(
                {
                    "case_id": case_id,
                    "split": "dev",
                    "input": {
                        "topic": topic,
                        "brief": (
                            "请你做一份主题为「%s」的战略研究报告，"
                            "最终产出文档将直接面向腾讯总办汇报。"
                        )
                        % topic,
                        "intake": intake,
                    },
                    "ground_truth": {
                        "reference_report_file": os.path.relpath(
                            groundtruth,
                            destination.parent,
                        ),
                        "reference_report_sha256": _sha256(groundtruth),
                        "reference_report_pages": pages,
                        "reference_report_text": gt_text,
                    },
                    "input_files": [
                        {
                            "source": os.path.relpath(
                                source,
                                destination.parent,
                            ),
                            "target": "materials",
                        }
                    ],
                    "turns": [
                        {"round": 0, "label": "task", "prompt": (
                            "请你做一份主题为「%s」的战略研究报告，"
                            "最终产出文档将直接面向腾讯总办汇报。"
                        ) % topic},
                        {"round": 1, "label": "intake_answers", "prompt": intake},
                    ],
                    "metadata": {
                        "topic": topic,
                        "source_collection": "web-upload",
                        "source_file": project["project_name"],
                        "source_kind": "directory",
                        "case_type": "real_project",
                        "intake_status": "neutral",
                        "source_index": project["source_index"],
                        "display_name": project["project_name"],
                    },
                    "skills": ["research-report"],
                }
            )
        payload = {
            "schema_version": "openharness-wb/v1",
            "defaults": {"skills": ["research-report"]},
            "cases": cases,
        }
        destination.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        return destination, len(cases)

    def finalize(
        self,
        upload_id: str,
        session_id: str,
    ) -> tuple[DataPackageUpload, int]:
        upload = self.get(upload_id, session_id)
        if not upload.file_count:
            raise DataPackageUploadError("上传包为空")
        try:
            source_root = (
                self._extract_zip(upload)
                if upload.kind == "zip"
                else Path(upload.root) / "incoming"
            )
            projects_root = self._find_projects_root(source_root, upload)
            dataset, case_count = self._build_dataset(
                projects_root,
                Path(upload.root) / "data.json",
            )
            upload.dataset_path = str(dataset)
            upload.status = "completed"
            return upload, case_count
        except Exception as exc:
            upload.status = "failed"
            if isinstance(exc, DataPackageUploadError):
                raise
            raise DataPackageUploadError(
                "解析上传包失败: %s" % exc
            ) from exc
