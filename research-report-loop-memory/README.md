# Research Report Loop + Memory

可安装的 WorkBuddy 集成插件：宿主 Agent 负责初稿写作，Python Runner 调度按 Rubric Dimension 隔离的 Judge 与持久 Rewriter 自动迭代；用户对终稿的明确写作反馈由 Memory Curator 保存为 L0/L1，并在证据充分时升级 Git-backed Rubric Set。

## 核心链路

1. `research-report-loop` Skill 确认需求并由宿主 Agent 写初稿。
2. Memory Curator 将稳定反馈维护为 `core / audience / project` 下的独立 L2B Memory Rubrics，不修改或预先映射 Base Rubrics。
3. 宿主写入 Job Schema v2，插件 PostToolUse Hook 自动在 Agent 沙箱外启动 Python Runner；Runner 读取 Base 与全部 L2B 候选，由 Resolution Judge 按当前任务决定 `additional / interpret / ignore`，再冻结本轮 Rubric。该链路不依赖 Report Loop 工具是否被当前会话索引。
4. Runner 默认并发六个隔离 Judge，不创建第七维。Judge Provider 默认是 WorkBuddy `gpt-5.6-sol / medium`，也可选 Codex `gpt-5.6-sol / medium`；调用失败、空响应或 Judge JSON 不合规时，自动切换到 WorkBuddy App 当前主模型。
5. Runner 使用 App 主模型启动一个持久 Rewriter CLI，串行执行 Rewrite → Judge，直到 5 分、连续两轮未采纳或 60 分钟，并返回历史最佳报告。
6. 用户明确反馈后，Skill 指引宿主先修改当前报告，再委派 `research-report-memory-curator` Capture；Judge 反馈不会进入 Memory。

写作 Agent 不直接 Recall Memory。L2B 只通过 Judge 影响后续报告，避免个性化记忆污染写作上下文。

## 精简的 Memory 调度

插件恢复独立 Memory 插件的两条调度链：

- **实时 Capture**：Capture-only Hook 识别已交付报告后的明确写作反馈，提醒宿主先修改报告，再通过 Agent/Task 委派 `research-report-memory-curator` 调用专用 MCP。Stop 最多检查一次是否遗漏 Capture；明确成功或失败后放行。
- **定时 Reflection**：Memory MCP 首次在 WorkBuddy 插件宿主中成功启动时，默认注册 macOS LaunchAgent 或 Windows Task Scheduler；每天 16:30 启动一次隔离的 WorkBuddy CLI Reflection Agent，复审待处理 L0/L1 和疑似冲突，并只提交 L2B 增量修改。用户关闭后不会被插件升级重新启用。

Hook 不注册 `PreToolUse`，不负责写前 Recall或报告内容校验。它只在 Job 写入后启动 Runner，返回结果文件路径与后台等待命令，并继续承担反馈 Capture checkpoint。Agent 用 `Bash(run_in_background=true)` 启动等待任务，收到 `<task-notification>` 后通过 `TaskOutput` 取得最终结果。Capture 明确失败不会回滚已完成报告。

## Report Loop 模型与隔离

- Writer 与 Rewriter 使用 WorkBuddy App 主对话中用户实际选择的模型；Hook 记录 session，Runner 从主会话 trace 自动读取 `requestModelId`，不再让 Agent 猜写模型 ID。
- Judge Provider 默认 `workbuddy`，固定为 `gpt-5.6-sol / medium`；也可在 Job 中明确选择 `codex`，固定为 `gpt-5.6-sol / medium`。Judge Prompt 均通过 stdin 传入。只有调用失败、空响应或输出不满足 Judge JSON 合约时，才熔断到 WorkBuddy App 当前主模型，评分低不会触发回退。
- 三项开场输入必须附带用户消息原文；系统/App 注入的路径和附件清单只算候选素材，不能确认重点素材或替代用户回答。
- 每个 Rubric Dimension 和每轮 Judge 使用独立 CLI 进程与上下文；query 和三项 intake 会传给每个 Judge。
- Rewriter 与 Judge 隔离，整个 Run 只保留一个 Rewriter stream-json 进程；它只接收净化后的 revision brief，不接收 Judge 原始输出。
## Hook、MCP 与 Runner 边界

- 只维护一个 `report-memory-v2` MCP Server，负责 L0/L1/L2B；其中保留 `report_loop_run` 作为兼容入口，但正常写作链路不依赖当前对话暴露该工具。
- Job、状态和内部结果统一放在报告目录的 `.report-loop/` 中。宿主 Hook 启动 `mcp/report_loop/runner.py` 后会阻止会话提前结束，直至宿主读取完成或失败结果。路径由插件根目录和 Job 动态解析，不写死用户名、安装目录或操作系统路径。
- Runner 对用户只交付最终报告和一个版本记录目录；评测 JSON、状态文件与工具日志不进入用户交付区。
- Python Runner 仍是唯一循环入口，负责 Rubric 编译、Judge、Rewrite、版本采纳与停止；Hook 不复制任何 Loop 逻辑。
- Agent 不直接运行 Python，也不需要申请访问 WorkBuddy 的认证或日志目录。

## Curator Prompt 双版本

- **默认 Principle-first**：`agents/research-report-memory-curator.md`。以未来用途和最小必要更新为判断主体，保留 Scope、Layer 与 MCP 安全契约，但不使用机械晋升阈值。
- **实验变体**：`prompts/research-report-memory-curator-v2-letta-first.md`。用于后续 A/B 验证；与默认版本共享 Runtime、Schema 和初始 Memory 快照。

两个版本不会同时暴露为两个同名 Agent。构建时只把选定 Prompt 写入安装包的 `agents/research-report-memory-curator.md`：

```bash
npm run build:release:prompt-v1
npm run build:release:prompt-v2
```

默认 `npm run build:release` 使用当前 Principle-first Curator。两个变体使用相同插件 ID，A/B 测试时应分轮安装，并使用相同的初始 Memory 快照。

## 本地数据

- Report Loop：`~/.research-report-loop/runs/<run-id>/`
- Memory：`~/.research-report-memory-v2-0821/`（为兼容已有数据保留；MCP 服务名为 `report-memory-v2`）
- Rubric Set Git Repository：`~/.research-report-memory-v2-0821/l2b-rubrics/personal/default/`
- 人类可读视图：上述目录的 `views/rubric-set.md`；Judge 以 JSON 和冻结文件为准。

集成插件复用现有 V2-0821 Memory 数据，不迁移或覆盖已有记忆。

旧版本若已经存在 Audience/Project L2B 文件，可先检查 Scope 路径迁移；默认仅 dry-run，确认无冲突后再执行 `--apply`：

```bash
node scripts/migrate-rubric-scope-paths.mjs
node scripts/migrate-rubric-scope-paths.mjs --apply
```

Report Memory 默认关闭。用户明确要求主 Agent 开启记忆后，Memory MCP 才会读写长期记忆，并调用 `scripts/install-reflection-macos.sh` 或 `scripts/install-reflection-windows.ps1` 注册每天 16:30 的 Reflection 任务。关闭 Memory 不会删除已有数据；已有定时任务即使仍被系统唤起，也会立即退出，不调用模型。

## Windows x64 安装包

Windows 包使用 `cmd.exe + run-node.cmd` 启动统一 MCP；宿主 Hook 使用 `run-python.cmd` 启动 Report Loop Runner，不依赖 Git Bash、WSL 或系统 `sh`。它优先复用 WorkBuddy 自带的 Node/Python，并强制 Python 以 UTF-8 运行。

安装前应卸载或禁用旧的独立 `openharness-report-loop` / `local-report-loop`。安装完成后，插件不应出现 `report_loop_start / submit / finish / status` 工具；`report_loop_run` 仅作兼容入口。可执行以下预检：

```bat
scripts\run-node.cmd scripts\verify-mcp-contract.mjs
```

Memory 开启后会自动注册 Reflection；也可手动执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-reflection-windows.ps1
```

如需关闭：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\disable-reflection-windows.ps1
```

## macOS 安装包

macOS 包使用 `sh + run-node.sh` 启动统一 MCP，宿主 Hook 通过 `run-python.sh` 启动 Report Loop Runner；优先复用 `~/.workbuddy/binaries` 下的运行时，也可使用 PATH 中满足版本要求的 Node/Python。默认提供 Apple Silicon 构建命令：

```bash
npm run build:release:macos
```

Memory 开启后会自动注册 Reflection；需要单独关闭 Reflection 时执行：

```bash
sh scripts/disable-reflection-macos.sh
```

Windows 与 macOS 均可用 `--no-archive` 只生成可检查的发布目录，不创建压缩包：

```bash
node scripts/build-release.mjs --target-platform win32 --target-arch x64 --no-archive
node scripts/build-release.mjs --target-platform darwin --target-arch arm64 --no-archive
```

## 开发验证

```bash
npm test
npm run syntaxcheck
npm run build:release
```

开发态使用 `--plugin-dir` 时，WorkBuddy 可能只加载 Skill/Agent 而不自动注册 MCP；Memory 工具的真实 E2E 可额外通过 `--mcp-config` 显式加载本目录 `.mcp.json`，或安装构建后的本地 marketplace 包。Report Loop 则在 Job 写入后由宿主 Hook 自动启动，不依赖会话 MCP 工具索引。
