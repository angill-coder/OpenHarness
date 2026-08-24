# Research Report Loop + Memory

可安装的 WorkBuddy 集成插件：宿主 Agent 负责报告写作，按 Rubric Dimension 隔离的 Judge 并行评测并驱动最多三版迭代；用户对终稿的明确写作反馈由 Memory Curator 保存为 L0/L1，并在证据充分时升级 Git-backed Rubric Set。

## 核心链路

1. `research-report-loop` Skill 确认需求并由宿主 Agent 写初稿。
2. L2B 通过 `criterionKey + add/extend/override/disable` 更新 `core / audience / project` Overlay，并形成新的 Rubric Set 版本。
3. `report_loop_start` 按 `Base → core → audience → project` 确定性解析并冻结，不生成 Audience×Project 组合文件。
4. `report_loop_submit` 默认发起六维并行 Judge；存在适用 Personal Rubrics 时增加第七维。宿主按 `revisionBrief` 改写，最多评测三个版本。
5. `report_loop_finish` 返回历史最佳已采纳报告。
6. 用户明确反馈后，Skill 指引宿主先修改当前报告，再委派 `research-report-memory-curator` Capture；Judge 反馈不会进入 Memory。

写作 Agent 不直接 Recall Memory。L2B 只通过 Judge 影响后续报告，避免个性化记忆污染写作上下文。

## 精简的 Memory 调度

插件恢复独立 Memory 插件的两条调度链：

- **实时 Capture**：Capture-only Hook 识别已交付报告后的明确写作反馈，提醒宿主先修改报告，再通过 Agent/Task 委派 `research-report-memory-curator` 调用专用 MCP。Stop 最多检查一次是否遗漏 Capture；明确成功或失败后放行。
- **定时 Dreaming**：macOS LaunchAgent 每天 16:30 启动一次隔离的 WorkBuddy CLI Curator，复审待处理 L0/L1 和疑似冲突，并只提交 L2B 增量修改。

Hook 不注册 `PreToolUse`，不负责写前 Recall、报告文件校验或 Report Loop 状态管理，也不要求主 Agent 直接调用 MCP。Capture 明确失败不会回滚已完成报告。

## Judge Provider

- 本地实验 `npm run build:local` 默认使用 `Codex CLI + gpt-5.6-sol + medium`。
- 正式发布 `npm run build:release` 默认使用 `Codex CLI + gpt-5.6-sol + medium`；WorkBuddy CLI 仅通过 `npm run build:release:workbuddy` 显式构建。
- 两种入口使用同一套六维并行 Judge、JSON 校验和版本采纳逻辑，只隔离模型调用层。
- 可通过 `RESEARCH_REPORT_LOOP_JUDGE_PROVIDER=codex|workbuddy` 切换，并用
  `RESEARCH_REPORT_LOOP_CODEX_MODEL` / `RESEARCH_REPORT_LOOP_WB_MODEL`、
  `RESEARCH_REPORT_LOOP_JUDGE_EFFORT` 覆盖模型和推理力度；兼容旧的通用变量
  `RESEARCH_REPORT_LOOP_JUDGE_MODEL`。
- Codex CLI 默认从 `PATH` 发现；macOS 也会检测 ChatGPT App 内置 CLI。自定义路径使用
  `RESEARCH_REPORT_LOOP_CODEX_CLI_PATH`。

本地 Codex 实验包与正式 WorkBuddy 发布包分别构建，显式 Provider 产物目录带后缀，互不覆盖：

```bash
npm run build:local              # 本地实验：Codex CLI / gpt-5.6-sol / medium
npm run build:release            # 正式发布：Codex CLI / gpt-5.6-sol / medium
npm run build:release:workbuddy  # 可选：WorkBuddy CLI / deepseek-v4-flash-ioa / medium
```

每个维度的 Judge 都会收到用户本轮完整任务。若用户明确要求与非证据类 Rubric 直接冲突，Judge 将该检查记为 `met` 并以 `user_override:` 说明，不再用基础 Rubric 反向纠正用户；事实准确性、证据可追溯和数据一致性等底线不能被覆盖。该机制只改变现有 Judge Prompt 和 Rewrite Brief，不增加模型调用。

Criterion Slot、Scope Overlay、Personal 权重和版本冻结规则见 [Rubric Set 演进设计](docs/rubric-set-evolution.md)。

## 两个 MCP Server

- `research-report-loop`：Rubric 编译、Judge、版本采纳与停止。
- `research-report-memory-v2-0821`：L0/L1/L2B Capture、Review、Manage 与 Forget。

## Curator Prompt 双版本

- **V1 Gate-first（默认）**：`agents/research-report-memory-curator.md`。以显式 Layer Gate、Scope 和 Payload 自检为主，适合作为稳定对照。
- **V2 Letta-first（实验）**：`prompts/research-report-memory-curator-v2-letta-first.md`。以未来行为、完整历史、渐进更新和高层记忆成本为判断主体，同时保留相同的 Scope、Rubric Set、Criterion Slot 与 MCP 契约。

两个版本不会同时暴露为两个同名 Agent。构建时只把选定 Prompt 写入安装包的 `agents/research-report-memory-curator.md`：

```bash
npm run build:release:prompt-v1
npm run build:release:prompt-v2
```

默认 `npm run build:release` 仍使用 V1，不会改变当前 WorkBuddy 行为。V1/V2 使用相同插件 ID，A/B 测试时应分轮安装，并使用相同的初始 Memory 快照。

## 本地数据

- Report Loop：`~/.research-report-loop/runs/<run-id>/`
- Memory：`~/.research-report-memory-v2-0821/`
- Rubric Set Git Repository：`~/.research-report-memory-v2-0821/l2b-rubrics/personal/default/`
- 人类可读视图：上述目录的 `views/rubric-set.md`；Judge 以 JSON 和冻结文件为准。

集成插件复用现有 V2-0821 Memory 数据，不迁移或覆盖已有记忆。

旧版本若已经存在 Audience/Project L2B 文件，可先检查 Scope 路径迁移；默认仅 dry-run，确认无冲突后再执行 `--apply`：

```bash
node scripts/migrate-rubric-scope-paths.mjs
node scripts/migrate-rubric-scope-paths.mjs --apply
```

安装包中的 `scripts/install-maintenance-macos.sh` 用于注册每天 16:30 的 Dreaming 任务；任务只连接本插件的 Memory MCP，不启动 Report Loop Judge。

## 开发验证

```bash
npm test
npm run syntaxcheck
npm run build:release
```

开发态使用 `--plugin-dir` 时，WorkBuddy 可能只加载 Skill/Agent 而不自动注册 MCP；真实 E2E 应额外通过 `--mcp-config` 显式加载本目录 `.mcp.json`，或安装构建后的本地 marketplace 包。
