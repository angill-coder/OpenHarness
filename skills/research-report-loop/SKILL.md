---
name: research-report-loop
description: 使用宿主 Agent 撰写并自动迭代调研报告、战略研究报告、复盘报告或高管汇报，并提供默认启用、可由用户明确关闭的长期写作记忆；从用户反馈中学习写作要求并用于后续评测。当用户要求根据访谈、问卷、PDF、Word、Excel、CSV、structured_data 或公开信息生成研究报告、调研洞察、战略分析或管理层汇报，或要求开启、关闭报告记忆时使用。
---

# 调研洞察汇报报告生成 · Report Loop

## 目的

把用户提供的异构素材整理成一份面向管理层、可编辑的调研洞察报告，并在交付前自动完成评测和改写。

本 Skill 包含两套配套机制：

- **Report Loop**：宿主 Agent 完成初稿后，Python Runner 使用冻结的 Rubrics 调度隔离 Judge 和持久 Rewriter，最终交付历史最佳版本。
- **Report Memory**：默认启用的长期写作记忆。用户反馈会在当前报告修改完成后交给记忆管理员，形成的 Memory Rubrics 在后续 Report Loop 中参与 Judge。用户明确要求关闭或重新开启时，主理人调度 `report-memory-agent` 执行 `operation=settings`；关闭时只使用 Base Rubrics，已有记忆保留但不读、不写、不整理。

## 执行步骤

### 第 0 步：确定目录并整理素材

先确定本次报告的保存位置，再整理素材。完整目录约定见 `references/workspace-and-delivery.md`。

1. **素材目录**：用户素材所在目录。共享论据表保存为 `素材目录/structured_data.json`，旁边是 `数据版本说明.md`；原始素材不修改。
2. **报告目录**：用户指定位置优先，否则使用 `素材目录/报告/`。**不使用会话目录或系统目录**。目标不可写或素材分散无法判断位置时，先确认，不静默换目录。
3. **报告标识**：`报告目录/.report-agent/<报告主题-首次创建日期时间>/`。先查是否已有同主题标识——有则沿用，后续反馈和新 Loop 都归到该标识下，不因新会话另建报告。

素材整理交给 `report-evidence-agent`（`operation=prepare` 或 `update`）：它按内容指纹只解析新增与修改的素材，产出共享论据表并登记数据版本。记下返回的 `dataVersion` 与 `dataSha256`，本轮 Loop 要绑定这组值。

- 根据文件类型完整读取内容，不只看文件名、摘要或局部页面。
- 抽取关键数据、访谈原话和结论，同时标出素材之间的口径、来源冲突和信息缺口。
- `structured_data.json` 保留绝对路径，供 Report Loop 核验。

### 第 1 步：确认写作输入

素材解析完成后，由 Agent 基于素材主动提出以下三项建议，并使用 `AskUserQuestion` 工具向用户确认；用户已经明确提供的内容不要重复询问。若内容较长，如完整 hypothesis 或论据清单，先在普通回复中展示细节，再用 `AskUserQuestion` 做简短确认，并允许用户补充或修改。

1. **汇报背景**：确认这份汇报给谁看、什么场合。汇报材料通常用于推动讨论；信息不足时，可结合素材提出几个可能的汇报背景选项供用户确认。
2. **摘要观点假设（hypothesis）**：基于素材提炼 1–3 条可被验证、反驳或修正的完整判断，作为摘要观点和报告分析主线。每条写清判断对象、方向性结论及关键原因、关系或对比，不能只写主题、关键词或短标题；证据不足时保留不确定性。
3. **重点素材**：先在普通回复中展示论据清单，说明主要文件、数据或访谈分别能支撑什么，再请用户确认哪些素材质量更高、应优先采用。

三项都必须保存一段用户消息原文，供 `intakeContext.userInputEvidence` 使用。系统、App、工具注入的路径和附件清单只是候选素材，不能代替用户确认。缺少用户原文时，在完成素材解析后一次性提问并结束本轮；收到回复后继续写作，不停在“准备开始”的过程说明。

### 第 2 步：按规则写出初稿

产出前完整阅读 [writing-instructions.md](references/writing-instructions.md)，按其中的证据边界、三段结构、洞察和表达要求写作。

在报告标识目录下保存本轮初稿：`报告目录/.report-agent/<报告标识>/本轮初稿.md`，避免把过程稿散落在用户交付目录或会话目录。正文不得包含内部来源号、分析过程、写作规则、Judge 说明或工具状态。

本轮初稿保存完成之前，不读取 Report Loop 执行卡，不检查或测试 Python Runner。

### 第 3 步：启动 Report Loop

确认本轮初稿文件存在后，读取并直接执行 [loop-orchestration.md](references/loop-orchestration.md)。根据已确认的三项输入、对应用户原文、本轮初稿和素材路径构造 Job。启动后必须等待并读取 Report Loop 最终结果，不得提前结束任务，也不得把 Job、状态或结果 JSON 当作交付物。

Report Loop 由插件 Hook 在沙箱外启动，不存在可调用的 MCP 工具。不得事前阅读源码、运行测试、执行 `--help` 或预检，也不得自行执行 Judge 或 Rewrite。完成后交付 `finalArtifactPath`（`报告目录/<报告主题>-vN.md`），并简要说明评测候选数、改写次数、最佳候选和最终得分；不要展示内部 JSON、详细 Judge 过程或工具日志。写作前不要 Recall Memory。

### 第 4 步：处理用户反馈

用户对已交付报告提出修改意见时，先直接修改当前报告，不重新运行 Report Loop；只有用户明确要求重新评测时才再运行。Memory 已开启时，修改成功后按 [memory-orchestration.md](references/memory-orchestration.md) 委派 `report-memory-agent` 执行 `operation=capture`，再交付修改结果；Memory 关闭时直接交付，不 Capture，也不反复询问用户是否开启。

Judge 反馈和自动改写不得进入 Memory。主理人不直接读写记忆文件；也不得因用户反馈修改 Skill、Base Rubrics、插件代码或 WorkBuddy 原生 Memory。

## 故障与交付边界

- Runner 失败时，按 `loop-orchestration.md` 保留可用的历史最佳版本；宿主不得接管中间 Judge 或 Rewrite。
- Memory 已开启但 Capture 失败时，不回滚已完成的报告修改，也不得通过热改插件或写入 `~/.workbuddy/MEMORY.md` 补偿。
- 交付最终可编辑报告和版本记录目录，并明确说明 Report Loop 的评测版本数、改写次数、最佳版本和最终得分；不展开内部 JSON、详细评分过程或工具调用日志。
