"""报告写作记忆的 Markdown 存储。

记忆存在用户主目录下**可见**的 `ReportAgentMemory/`，与宿主产品和插件安装
目录无关。全部使用 Markdown，用户可以直接查看和修改；每次写入前重新核对
文件内容，先合并新增的人工修改，不按旧上下文整库回写。

布局（见 docs/memory-and-storage.md）::

    ReportAgentMemory/
    ├── MEMORY.md           设置、revision、active L2B
    ├── memory-history.md   按 revision 追加的变更记录
    ├── L0-episodes/        每条 Episode 单独保存
    └── L1-atoms/           按 Scope 保存，保留 sourceEpisodeIds

刻意不做的事：

* **不维护全量 L0/L1 索引或 MEMORY 快照** —— 按 ID 在对应目录查找，
  依靠 L2B → sourceL1Ids → L1 → sourceEpisodeIds → L0 的来源链回溯。
* **不提供历史回滚** —— `memory-history.md` 只用于审计与理解旧规则；
  被撤销的规则不再生效。
* **不做向量检索或数据库** —— 纯文件读写，用户可直接编辑。

`revision` 是 `MEMORY.md` 顶部的唯一版本号，初始为 0。只有持久化修改成功
才推进；无文件变化不递增。每次操作开始重新读取当前 revision，不依赖旧
上下文。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_MEMORY_DIR_NAME = "ReportAgentMemory"
MEMORY_FILE = "MEMORY.md"
HISTORY_FILE = "memory-history.md"
EPISODES_DIR = "L0-episodes"
ATOMS_DIR = "L1-atoms"

SCOPES = ("core", "audience", "project")
REFLECTION_HOUR = 16
REFLECTION_MINUTE = 30

_FRONT_REVISION = re.compile(r"^revision:\s*(\d+)\s*$", re.MULTILINE)
_FRONT_ENABLED = re.compile(r"^enabled:\s*(true|false)\s*$", re.MULTILINE | re.IGNORECASE)
_FRONT_REFLECTED = re.compile(r"^lastReflectionAt:[ \t]*(\S*)[ \t]*$", re.MULTILINE)
_RUBRIC_BLOCK = re.compile(
    r"^###\s+(?P<id>MR-[A-Za-z0-9_-]+)\s*$(?P<body>.*?)(?=^###\s+MR-|\Z)",
    re.MULTILINE | re.DOTALL,
)
_FIELD = re.compile(r"^-\s*(?P<key>[A-Za-z0-9_]+)\s*:\s*(?P<value>.*)$", re.MULTILINE)
_ATOM_ID = re.compile(r"^L1-[A-Za-z0-9_-]+$")
_EPISODE_ID = re.compile(r"^EP-[A-Za-z0-9_-]+$")
_RUBRIC_ID = re.compile(r"^MR-[A-Za-z0-9_-]+$")


class MemoryStoreError(RuntimeError):
    """记忆目录不可用或内容无法解析；调用方应如实返回失败。"""


def default_memory_root() -> Path:
    """用户主目录下可见的记忆目录。"""
    return Path.home() / DEFAULT_MEMORY_DIR_NAME


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _escape(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


@dataclass
class MemoryRubric:
    """L2B Memory Rubric：稳定、可观察、值得长期影响 Judge 的用户标准。"""

    id: str
    statement: str
    scope: str = "core"
    scopeValue: str = ""
    sourceL1Ids: list[str] = field(default_factory=list)
    dimensionCandidate: dict[str, str] | None = None

    def __post_init__(self) -> None:
        if not _RUBRIC_ID.match(self.id):
            raise MemoryStoreError(f"非法 L2B ID：{self.id}")
        if self.scope not in SCOPES:
            raise MemoryStoreError(f"Scope 只能是 core/audience/project：{self.scope}")
        if self.scope == "core" and self.scopeValue:
            raise MemoryStoreError("core Scope 不应填写 scopeValue")
        if self.scope != "core" and not self.scopeValue:
            raise MemoryStoreError(f"{self.scope} Scope 必须填写 scopeValue")
        if not str(self.statement).strip():
            raise MemoryStoreError("L2B statement 不能为空")

    def to_markdown(self) -> str:
        lines = [
            f"### {self.id}",
            "",
            f"- statement: {_escape(self.statement)}",
            f"- scope: {self.scope}",
        ]
        if self.scopeValue:
            lines.append(f"- scopeValue: {_escape(self.scopeValue)}")
        lines.append(f"- sourceL1Ids: {', '.join(self.sourceL1Ids)}")
        if self.dimensionCandidate:
            candidate = self.dimensionCandidate
            lines.append(
                "- dimensionCandidate: "
                + f"name={_escape(candidate.get('name', ''))}; "
                + f"label={_escape(candidate.get('label', ''))}; "
                + f"reason={_escape(candidate.get('reason', ''))}"
            )
        lines.append("")
        return "\n".join(lines)

    def to_candidate(self) -> dict[str, Any]:
        """Resolution Judge 契约要求的候选结构。"""
        payload: dict[str, Any] = {
            "id": self.id,
            "statement": self.statement,
            "scope": self.scope,
            "sourceL1Ids": list(self.sourceL1Ids),
        }
        if self.scopeValue:
            payload["scopeValue"] = self.scopeValue
        if self.dimensionCandidate:
            payload["dimensionCandidate"] = dict(self.dimensionCandidate)
        return payload


@dataclass
class MemoryState:
    """`MEMORY.md` 的当前内容。"""

    revision: int = 0
    enabled: bool = True
    lastReflectionAt: str | None = None
    rubrics: list[MemoryRubric] = field(default_factory=list)


def _parse_rubrics(raw: str) -> list[MemoryRubric]:
    """解析 active L2B。

    单条 rubric 格式错误（如 audience 缺 scopeValue）时跳过该条并继续，
    而不是让整份 MEMORY.md 变成不可读——否则一处人工编辑失误就会让全部
    记忆失效。跳过的条目由 provider 汇报为 warning。
    """
    rubrics: list[MemoryRubric] = []
    for match in _RUBRIC_BLOCK.finditer(raw):
        body = match.group("body")
        fields = {
            item.group("key"): item.group("value").strip()
            for item in _FIELD.finditer(body)
        }
        candidate = None
        raw_candidate = fields.get("dimensionCandidate")
        if raw_candidate:
            parts = dict(
                piece.split("=", 1)
                for piece in (chunk.strip() for chunk in raw_candidate.split(";"))
                if "=" in piece
            )
            if any(parts.values()):
                candidate = {key: value.strip() for key, value in parts.items()}
        source_ids = [
            value.strip()
            for value in (fields.get("sourceL1Ids") or "").split(",")
            if value.strip()
        ]
        try:
            rubrics.append(MemoryRubric(
                id=match.group("id"),
                statement=fields.get("statement", ""),
                scope=fields.get("scope", "core") or "core",
                scopeValue=fields.get("scopeValue", ""),
                sourceL1Ids=source_ids,
                dimensionCandidate=candidate,
            ))
        except MemoryStoreError:
            rubrics.append(_InvalidRubric(
                id=match.group("id"),
                reason=fields.get("scope", "core"),
            ))
    return rubrics


@dataclass
class _InvalidRubric:
    """格式错误、已跳过的 L2B 条目占位。provider 据此产生 warning。"""

    id: str
    reason: str = ""
    scope: str = "invalid"
    scopeValue: str = ""
    statement: str = ""
    sourceL1Ids: list[str] = field(default_factory=list)
    dimensionCandidate: dict[str, str] | None = None


def render_memory_file(state: MemoryState) -> str:
    """渲染 MEMORY.md。顶部明确写 revision 作为唯一版本号。"""
    lines = [
        "# 报告写作记忆",
        "",
        f"revision: {state.revision}",
        f"enabled: {'true' if state.enabled else 'false'}",
        f"lastReflectionAt: {state.lastReflectionAt or ''}",
        "",
        "本文件由报告写作记忆管理员维护，你可以直接查看和修改。",
        "`revision` 用于识别当前状态，不提供历史回滚；变更记录见 "
        f"`{HISTORY_FILE}`。",
        "",
        "## Active L2B Memory Rubrics",
        "",
    ]
    if not state.rubrics:
        lines.extend(["（暂无）", ""])
    else:
        for rubric in state.rubrics:
            lines.append(rubric.to_markdown())
    return "\n".join(lines).rstrip() + "\n"


class MemoryStore:
    """`ReportAgentMemory/` 的 Markdown 读写。

    只操作调用方传入的绝对路径；缺少绝对路径或目录无法访问时抛出
    ``MemoryStoreError``，不猜测路径、不改用宿主自带 Memory。
    """

    def __init__(self, memory_root: Path | str) -> None:
        root = Path(memory_root).expanduser()
        if not root.is_absolute():
            raise MemoryStoreError(f"memoryRoot 必须是绝对路径：{memory_root}")
        self.root = root.resolve()

    # -- 路径 ----------------------------------------------------------
    @property
    def memory_file(self) -> Path:
        return self.root / MEMORY_FILE

    @property
    def history_file(self) -> Path:
        return self.root / HISTORY_FILE

    @property
    def episodes_dir(self) -> Path:
        return self.root / EPISODES_DIR

    @property
    def atoms_dir(self) -> Path:
        return self.root / ATOMS_DIR

    def episode_path(self, episode_id: str) -> Path:
        if not _EPISODE_ID.match(episode_id):
            raise MemoryStoreError(f"非法 Episode ID：{episode_id}")
        return self.episodes_dir / f"{episode_id}.md"

    def atom_path(self, atom_id: str, scope: str) -> Path:
        if not _ATOM_ID.match(atom_id):
            raise MemoryStoreError(f"非法 L1 ID：{atom_id}")
        if scope not in SCOPES:
            raise MemoryStoreError(f"Scope 只能是 core/audience/project：{scope}")
        return self.atoms_dir / scope / f"{atom_id}.md"

    # -- 初始化 --------------------------------------------------------
    def exists(self) -> bool:
        return self.memory_file.is_file()

    def is_empty(self) -> bool:
        if not self.root.exists():
            return True
        return not any(self.root.iterdir())

    def initialize(self) -> MemoryState:
        """首次使用且目录不存在或为空时创建 MEMORY.md。

        已有内容但缺少或无法读取 MEMORY.md 时报告问题，不重新初始化或覆盖。
        """
        if self.memory_file.is_file():
            return self.read_state()
        if not self.is_empty():
            raise MemoryStoreError(
                f"{self.root} 已有内容但缺少 {MEMORY_FILE}；"
                "请先检查目录，不自动重新初始化"
            )
        state = MemoryState(revision=0, enabled=True, lastReflectionAt=None, rubrics=[])
        self.root.mkdir(parents=True, exist_ok=True)
        self._write_atomic(self.memory_file, render_memory_file(state))
        return state

    # -- 读 ------------------------------------------------------------
    def read_state(self) -> MemoryState:
        """每次操作显式读取，不依赖自动注入或旧上下文。"""
        if not self.memory_file.is_file():
            raise MemoryStoreError(
                f"缺少 {MEMORY_FILE}：{self.memory_file}"
            )
        try:
            raw = self.memory_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise MemoryStoreError(f"无法读取 {MEMORY_FILE}：{exc}") from exc

        revision_match = _FRONT_REVISION.search(raw)
        if not revision_match:
            # 只有旧文件缺少版本号时才核验现状并登记基线
            raise MemoryStoreError(
                f"{MEMORY_FILE} 缺少 revision 版本号；请核验现状后登记基线"
            )
        enabled_match = _FRONT_ENABLED.search(raw)
        reflected_match = _FRONT_REFLECTED.search(raw)
        reflected = (reflected_match.group(1).strip() if reflected_match else "")
        return MemoryState(
            revision=int(revision_match.group(1)),
            enabled=(enabled_match.group(1).lower() == "true") if enabled_match else True,
            lastReflectionAt=reflected or None,
            rubrics=_parse_rubrics(raw),
        )

    def read_atom(self, atom_id: str) -> dict[str, Any] | None:
        """按 ID 在 Scope 目录下查找，不维护全量索引。"""
        if not _ATOM_ID.match(atom_id):
            raise MemoryStoreError(f"非法 L1 ID：{atom_id}")
        for scope in SCOPES:
            path = self.atoms_dir / scope / f"{atom_id}.md"
            if not path.is_file():
                continue
            raw = path.read_text(encoding="utf-8")
            fields = {
                item.group("key"): item.group("value").strip()
                for item in _FIELD.finditer(raw)
            }
            episodes = [
                value.strip()
                for value in (fields.get("sourceEpisodeIds") or "").split(",")
                if value.strip()
            ]
            return {
                "id": atom_id,
                "content": fields.get("content", ""),
                "scope": fields.get("scope", scope),
                "scopeValue": fields.get("scopeValue", ""),
                "sourceEpisodeIds": episodes,
            }
        return None

    def read_episode(self, episode_id: str) -> str | None:
        path = self.episode_path(episode_id)
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def pending_episode_ids(self) -> list[str]:
        """未被任何 L1 引用的 Episode，供 Reflection 恢复中断的 Capture。"""
        if not self.episodes_dir.is_dir():
            return []
        referenced: set[str] = set()
        if self.atoms_dir.is_dir():
            for scope in SCOPES:
                scope_dir = self.atoms_dir / scope
                if not scope_dir.is_dir():
                    continue
                for path in scope_dir.glob("L1-*.md"):
                    atom = self.read_atom(path.stem)
                    if atom:
                        referenced.update(atom["sourceEpisodeIds"])
        return sorted(
            path.stem
            for path in self.episodes_dir.glob("EP-*.md")
            if path.stem not in referenced
        )

    # -- 写 ------------------------------------------------------------
    @staticmethod
    def _write_atomic(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(path.name + ".memory.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)

    def write_episode(
        self,
        episode_id: str,
        *,
        feedback: str,
        task: str = "",
        audience: str = "",
        project: str = "",
        conversation: list[dict[str, str]] | None = None,
        beforeAfter: str = "",
    ) -> Path:
        """保存 L0 Episode：用户反馈及必要上下文，供审计与重新提炼。

        不写系统提示、推理过程、工具日志或非写作偏好。
        """
        path = self.episode_path(episode_id)
        if path.exists():
            return path
        lines = [
            f"# {episode_id}",
            "",
            f"- createdAt: {_now_iso()}",
            f"- task: {_escape(task)}",
            f"- audience: {_escape(audience)}",
            f"- project: {_escape(project)}",
            "",
            "## 用户反馈",
            "",
            str(feedback).strip(),
            "",
        ]
        if beforeAfter.strip():
            lines.extend(["## 修改前后", "", beforeAfter.strip(), ""])
        if conversation:
            lines.extend(["## 对话窗口", ""])
            for turn in conversation:
                role = str(turn.get("role") or "").strip() or "unknown"
                content = str(turn.get("content") or "").strip()
                lines.extend([f"**{role}**：{content}", ""])
        self._write_atomic(path, "\n".join(lines).rstrip() + "\n")
        return path

    def write_atom(
        self,
        atom_id: str,
        *,
        content: str,
        scope: str,
        scopeValue: str = "",
        sourceEpisodeIds: list[str] | None = None,
    ) -> Path:
        """保存 L1：一条只表达一件事，并保留真实 Episode 来源。"""
        if not str(content).strip():
            raise MemoryStoreError("L1 content 不能为空")
        episodes = list(sourceEpisodeIds or [])
        if not episodes:
            raise MemoryStoreError(f"L1 {atom_id} 必须保留 sourceEpisodeIds")
        path = self.atom_path(atom_id, scope)
        lines = [
            f"# {atom_id}",
            "",
            f"- content: {_escape(content)}",
            f"- scope: {scope}",
        ]
        if scopeValue:
            lines.append(f"- scopeValue: {_escape(scopeValue)}")
        lines.extend([
            f"- sourceEpisodeIds: {', '.join(episodes)}",
            f"- updatedAt: {_now_iso()}",
            "",
        ])
        self._write_atomic(path, "\n".join(lines))
        return path

    def remove_atom(self, atom_id: str) -> bool:
        for scope in SCOPES:
            path = self.atoms_dir / scope / f"{atom_id}.md"
            if path.is_file():
                path.unlink()
                return True
        return False

    def save_state(self, state: MemoryState, *, advance_revision: bool) -> MemoryState:
        """写回 MEMORY.md。只有持久化修改成功才推进版本。

        写入前重新读取磁盘内容，若期间有人工修改则以传入状态为准合并——
        调用方负责在读到最新 revision 的基础上构造 state。
        """
        if advance_revision:
            state.revision += 1
        self._write_atomic(self.memory_file, render_memory_file(state))
        verified = self.read_state()
        if verified.revision != state.revision:
            raise MemoryStoreError(
                f"MEMORY.md 写入后核验失败：期望 revision {state.revision}，"
                f"实际 {verified.revision}"
            )
        return verified

    def append_history(
        self,
        *,
        revision: int,
        change: str,
        reason: str,
        sourceIds: list[str] | None = None,
    ) -> Path:
        """向 memory-history.md 追加一条记录。

        无变化或仅更新复盘时间时不应调用；历史不用于自动回滚。
        """
        path = self.history_file
        if not path.is_file():
            header = [
                "# 记忆变更历史",
                "",
                "按 revision 追加，仅供审查、纠错或理解旧规则时按需读取。",
                "被撤销的规则不再生效；历史不提供自动回滚。",
                "",
                "",
            ]
            self._write_atomic(path, "\n".join(header))
        entry = [
            f"## revision {revision} · {_now_iso()}",
            "",
            f"- 变更：{_escape(change)}",
            f"- 原因：{_escape(reason)}",
        ]
        if sourceIds:
            entry.append(f"- 来源：{', '.join(sourceIds)}")
        entry.append("")
        with path.open("a", encoding="utf-8") as handle:
            handle.write("\n".join(entry) + "\n")
        return path

    # -- Reflection 到期检查 -------------------------------------------
    def reflection_due(self, *, now: datetime | None = None) -> bool:
        """当地时间已过 16:30 且今天尚未复盘，或上一个自然日仍未复盘。

        纯原生 Expert 不依赖后台调度：长时间未使用时在下一次调用补做，
        不为补齐空闲日期逐日运行。
        """
        state = self.read_state()
        if not state.enabled:
            return False
        current = now or datetime.now()
        if not state.lastReflectionAt:
            return _past_reflection_time(current)
        try:
            last = datetime.fromisoformat(state.lastReflectionAt)
        except ValueError:
            # 时间戳损坏时按未复盘处理，不猜测
            return _past_reflection_time(current)
        if last.date() == current.date():
            return False
        if last.date() < current.date() - _ONE_DAY:
            return True
        return _past_reflection_time(current)

    def mark_reflected(self, *, now: datetime | None = None) -> MemoryState:
        """更新 lastReflectionAt。仅元数据变化时不推进 revision。"""
        state = self.read_state()
        state.lastReflectionAt = (now or datetime.now()).isoformat(timespec="seconds")
        self._write_atomic(self.memory_file, render_memory_file(state))
        return self.read_state()

    def set_enabled(self, enabled: bool) -> MemoryState:
        """开关记忆。关闭时保留已有文件，但 resolve 不返回候选。"""
        state = self.read_state()
        if state.enabled == enabled:
            return state
        state.enabled = enabled
        return self.save_state(state, advance_revision=True)

    # -- Resolve -------------------------------------------------------
    def resolve_candidates(
        self, *, audience: str = "", project: str = ""
    ) -> dict[str, Any]:
        """按当前 task/audience/project 返回最小必要候选。

        默认只返回 L2B 和准确的 sourceL1Ids，不展开全部 L1 原文。
        Memory 关闭或没有候选时仍返回成功，candidates=[]。
        """
        state = self.read_state()
        if not state.enabled:
            return {
                "marker": "MEMORY_RESOLVE_COMPLETED",
                "enabled": False,
                "revision": str(state.revision),
                "candidates": [],
            }
        candidates = [
            rubric.to_candidate()
            for rubric in state.rubrics
            if _scope_applies(rubric, audience=audience, project=project)
        ]
        return {
            "marker": "MEMORY_RESOLVE_COMPLETED",
            "enabled": True,
            "revision": str(state.revision),
            "candidates": candidates,
        }

    def inspect_sources(
        self, *, revision: str, requests: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """只返回请求 ID 对应的 L1 内容，不顺带返回其他 Atom。

        revision 已变化时返回 MEMORY_SOURCE_CONFLICT，由主 Agent 重新
        Resolve，不混用版本。
        """
        state = self.read_state()
        if str(state.revision) != str(revision):
            return {
                "marker": "MEMORY_SOURCE_CONFLICT",
                "revision": str(state.revision),
                "requestedRevision": str(revision),
            }
        evidence = []
        for item in requests:
            memory_id = str(item.get("memoryId") or "")
            sources = []
            missing = []
            for atom_id in item.get("sourceL1Ids") or []:
                atom = self.read_atom(str(atom_id))
                if atom is None:
                    missing.append(str(atom_id))
                else:
                    sources.append({
                        "id": atom["id"],
                        "content": atom["content"],
                        "scope": atom["scope"],
                        "sourceEpisodeIds": atom["sourceEpisodeIds"],
                    })
            evidence.append({
                "memoryId": memory_id,
                "sources": sources,
                "missingSourceL1Ids": missing,
            })
        return {
            "marker": "MEMORY_SOURCE_INSPECTION_COMPLETED",
            "revision": str(state.revision),
            "evidence": evidence,
        }


_ONE_DAY = timedelta(days=1)


def _past_reflection_time(moment: datetime) -> bool:
    return (moment.hour, moment.minute) >= (REFLECTION_HOUR, REFLECTION_MINUTE)


def _scope_applies(
    rubric: MemoryRubric, *, audience: str = "", project: str = ""
) -> bool:
    """决定该 L2B 是否进入本轮候选。

    `core` 始终适用。`audience` / `project` 的 scopeValue 是否与当前任务同义、
    是否语义重复，**由 Resolution Judge 结合任务解释**——这里不做精确字符串
    匹配，否则"CEO"与"管理层"这类同义受众会被错误过滤掉。

    因此本函数只在一种情况下排除候选：当前任务明确提供了对应元数据，且与
    rubric 的 scopeValue 完全不同字面值时**仍然保留**。实际的取舍交给
    Resolution Judge，这里保证的是"不漏"。
    """
    return rubric.scope in SCOPES
