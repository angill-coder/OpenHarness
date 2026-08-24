# Research Report Loop + Memory

可安装的 WorkBuddy 集成插件：宿主 Agent 负责报告写作，按 Rubric Dimension 隔离的 Judge 并行评测并驱动最多三版迭代；用户对终稿的明确写作反馈由 Memory Curator 保存为 L0/L1，并在证据充分时升级 Git-backed Rubric Set。

## 核心链路

1. `research-report-loop` Skill 确认需求并由宿主 Agent 写初稿。
2. L2B 通过 `criterionKey + add/extend/override/disable` 更新 `core / audience / project` Overlay，并形成新的 Rubric Set 版本。
3. `report_loop_start` 按 `Base → core → audience → project` 确定性解析并冻结，不生成 Audience×Project 组合文件。
4. `report_loop_submit` 默认发起六维并行 Judge；存在适用 Personal Rubrics 时增加第七维。宿主按 `revisionBrief` 改写，最多评测三个版本。
5. `report_loop_finish` 返回历史最佳已采纳报告。
6. 用户明确反馈后，Hook 要求委派 `research-report-memory-curator` Capture；Judge 反馈不会进入 Memory。

写作 Agent 不直接 Recall Memory。L2B 只通过 Judge 影响后续报告，避免个性化记忆污染写作上下文。

Criterion Slot、Scope Overlay、Personal 权重和版本冻结规则见 [Rubric Set 演进设计](docs/rubric-set-evolution.md)。

## 两个 MCP Server

- `research-report-loop`：Rubric 编译、Judge、版本采纳与停止。
- `research-report-memory-v2-0821`：L0/L1/L2B Capture、Review、Manage 与 Forget。

## 本地数据

- Report Loop：`~/.research-report-loop/runs/<run-id>/`
- Memory：`~/.research-report-memory-v2-0821/`
- Rubric Set Git Repository：`~/.research-report-memory-v2-0821/l2b-rubrics/personal/default/`
- 人类可读视图：上述目录的 `views/rubric-set.md`；Judge 以 JSON 和冻结文件为准。

集成插件复用现有 V2-0821 Memory 数据，不迁移或覆盖已有记忆。

## 开发验证

```bash
npm test
npm run syntaxcheck
npm run build:release
```

开发态使用 `--plugin-dir` 时，WorkBuddy 可能只加载 Skill/Hook 而不自动注册 MCP；真实 E2E 应额外通过 `--mcp-config` 显式加载本目录 `.mcp.json`，或安装构建后的本地 marketplace 包。
