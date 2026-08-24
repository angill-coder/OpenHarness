---
name: research-report-loop
description: 使用宿主 Agent 撰写并自动迭代调研报告、战略研究报告或高管汇报，并从用户反馈中学习长期写作要求。当用户要求根据访谈、问卷、PDF、Word、Excel、CSV、structured_data 等素材生成研究报告、调研洞察、战略分析、复盘报告或管理层汇报时使用；初稿完成后必须调用按 Rubric Dimension 隔离的并行 Judge，最多评测三个版本，并交付历史最佳版本。
---

# Research Report Loop

## 目标

使用当前宿主 Agent 完成报告写作，再通过隔离 Judge 评测和迭代。写作上下文与 Judge 上下文保持分离；最终交付历史最佳已采纳版本，而不是未经确认的最后一次修改。用户反馈由独立 Memory Curator 记录；L2B 变化会升级 Git-backed Rubric Set，新版本只在下一次 `report_loop_start` 时按 Scope 解析进 Judge，不直接 Recall 到写作上下文。

## 专用 Report Memory

本插件使用独立的 `research-report-memory-v2-0821`，其层级是 L0 Writing Episode、L1 Atom Memory 和 L2B Rubrics Memory。它与 WorkBuddy 自带的 `~/.workbuddy/MEMORY.md`、项目 `.workbuddy/memory/**` 和工作日志不是同一套记忆。

用户明确评价报告写法或提出可复用写作要求时，本身就构成 Capture 触发条件，不需要用户再说“请记住”。宿主必须在完成当前报告修改后，通过 Agent/Task 委派 `research-report-memory-curator`；只有 Curator 可以调用专用 Memory MCP。宿主不得把 WorkBuddy 原生 Memory 的读写当作 Capture 的替代，也不得声称原生 Memory 对应本插件的 L0/L1/L2B。

## 执行流程

### 1. 确认写作输入

开场先确认以下三项；用户已提供的不要重复询问，只补缺项：

1. 汇报背景：给谁看、支撑什么决策、什么场合。
2. 材料假设：用户希望验证或证伪的判断。
3. 重点素材：哪些文件或证据权重最高。

澄清轮可以正常结束。三项齐全后，继续完成素材分析、写作和 Report Loop，不要停在“准备开始”的过程说明。

### 2. 读取写作规则和素材

完整读取 [writing-instructions.md](references/writing-instructions.md)，按照其中的证据边界、三段结构、洞察和表达要求写作。

优先读取用户指定的重点素材。若存在 `structured_data.json`，先完整读取并把路径保留给 Judge；原始资料用于补充语境和核验，不得篡改或补造证据。

### 3. 写出初稿文件

在当前工作目录创建可编辑的 Markdown 报告，例如 `report.md`。报告正文不得包含写作过程、Judge 说明、内部来源编号或工具状态。

### 4. 启动并执行 Report Loop

完整遵循 [loop-orchestration.md](references/loop-orchestration.md)：

1. 调用 `mcp__research-report-loop__report_loop_start`，记录返回的 `runId`。
2. 调用 `mcp__research-report-loop__report_loop_submit` 提交初稿。
3. 返回 `nextAction=revise` 时，以 `bestArtifactPath` 为唯一基线，根据 `revisionBrief` 做最小范围修改，再提交下一版。
4. 返回 `nextAction=deliver` 时，调用 `mcp__research-report-loop__report_loop_finish`。
5. 只交付 `report_loop_finish` 返回的 `bestArtifactPath`。

不要自行计算分数、伪造 Judge 结果或跳过 MCP。Report Loop 默认使用基础六维；存在适用的 Personal Rubrics 时动态增加第七维。目标固定为 5.0，最多评测三个版本。

`report_loop_start` 会读取当前 Rubric Set，按 `Base → core → audience → project` 解析 Criterion Overlay 并冻结。写作前不要另行调用 `writing_memory_recall`，也不要把 Memory Context 拼进报告写作提示词。

### 5. 交付

向用户交付最终 Markdown 文件并简要说明报告已完成。除非用户主动询问，不展开内部评分、版本试错和工具调用过程。

### 6. 用户反馈后的 Memory Capture

完整遵循 [memory-orchestration.md](references/memory-orchestration.md)：

1. 用户对已交付报告提出修改意见时，先直接修改当前报告文件；这是一次反馈修订，不重新启动或提交 Report Loop。
2. 确认报告文件修改成功后，再通过 Agent/Task 委派 `research-report-memory-curator` 执行 `operation=capture`。主 Agent 不得直接调用 Memory MCP。
3. 收到 `MEMORY_CAPTURE_COMPLETED` 或明确失败后，交付本轮修改结果并结束。

用户只表达长期写作要求、明确不要求修改当前报告时，可以跳过文件修订，但仍必须委派 Curator Capture。只有用户明确要求“重新评测”或“再跑一轮 Judge”时，反馈修订后才运行新的 Report Loop。

Judge 的分数、反馈和自动改写不属于用户偏好，绝不能触发 Capture。只有用户反馈和用户实际编辑进入 Memory 学习链。

### 7. 插件资产边界

报告写作和反馈修订期间，只能修改当前报告及用户明确指定的交付文件。以下内容是已安装插件的系统资产，不是用户报告，不得因写作反馈而修改：

- `skills/**`、`SKILL.md` 和写作说明文件；
- `rubrics/**` 中的 Base Rubric；
- `dist/**`、`hooks/**`、`mcp/**`、插件 Manifest、脚本和 README；
- `~/.workbuddy/MEMORY.md` 或项目 `.workbuddy/memory/**`。

用户说“以后都这样写”仍然只进入 Memory Capture，不代表授权修改 Skill 或 Base Rubric。MCP 或插件故障时，不得自行修补安装目录、改写全局记忆或重启 Connector；保留已经完成的报告修改，如实说明 Capture 失败。只有用户明确提出开发或调试插件时，才在插件源码仓库处理，并通过新版本发布，不直接热改安装副本。

## 故障处理

- Judge 工具失败时只重试一次同一提交，不要通过反复启动新 Run 绕过故障。
- 已有成功评测版本但后续 Judge 不可用时，调用 `report_loop_finish(reason="judge_unavailable")`，交付当前最佳版本并如实说明质量检查未完整结束。
- 初稿从未成功完成 Judge 时，可以保留报告文件并说明 Judge 未完成；不要宣称已经通过 Report Loop。
- 不要因为故障重新生成已经完成的正文，也不要无限调用状态工具。
- Memory Capture 失败不应回滚已完成报告；如实说明反馈未写入，不要由主 Agent 越权维护 L1/L2B，也不要修改 Skill、Rubric 或全局 Memory 作为替代。

## 交付前检查

- [ ] 三项开场输入已确认。
- [ ] 初稿已写入 Markdown 文件。
- [ ] `report_loop_start` 只调用一次并保存 `runId`。
- [ ] 每个版本只提交一次；失败重试不创建新 Run。
- [ ] 修改基于 `bestArtifactPath`，没有从 rejected 版本继续写。
- [ ] 已调用 `report_loop_finish`，交付路径与 `bestArtifactPath` 一致。
- [ ] 如本轮收到用户写作反馈，已先修改当前报告、未重跑 Report Loop，再委派 Curator Capture；Judge 反馈未被 Capture。
- [ ] 未修改已安装 Skill、Base Rubric、插件代码或全局/项目 Memory 文件。
