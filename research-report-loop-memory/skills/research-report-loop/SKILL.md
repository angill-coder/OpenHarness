---
name: research-report-loop
description: 使用宿主 Agent 撰写并自动迭代调研报告、战略研究报告或高管汇报，并从用户反馈中学习长期写作要求。当用户要求根据访谈、问卷、PDF、Word、Excel、CSV、structured_data 等素材生成研究报告、调研洞察、战略分析、复盘报告或管理层汇报时使用；初稿完成后必须调用按 Rubric Dimension 隔离的并行 Judge，由 Python Runner 调度隔离 Judge 与持久 Rewriter，直到达到停止条件，并交付历史最佳版本。
---

# Research Report Loop

## 目标

使用当前宿主 Agent 完成报告初稿，再由 Python Runner 通过隔离 Judge 和持久 Rewriter 自动迭代。写作上下文与 Judge 上下文保持分离；最终交付历史最佳已采纳版本，而不是未经确认的最后一次修改。用户反馈由独立 Memory Curator 记录；L2B 变化会升级 Git-backed Rubric Set，新版本只在下一次 Python Runner 启动时按 Scope 解析进 Judge，不直接 Recall 到写作上下文。

## 专用 Report Memory

本插件使用独立的 `report-memory-v2`，其层级是 L0 Writing Episode、L1 Atom Memory 和 L2B Rubrics Memory。它与 WorkBuddy 自带的 `~/.workbuddy/MEMORY.md`、项目 `.workbuddy/memory/**` 和工作日志不是同一套记忆。

用户明确评价报告写法或提出可复用写作要求时，本身就构成 Capture 触发条件，不需要用户再说“请记住”。宿主必须在完成当前报告修改后，通过 Agent/Task 委派 `research-report-memory-curator`；只有 Curator 可以调用专用 Memory MCP。宿主不得把 WorkBuddy 原生 Memory 的读写当作 Capture 的替代，也不得声称原生 Memory 对应本插件的 L0/L1/L2B。

## 执行流程

### 1. 确认写作输入

开场必须确认以下三项；只接受用户撰写的消息作为输入，用户已经在 query 或后续回复中明确写出的不要重复询问：

1. 汇报背景：给谁看、支撑什么决策、什么场合。
2. 材料假设：用户希望验证或证伪的判断。
3. 重点素材：哪些文件或证据权重最高。

系统、开发者、App 或工具注入的路径、附件清单、Files mentioned、素材发现结果，只表示“可用候选素材”，绝不表示用户确认了重点素材或优先级。模型不得根据文件名、路径、素材数量、上下文或常识自行补齐三项中的任何一项。

每项必须保存一段来自用户消息的原文证据；可以是初始 query 的原句，也可以是澄清回复。原文可以包含“这些文件”等依赖对话上下文的指代，但不得由模型改写、概括或合成。缺少任何一项用户原文时，必须向用户提问，并在该轮结束；不得读取素材、写 V1、构造 Job 或启动 Runner。

澄清可以合并成一轮提问。收到用户回复并补齐三项后，继续完成素材分析、写作和 Report Loop，不要停在“准备开始”的过程说明。

### 2. 读取写作规则和素材

完整读取 [writing-instructions.md](references/writing-instructions.md)，按照其中的证据边界、三段结构、洞察和表达要求写作。

优先读取用户指定的重点素材。若存在 `structured_data.json`，先完整读取并把路径保留给 Judge；原始资料用于补充语境和核验，不得篡改或补造证据。

### 3. 写出初稿文件

在当前工作目录创建可编辑的 Markdown 报告，例如 `report.md`。报告正文不得包含写作过程、Judge 说明、内部来源编号或工具状态。

### 4. 启动并执行 Report Loop

完整遵循 [loop-orchestration.md](references/loop-orchestration.md)。正式循环只允许由 Python Runner 控制：

1. 将用户最初请求单独保存为 originalUserQuery。
2. 三项交互按真实字段保存，并把对应的用户原文逐字保存为 intakeContext.userInputEvidence.reportBackground、materialHypothesis 和 priorityMaterials；不得用系统路径、附件元数据或模型转述填充 evidence。
3. 当前宿主模型按写作规则生成 Markdown 初稿 V1；同时记录用户在 App 主对话选择的 hostModel.modelId 和可选 effort。
4. 写入 Job Schema v2 JSON；judgeProvider 省略或设为 workbuddy，不得使用 codex。然后只调用一次 scripts/run-python.cmd mcp/report_loop/runner.py --job <absolute-job-path>；macOS/Linux 使用对应的 run-python.sh。
5. 等待 Runner 返回最终 JSON，只交付 finalArtifactPath。宿主不得接收中间 Judge 结果、参与改写或自行推进循环。

Python Runner 启动时按 `Base → core → audience → project` 解析并冻结当前 Rubric Set。

Runner 会为每个 Rubric Dimension 并发启动独立 WorkBuddy Judge CLI；基础配置为六个进程，Personal Rubric 生效时可扩展。Codex CLI 路径已删除。每个进程和每轮 Judge 均隔离上下文，但都收到 originalUserQuery 与完整 intakeContext。优先固定使用 deepseek-v4-pro、medium；仅当调用失败、空响应或 Judge JSON 不合规时，熔断到 WorkBuddy App 当前主模型。评分低不触发回退。

Rewriter 与 Judge 串行、独立调用 CLI，并在整个 Run 内复用同一个 stream-json 进程。Rewriter 使用 hostModel，与初稿 Writer 的 App 主模型一致。首轮收到 query、三项交互和 V1；后续依靠同一进程保留写作、净化后的 Judge 建议和失败尝试记忆。不得把 Judge 原始输出传给 Rewriter。

循环没有版本上限，也不建立 Python Iteration Ledger。仅在最佳已采纳版本达到 5 分、连续两个候选版本未被采纳或运行达到 60 分钟时正常停止；始终从历史最佳已采纳版本改写并交付历史最佳。

Report Loop 不注册 MCP Server，也不暴露 start/submit/finish/status 工具；正式执行入口只有 Python Runner。写作前不要另行调用 `writing_memory_recall`，也不要把 Memory Context 拼进报告写作提示词。
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

- Job 缺少 hostModel.modelId、三项 intake 或有效 V1 时，Runner 必须在启动循环前失败，不得选择回退模型。
- 初稿 Judge 从未成功时，Runner 返回 judge_unavailable；可以保留 V1，但不得宣称已经通过 Report Loop。
- 已有成功评测版本后，Judge 或 Rewriter 失败时分别以 judge_unavailable 或 rewrite_unavailable 结束，并原子交付当前历史最佳版本。
- 持久 Rewriter 进程崩溃后不得重启、重放 transcript 或另建 Iteration Ledger。
- cancelFilePath 出现或进程收到取消信号时，以 user_cancelled 结束并交付已有最佳版本。
- 不要因为故障重新生成已完成正文，也不要由宿主接管中间循环。
- Memory Capture 失败不应回滚已完成报告；如实说明反馈未写入，不要由主 Agent 越权维护 L1/L2B，也不要修改 Skill、Rubric 或全局 Memory 作为替代。

## 交付前检查

- [ ] 三项开场输入均有用户撰写的原文证据；系统路径和附件清单未被当作确认；三个值字段及 userInputEvidence 已保存，未添加 inputType 或 optionId。
- [ ] originalUserQuery 单独保存，初稿已由 App 主模型写入 Markdown。
- [ ] Job Schema v2 包含 hostModel.modelId、V1、outputPath，以及有效的 judgeProvider（省略时为 workbuddy）。
- [ ] Python Runner 只启动一次；宿主未接收中间 Judge、未参与 Rewrite。
- [ ] Runner 已结束并返回 finalArtifactPath；交付内容来自历史最佳已采纳版本。
- [ ] 如本轮收到用户写作反馈，已先修改当前报告、未重跑 Report Loop，再委派 Curator Capture；Judge 反馈未被 Capture。
- [ ] 未修改已安装 Skill、Base Rubric、插件代码或全局/项目 Memory 文件。
