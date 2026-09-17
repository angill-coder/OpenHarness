"""工作区与交付目录约定。

产物位置由这里推导，不由 LLM 决定。主 Agent 在第 0 步确定报告目录后传入
绝对路径；本模块负责推导、校验和落位，越界即拒绝。

目录约定（见 skills/research-report-loop/references/workspace-and-delivery.md）::

    用户项目文件夹/
    ├── 原始素材……
    ├── structured_data.json
    ├── 数据版本说明.md
    └── 报告/
        ├── <报告主题>-v1.md
        ├── 版本说明.md
        └── .report-agent/
            └── <报告主题-首次创建日期时间>/
                ├── 素材处理/r001/
                └── loop-001-YYYYMMDD-HHmmss/
                    ├── 本轮需求.md
                    ├── 候选报告/R0.md …
                    └── 评测与改写记录/

关键不变量：**同一报告的多次 Loop 共用一个 reportId**。用户看完报告要
大改并重启 Loop 时，沿用已有 reportId，在其下新建 loop-002，而不是另建
一份报告——否则版本号和评测历史会断掉。
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

REPORT_DIR_NAME = "报告"
INTERNAL_DIR_NAME = ".report-agent"
MATERIAL_WORK_DIR_NAME = "素材处理"
CANDIDATES_DIR_NAME = "候选报告"
RECORDS_DIR_NAME = "评测与改写记录"
ROUND_REQUEST_FILE = "本轮需求.md"
VERSION_LEDGER_FILE = "版本说明.md"
DATA_LEDGER_FILE = "数据版本说明.md"
STRUCTURED_DATA_FILE = "structured_data.json"

# 扫描原始素材时排除的产物
SCAN_EXCLUDED_NAMES = frozenset({
    REPORT_DIR_NAME,
    INTERNAL_DIR_NAME,
    STRUCTURED_DATA_FILE,
    DATA_LEDGER_FILE,
})

_UNSAFE_TOPIC = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_REPORT_ID = re.compile(r"^(?P<topic>.+)-(?P<stamp>\d{8}-\d{6})$")
_LOOP_DIR = re.compile(r"^loop-(?P<seq>\d{3})-(?P<stamp>\d{8}-\d{6})$")
_MATERIAL_ROUND = re.compile(r"^r(?P<seq>\d{3})$")


class WorkspaceError(RuntimeError):
    """工作区路径非法或不可写。"""


def sanitize_topic(topic: str) -> str:
    """把报告主题归一化成可用作文件名的片段。"""
    value = _UNSAFE_TOPIC.sub("", str(topic or "").strip())
    value = re.sub(r"\s+", " ", value).strip(" .")
    if not value:
        raise WorkspaceError("报告主题不能为空")
    # 留出 -vNN.md / -YYYYMMDD-HHmmss 后缀空间
    return value[:80]


def derive_material_root(material_paths: list[str]) -> Path:
    """从已校验的素材路径推导素材目录。

    素材路径在 Job 校验时已确认存在，因此这里是可信锚点。取各素材所在目录
    的公共父目录；退化到文件系统根或家目录时拒绝，交由调用方要求显式指定，
    不静默换目录。
    """
    if not material_paths:
        raise WorkspaceError("缺少重点素材路径，无法推导素材目录")
    directories = []
    for raw in material_paths:
        path = Path(raw).expanduser().resolve()
        directories.append(str(path if path.is_dir() else path.parent))
    common = Path(os.path.commonpath(directories)).resolve()
    home = Path.home().resolve()
    if common == common.parent or common in (home, home.parent):
        raise WorkspaceError(
            f"素材分散在互不相关的位置（公共父目录为 {common}），"
            "请明确指定报告保存位置"
        )
    return common


def resolve_report_dir(
    *,
    material_root: Path,
    requested: str | None = None,
) -> Path:
    """用户指定位置优先，否则使用 素材目录/报告/。"""
    if str(requested or "").strip():
        return Path(str(requested).strip()).expanduser().resolve()
    return (material_root / REPORT_DIR_NAME).resolve()


def ensure_writable(directory: Path) -> Path:
    """确认目录可创建且可写；不可写时明确失败，不静默换目录。"""
    target = Path(directory).expanduser().resolve()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkspaceError(f"报告目录无法创建：{target}（{exc}）") from exc
    if not os.access(target, os.W_OK):
        raise WorkspaceError(f"报告目录不可写：{target}")
    return target


def assert_within(path: Path, root: Path, *, label: str) -> Path:
    """校验 path 落在 root 子树内，越界即拒绝。"""
    resolved = Path(path).expanduser().resolve()
    root_resolved = Path(root).expanduser().resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise WorkspaceError(
            f"{label} 超出报告目录范围：{resolved} 不在 {root_resolved} 内"
        ) from exc
    return resolved


def new_report_id(topic: str, *, now: datetime | None = None) -> str:
    """生成 <报告主题>-<首次创建日期时间> 报告标识。"""
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return f"{sanitize_topic(topic)}-{stamp}"


def parse_report_id(report_id: str) -> tuple[str, str]:
    """拆出 (主题, 时间戳)；格式不符即拒绝。"""
    match = _REPORT_ID.match(str(report_id or "").strip())
    if not match:
        raise WorkspaceError(f"非法报告标识：{report_id}")
    return match.group("topic"), match.group("stamp")


def internal_root(report_dir: Path) -> Path:
    return (Path(report_dir).resolve() / INTERNAL_DIR_NAME).resolve()


def report_work_dir(report_dir: Path, report_id: str) -> Path:
    """同一报告的内部目录；多次 Loop 共用。"""
    parse_report_id(report_id)
    return (internal_root(report_dir) / report_id).resolve()


def find_existing_report_ids(report_dir: Path, topic: str) -> list[str]:
    """列出同主题的已有报告标识，按时间戳升序。

    用于续写判定：用户反馈后重启 Loop 应沿用已有标识，而不是另建报告。
    """
    root = internal_root(report_dir)
    if not root.is_dir():
        return []
    wanted = sanitize_topic(topic)
    found = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        try:
            existing_topic, stamp = parse_report_id(entry.name)
        except WorkspaceError:
            continue
        if existing_topic == wanted:
            found.append((stamp, entry.name))
    return [name for _, name in sorted(found)]


def next_loop_dir(
    report_dir: Path,
    report_id: str,
    *,
    now: datetime | None = None,
) -> Path:
    """为新一轮 Loop 分配 loop-序号-时间戳/ 目录。

    序号在同一 reportId 下递增，因此用户大改后重启 Loop 得到 loop-002，
    与首轮并列保留，评测历史不丢。
    """
    work_dir = report_work_dir(report_dir, report_id)
    highest = 0
    if work_dir.is_dir():
        for entry in work_dir.iterdir():
            match = _LOOP_DIR.match(entry.name) if entry.is_dir() else None
            if match:
                highest = max(highest, int(match.group("seq")))
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return (work_dir / f"loop-{highest + 1:03d}-{stamp}").resolve()


def existing_loop_dirs(report_dir: Path, report_id: str) -> list[Path]:
    """已有 Loop 目录，按序号升序。"""
    work_dir = report_work_dir(report_dir, report_id)
    if not work_dir.is_dir():
        return []
    found = []
    for entry in work_dir.iterdir():
        match = _LOOP_DIR.match(entry.name) if entry.is_dir() else None
        if match:
            found.append((int(match.group("seq")), entry))
    return [path for _, path in sorted(found)]


def next_material_round_dir(report_dir: Path, report_id: str) -> Path:
    """素材处理目录 r001、r002……仅在解析或记录事实更正时创建。"""
    parent = report_work_dir(report_dir, report_id) / MATERIAL_WORK_DIR_NAME
    highest = 0
    if parent.is_dir():
        for entry in parent.iterdir():
            match = _MATERIAL_ROUND.match(entry.name) if entry.is_dir() else None
            if match:
                highest = max(highest, int(match.group("seq")))
    return (parent / f"r{highest + 1:03d}").resolve()


@dataclass(frozen=True)
class LoopPaths:
    """一轮 Loop 的产物位置。目录按需创建，不预建空目录。"""

    loop_dir: Path
    round_request: Path
    candidates_dir: Path
    records_dir: Path
    run_state: Path
    resolution_plan: Path
    judgments_dir: Path
    revision_briefs_dir: Path

    @classmethod
    def under(cls, loop_dir: Path) -> "LoopPaths":
        loop = Path(loop_dir).resolve()
        records = loop / RECORDS_DIR_NAME
        return cls(
            loop_dir=loop,
            round_request=loop / ROUND_REQUEST_FILE,
            candidates_dir=loop / CANDIDATES_DIR_NAME,
            records_dir=records,
            run_state=records / "run-state.json",
            resolution_plan=records / "resolution-plan.json",
            judgments_dir=records / "judgments",
            revision_briefs_dir=records / "revision-briefs",
        )

    def candidate(self, index: int) -> Path:
        """内部候选 R0 初稿、R1、R2……每轮独立递增。"""
        if index < 0:
            raise WorkspaceError("候选序号不能为负")
        return self.candidates_dir / f"R{index}.md"

    def judgment(self, candidate: str) -> Path:
        return self.judgments_dir / f"{candidate}.json"

    def revision_brief(self, candidate: str) -> Path:
        return self.revision_briefs_dir / f"{candidate}.json"


def next_candidate_index(paths: LoopPaths) -> int:
    """下一个 RN 序号；空目录时为 0（R0 初稿）。"""
    if not paths.candidates_dir.is_dir():
        return 0
    highest = -1
    for entry in paths.candidates_dir.glob("R*.md"):
        suffix = entry.stem[1:]
        if suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest + 1


def delivered_versions(report_dir: Path, topic: str) -> list[int]:
    """已交付的 vN 序号，升序。"""
    directory = Path(report_dir).resolve()
    if not directory.is_dir():
        return []
    prefix = sanitize_topic(topic)
    pattern = re.compile(rf"^{re.escape(prefix)}-v(\d+)$")
    versions = []
    for entry in directory.glob(f"{prefix}-v*.md"):
        match = pattern.match(entry.stem)
        if match:
            versions.append(int(match.group(1)))
    return sorted(versions)


def next_version(report_dir: Path, topic: str) -> int:
    """下一个交付版本号。

    Loop 交付与直接改写共用编号：取已有最大序号加一，旧版保留。正文未变时
    调用方不应发布新版本，只更新评测记录。
    """
    existing = delivered_versions(report_dir, topic)
    return (existing[-1] + 1) if existing else 1


def delivery_path(report_dir: Path, topic: str, version: int) -> Path:
    """报告/<报告主题>-vN.md"""
    if version < 1:
        raise WorkspaceError("报告版本号从 1 开始")
    return (Path(report_dir).resolve() / f"{sanitize_topic(topic)}-v{version}.md").resolve()


def version_ledger_path(report_dir: Path) -> Path:
    return (Path(report_dir).resolve() / VERSION_LEDGER_FILE).resolve()


def data_ledger_path(material_root: Path) -> Path:
    return (Path(material_root).resolve() / DATA_LEDGER_FILE).resolve()


def structured_data_path(material_root: Path) -> Path:
    return (Path(material_root).resolve() / STRUCTURED_DATA_FILE).resolve()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def hide_internal_dir(path: Path) -> bool:
    """Windows 上给内部目录设置 Hidden；失败不阻塞交付。

    macOS/Linux 依赖点号前缀，本身已隐藏，直接返回 True。
    """
    target = Path(path)
    if not target.exists():
        return False
    if sys.platform != "win32":
        return True
    try:
        completed = subprocess.run(
            ["attrib", "+h", str(target)],
            capture_output=True,
            check=False,
            timeout=10,
        )
        return completed.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def is_scan_excluded(name: str, *, report_work_dirs: frozenset[str] = frozenset()) -> bool:
    """扫描原始素材时是否排除该条目。"""
    return name in SCAN_EXCLUDED_NAMES or name in report_work_dirs
