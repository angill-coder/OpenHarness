# Research Report Loop + Memory

可安装的 WorkBuddy 集成插件：宿主 Agent 负责初稿写作，Python Runner 调度按 Rubric Dimension 隔离的 Judge 与持久 Rewriter 自动迭代；用户对终稿的明确写作反馈由 Memory Curator 保存为 L0/L1，并在证据充分时升级 Git-backed Rubric Set。

## 核心链路

1. `research-report-loop` Skill 确认需求并由宿主 Agent 写初稿。
2. L2B 通过 `criterionKey + add/extend/override/disable` 更新 `core / audience / project` Overlay，并形成新的 Rubric Set 版本。
3. 宿主写入 Job Schema v2，并一次性启动 Python Runner。Runner 按 `Base → core → audience → project` 解析并冻结 Rubric。
4. Runner 默认并发六个隔离 Judge；存在适用 Personal Rubrics 时增加第七维。Judge Provider 默认是 WorkBuddy `deepseek-v4-pro / medium`，也可选 Codex `gpt-5.6-sol / medium`；调用失败、空响应或 Judge JSON 不合规时，自动切换到 WorkBuddy App 当前主模型。
5. Runner 使用 App 主模型启动一个持久 Rewriter CLI，串行执行 Rewrite → Judge，直到 5 分、连续两轮未采纳或 60 分钟，并返回历史最佳报告。
6. 用户明确反馈后，Skill 指引宿主先修改当前报告，再委派 `research-report-memory-curator` Capture；Judge 反馈不会进入 Memory。

写作 Agent 不直接 Recall Memory。L2B 只通过 Judge 影响后续报告，避免个性化记忆污染写作上下文。

## 精简的 Memory 调度

插件恢复独立 Memory 插件的两条调度链：

- **实时 Capture**：Capture-only Hook 识别已交付报告后的明确写作反馈，提醒宿主先修改报告，再通过 Agent/Task 委派 `research-report-memory-curator` 调用专用 MCP。Stop 最多检查一次是否遗漏 Capture；明确成功或失败后放行。
- **定时 Reflection**：macOS LaunchAgent 或 Windows Task Scheduler 每天 16:30 启动一次隔离的 WorkBuddy CLI Reflection Agent，复审待处理 L0/L1 和疑似冲突，并只提交 L2B 增量修改。

Hook 不注册 `PreToolUse`，不负责写前 Recall、报告文件校验或 Report Loop 状态管理，也不要求主 Agent 直接调用 MCP。Capture 明确失败不会回滚已完成报告。

## Report Loop 模型与隔离

- Writer 与 Rewriter 使用 WorkBuddy App 主对话中用户选择的模型；Job 必须提供 `hostModel.modelId`，不设置回退模型。
- Judge Provider 默认 `workbuddy`，固定为 `deepseek-v4-pro / medium`；也可在 Job 中明确选择 `codex`，固定为 `gpt-5.6-sol / medium`。Judge Prompt 均通过 stdin 传入。只有调用失败、空响应或输出不满足 Judge JSON 合约时，才熔断到 WorkBuddy App 当前主模型，评分低不会触发回退。
- 三项开场输入必须附带用户消息原文；系统/App 注入的路径和附件清单只算候选素材，不能确认重点素材或替代用户回答。
- 每个 Rubric Dimension 和每轮 Judge 使用独立 CLI 进程与上下文；query 和三项 intake 会传给每个 Judge。
- Rewriter 与 Judge 隔离，整个 Run 只保留一个 Rewriter stream-json 进程；它只接收净化后的 revision brief，不接收 Judge 原始输出。
## MCP 与 Runner 边界

- Report Loop 不注册 MCP Server；`mcp/report_loop/runner.py` 是唯一循环入口，负责 Rubric 编译、Judge、Rewrite、版本采纳与停止。
- 仅 `report-memory-v2` 注册为 MCP Server，用于 L0/L1/L2B Capture、Review、Manage 与 Forget。

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

安装包中的 `scripts/install-reflection-macos.sh` 或 `scripts/install-reflection-windows.ps1` 用于注册每天 16:30 的 Reflection 任务；任务通过稳定启动器自动解析当前已安装的插件版本，只连接本插件的 Memory MCP，不启动 Report Loop Judge。

## Windows x64 安装包

Windows 包使用 `cmd.exe + run-node.cmd` 启动 Memory MCP，并使用 `run-python.cmd` 启动 Report Loop Runner；不依赖 Git Bash、WSL 或系统 `sh`。它优先复用 WorkBuddy 自带的 Node/Python，并强制 Python 以 UTF-8 运行。

安装前应卸载或禁用旧的独立 `openharness-report-loop` / `local-report-loop`。安装完成后，插件不应出现 `report_loop_start / submit / finish / status` 工具，只应自动注册 Memory MCP。可执行以下预检：

```bat
scripts\run-node.cmd scripts\verify-mcp-contract.mjs
```

如需启用每天 16:30 的 Memory Reflection，可执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install-reflection-windows.ps1
```

## macOS 安装包

macOS 包使用 `sh + run-node.sh / run-python.sh` 启动 Memory MCP 和 Report Loop Runner；优先复用 `~/.workbuddy/binaries` 下的运行时，也可使用 PATH 中满足版本要求的 Node/Python。默认提供 Apple Silicon 构建命令：

```bash
npm run build:release:macos
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

开发态使用 `--plugin-dir` 时，WorkBuddy 可能只加载 Skill/Agent 而不自动注册 Memory MCP；真实 E2E 可额外通过 `--mcp-config` 显式加载本目录 `.mcp.json`，或安装构建后的本地 marketplace 包。Report Loop 始终由 Skill 一次性启动 Python Runner。
