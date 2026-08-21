# Research Report Memory V2 MVP

面向 `research-report` Skill 的专用写作记忆插件。主写作 Agent 专注需求澄清、素材分析与报告写作；独立 WorkBuddy Memory Sub-agent 负责 Recall、反馈提炼和定期整理。

## MVP 架构

```text
research-report Skill / Memory Hook
              ↓ 委派
WB Memory Sub-agent
              ↓ 调用
Memory MCP / Runtime
        ├── L0 Episode：完整 JSON
        ├── L1 Atom：TencentDB MemoryCore / SQLite
        └── L2 Context + L3 Rubrics：Git-backed Markdown Repository
```

- Recall：需求确认后，Sub-agent 读取 L2/L3，必要时补充 L1，向主 Agent 返回固定 Memory Prompt。
- Capture：用户反馈后，Sub-agent 保存完整 L0 Episode，其中包含以反馈为锚点的原始 user/assistant 对话窗口，再判断 L1 变化，并即时 review、按需 Commit 相关 Scope 的 L2/L3；新 active Rubrics 可直接用于本轮后续 Judge。Capture 通过单字符串 `writing_memory_capture_payload` 调用，避免 WorkBuddy 参数损坏并兼容其自定义 Agent 契约。
- Maintenance：独立 WB CLI 会话每天复查 pending L0、重复/冲突/过期 L1，以及 L2/L3 的错误提炼和上下文膨胀。
- Hook：只检查结果，不判断调用方。写前等待 Curator 返回 `MEMORY_RECALL_COMPLETED`，反馈后、交付前等待 `MEMORY_CAPTURE_COMPLETED`；若 Curator 返回明确的 `*_FAILED`，如实说明后放行原任务，避免 Memory 故障卡住报告写作。

## 记忆结构

记忆统一按 **Layer × Scope** 分类：Layer 表示加工程度和用途，Scope 表示未来适用范围。L1–L3 每条记忆只属于一个 `core / audience / project` Scope；L0 是不按长期 Scope 归类的来源证据层。

| 层级 | 内容 | 存储 |
|---|---|---|
| L0 Episode Memory | 原始反馈事件窗口、任务语境、报告前后版本、Judge 与人工修改 | `l0-l1-memory/l0-episodes/*.json`（唯一权威数据） |
| L1 Atom Memory | 单条可复用写作要求 | `l0-l1-memory/l1-atoms/records/*.jsonl` + `l0-l1-memory/memorycore/vectors.db` |
| L2 Context Memory | Writing Core、Audience、Project 场景总结 | Git-backed Markdown |
| L3 Rubrics Memory | `criterion / pass / fail` 自检和 Judge 标准 | Git-backed Markdown |

L1/L2/L3 Scope 只使用：

- `core`：跨项目长期生效；
- `audience`：特定受众或汇报环境；
- `project`：特定项目。

冲突优先级：本轮要求 > project > audience > core > Skill。

MVP 不设计额外 Dimension；记忆在 Scope 内按自然语言主题组织和更新。

Audience 使用宿主提供的名称作为 Scope 标识；调用方应在 Recall 与 Capture 时使用一致的受众名称。

## 本地数据

默认数据目录：`~/.research-report-memory-v2-mvp`。

默认同时在用户主目录创建 `~/Research Report Memory` 快捷方式，Windows 对应 `%USERPROFILE%\\Research Report Memory`，指向 `l2-l3-memory/personal/default/`，方便直接查看和编辑 L2/L3 Markdown。已有同名文件或目录时不会覆盖；可用 `RESEARCH_REPORT_MEMORY_SHORTCUT` 自定义路径，或设为 `0` 禁用。

```text
~/.research-report-memory-v2-mvp/
├── l0-l1-memory/
│   ├── l0-episodes/                # 完整 L0 Writing Episode；L0 唯一权威数据
│   ├── l1-atoms/records/           # L1 Atom JSONL 审计记录
│   └── memorycore/                 # TencentDB MemoryCore L1 检索引擎
│       ├── vectors.db              # L1 当前查询数据
│       ├── conversations/          # MemoryCore 内部兼容目录；本产品不写入 L0
│       ├── records/                # MemoryCore 启动时创建的空兼容目录；L1 JSONL 在 l1-atoms/records
│       ├── scene_blocks/           # MemoryCore 原生 L2 目录；本产品未使用
│       ├── .metadata/              # MemoryCore manifest/checkpoint
│       └── .backup/                # MemoryCore 滚动备份预留目录
├── maintenance/state.json
├── maintenance-logs/               # 每日整理任务日志
├── hook-state/                     # Recall/Capture 流程门禁的临时状态
└── l2-l3-memory/
    ├── personal/default/
    │   ├── .git/
    │   ├── system/l2-context.md + l3-rubrics.md
    │   ├── audiences/<audience>/l2-context.md + l3-rubrics.md
    │   ├── projects/<project>/l2-context.md + l3-rubrics.md
    │   └── .memory/provenance.jsonl
    └── worktrees/                   # L2/L3 原子提交使用的临时 Git Worktree
```

L2/L3 以可见 Markdown 为唯一权威数据：Item ID 使用 `##` 标题，来源使用简短的 `<!-- sources: ... -->` 注释；正文、`Rules`、`Criterion / Pass / Fail / Status` 均由解析器直接读取，不保存隐藏的完整 JSON 副本。

完整 Episode JSON 是 L0 的唯一权威数据；不再向 MemoryCore SQLite 写入 L0 镜像。MemoryCore 从 L1 开始承担 JSONL 持久化和 SQLite 检索，`conversations` 仅作为上游兼容目录保留。L2/L3 则由独立的 Git-backed Markdown 管理。

L0 不复制整段 Session。它从用户正在评价的上一条 Assistant 可见输出开始，逐字保存到当前用户反馈结束，通常 2–6 条、最多 8 条 user/assistant 消息；不保存 System Prompt、模型推理或工具日志。`contextBefore / contextAfter` 只作为提炼摘要，不能替代 `conversationExcerpt`。过长时只允许截断 Assistant 内容，并显式记录省略原因；用户反馈必须完整保留。

旧 Episode 仍可读取。若能从宿主会话日志可靠恢复原始消息，可用相同 `externalSourceId` 重放 Capture：Runtime 只补齐缺失的 `conversationExcerpt`，不会覆盖已有原始消息，也不会重复更新 L1/L2/L3。

这个目录与 v0.2.2 的 `~/.research-report-memory`、v1 的 `~/.research-report-memory-v1` 完全隔离。

## 开发与验证

```bash
npm install
npm run syntaxcheck
npm test
npm run build:release
```

WorkBuddy 开发加载：

```bash
codebuddy --plugin-dir /absolute/path/to/research-report-memory-v2-mvp
```

手动运行一次 Maintenance：

```bash
./scripts/run-memory-maintenance-workbuddy.sh
```

安装 macOS 每天 16:30 的任务：

```bash
./scripts/install-maintenance-macos.sh
```

## MVP 复用边界

- 复用 memory-v1：MCP stdio Server、MemoryCore L0/L1 直写、Writing Episode、WB Agent frontmatter、Memory Guard、定时 WB CLI 与正式安装脚本。
- 新增：`core/audience/project` Scope、L3 Rubrics、固定 Recall Plan、Git-backed Context Repository、Worktree Commit/Revert 基础和 Sub-agent Recall checkpoint。
- MVP 不部署 Letta Native Agent，也不需要额外模型 API；Letta 的 Context Repository、Memory Patch、Worktree 和 Shared Memory 思路用于存储设计，后续再评估直接接入其 Runtime。

目标功能设计见 [research-report-memory_融合方案设计.md](./research-report-memory_融合方案设计.md)。
