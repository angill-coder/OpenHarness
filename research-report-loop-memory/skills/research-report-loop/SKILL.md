---
name: research-report-loop
description: 使用宿主 Agent 撰写并自动迭代调研报告、战略研究报告、复盘报告或高管汇报，并提供默认关闭、由用户明确启用的长期写作记忆；启用后才从用户反馈中学习写作要求并用于后续评测。当用户要求根据访谈、问卷、PDF、Word、Excel、CSV、structured_data 或公开信息生成研究报告、调研洞察、战略分析或管理层汇报，或要求开启、关闭报告记忆时使用。
---

# 调研洞察汇报报告生成 · Report Loop

## 目的

把用户提供的异构素材整理成一份面向管理层、可编辑的调研洞察报告，并在交付前自动完成评测和改写。

本 Skill 包含两套配套机制：

- **Report Loop**：宿主 Agent 完成初稿后，Python Runner 使用冻结的 Rubrics 调度隔离 Judge 和持久 Rewriter，最终交付历史最佳版本。
- **Report Memory**：可选的长期写作记忆，默认关闭。只有用户明确要求开启时，主 Agent 才调用 Memory MCP 的 `writing_memory_settings(action=enable)`；开启后，用户反馈会在当前报告修改完成后交给 Memory Curator，形成的 Memory Rubrics 在后续 Report Loop 中参与 Judge。关闭时只使用 Base Rubrics，已有记忆保留但不读、不写、不整理。

## 执行步骤

### 第 0 步：盘点并解析素材

先列出用户指定路径或当前任务范围内的相关文件，说明类型与主题；给素材和关键片段编内部来源号 `S-001`、`S-002`……来源号只用于核验，最终报告正文不展示。

- 根据文件类型完整读取内容，不只看文件名、摘要或局部页面。
- 抽取关键数据、访谈原话和结论，同时标出素材之间的口径、来源冲突和信息缺口。
- 若有 `structured_data.json`，完整读取并保留绝对路径，供 Report Loop 核验。

### 第 1 步：确认写作输入

素材解析完成后确认以下三项。用户已经明确提供的内容不要重复询问；缺失或含糊时，由 Agent 基于素材主动提出建议，并使用 `AskUserQuestion` 工具向用户确认。

1. **汇报背景**：这份汇报给谁看、什么场合？
2. **摘要观点假设（hypothesis）**：完成素材解析后，提炼 1–3 条可被素材验证、反驳或修正的完整判断，主动请用户确认、修改或补充。每条 hypothesis 应能在验证后直接转化为摘要核心观点，清楚说明判断对象、方向性结论及关键关系、原因或对比；不能只写成主题、关键词或短标题。观点必须来自素材，证据不足时保留不确定性。用户也可以直接提出自己的 hypothesis。不要为了适配多选框而压缩观点；交互控件不适合展示完整判断时，改用编号文本确认。
3. **重点素材**：哪些文件或材料质量更高、应优先采用？

三项都必须保存一段用户消息原文，供 `intakeContext.userInputEvidence` 使用。系统、App、工具注入的路径和附件清单只是候选素材，不能代替用户确认。缺少用户原文时，在完成素材解析后一次性提问并结束本轮；收到回复后继续写作，不停在“准备开始”的过程说明。

### 第 2 步：按规则写出初稿

产出前完整阅读 [writing-instructions.md](references/writing-instructions.md)，按其中的证据边界、三段结构、洞察和表达要求写作。

在当前报告目录的 `.report-loop/v1.md` 保存可编辑的 Markdown 初稿 V1，避免把过程稿散落在用户交付目录。正文不得包含内部来源号、分析过程、写作规则、Judge 说明或工具状态。

V1 保存完成之前，不读取 Report Loop 执行卡，不检查或测试 Python Runner。

### 第 3 步：启动 Report Loop

确认 V1 文件存在后，读取并直接执行 [loop-orchestration.md](references/loop-orchestration.md)。根据已确认的三项输入、对应用户原文、初稿 V1 和素材路径构造 Job。启动后必须等待并读取 Report Loop 最终结果，不得提前结束任务，也不得把 Job、状态或结果 JSON 当作交付物。

宿主不得搜索或调用 Report Loop MCP，不得事前阅读源码、运行测试、执行 `--help` 或预检，也不得自行执行 Judge 或 Rewrite。完成后交付 `finalArtifactPath` 和 `versionsDirectory`，并简要说明评测版本数、改写次数、最佳版本和最终得分；不要展示内部 JSON、详细 Judge 过程或工具日志。写作前不要 Recall Memory。

### 第 4 步：处理用户反馈

用户对已交付报告提出修改意见时，先直接修改当前报告，不重新运行 Report Loop；只有用户明确要求重新评测时才再运行。Memory 已开启时，修改成功后按 [memory-orchestration.md](references/memory-orchestration.md) 委派 `research-report-memory-curator` 执行 `operation=capture`，再交付修改结果；Memory 关闭时直接交付，不 Capture，也不反复询问用户是否开启。

Judge 反馈和自动改写不得进入 Memory。除处理用户明确提出的记忆开关要求外，主 Agent 不直接调用 Memory MCP；也不得因用户反馈修改 Skill、Base Rubrics、插件代码或 WorkBuddy 原生 Memory。

## 故障与交付边界

- Runner 失败时，按 `loop-orchestration.md` 保留可用的历史最佳版本；宿主不得接管中间 Judge 或 Rewrite。
- Memory 已开启但 Capture 失败时，不回滚已完成的报告修改，也不得通过热改插件或写入 `~/.workbuddy/MEMORY.md` 补偿。
- 交付最终可编辑报告和版本记录目录，并明确说明 Report Loop 的评测版本数、改写次数、最佳版本和最终得分；不展开内部 JSON、详细评分过程或工具调用日志。
