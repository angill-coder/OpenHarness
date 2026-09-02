---
name: research-report-loop-v2
description: 使用宿主 Agent 撰写并通过 WorkBuddy 原生 Sub-agent 自动迭代调研报告、战略研究报告、复盘报告或高管汇报，并提供默认启用、可由用户明确关闭的长期写作记忆；从用户反馈中学习写作要求并用于后续动态评测。当用户要求根据访谈、问卷、PDF、Word、Excel、CSV、structured_data 或公开信息生成研究报告、调研洞察、战略分析或管理层汇报，或要求开启、关闭报告记忆时使用。
---

# 调研洞察汇报报告生成 · Report Loop V2

## 目的

把用户提供的异构素材整理成一份面向管理层、可编辑的调研洞察报告，并在交付前自动完成评测和改写。

本 Skill 包含两套配套机制：

- **Report Loop**：宿主 Agent 完成初稿 V0；Resolution Judge 根据 Base Rubrics 与 Memory Rubrics 冻结本轮动态评测维度，再由隔离的 Dimension Judge 和 Rewriter 完成评测迭代，最终交付历史最佳版本。
- **Report Memory**：默认启用的长期写作记忆。用户反馈会在当前报告修改完成后交给 Memory Agent，形成的 Memory Rubrics 在后续 Report Loop 中参与 Resolution。用户明确要求关闭或重新开启时，由 Memory Agent 更新设置；关闭时只使用 Base Rubrics，已有记忆保留但不读、不写、不整理。

## 执行步骤

### 第 0 步：盘点并解析素材

先列出用户指定路径或当前任务范围内的相关文件，说明类型与主题；给素材和关键片段编内部来源号 `S-001`、`S-002`……来源号只用于核验，最终报告正文不展示。

- 根据文件类型完整读取内容，不只看文件名、摘要或局部页面。
- 抽取关键数据、访谈原话和结论，同时标出素材之间的口径、来源冲突和信息缺口。
- 若有 `structured_data.json`，完整读取并保留绝对路径，供 Report Loop 核验。

### 第 1 步：确认写作输入

素材解析完成后，由 Agent 基于素材主动提出以下三项建议，并使用 `AskUserQuestion` 工具向用户确认；用户已经明确提供的内容不要重复询问。若内容较长，如完整 hypothesis 或论据清单，先在普通回复中展示细节，再用 `AskUserQuestion` 做简短确认，并允许用户补充或修改。

1. **汇报背景**：确认这份汇报给谁看、什么场合。汇报材料通常用于推动讨论；信息不足时，可结合素材提出几个可能的汇报背景选项供用户确认。
2. **摘要观点假设（hypothesis）**：基于素材提炼 1–3 条可被验证、反驳或修正的完整判断，作为摘要观点和报告分析主线。每条写清判断对象、方向性结论及关键原因、关系或对比，不能只写主题、关键词或短标题；证据不足时保留不确定性。
3. **重点素材**：先在普通回复中展示论据清单，说明主要文件、数据或访谈分别能支撑什么，再请用户确认哪些素材质量更高、应优先采用。

三项都必须保存一段用户消息原文，供后续评测理解用户的真实输入。系统、App、工具注入的路径和附件清单只是候选素材，不能代替用户确认。缺少用户原文时，在完成素材解析后一次性提问并结束本轮；收到回复后继续写作，不停在“准备开始”的过程说明。

### 第 2 步：按规则写出初稿 V0

产出前完整阅读 [writing-instructions.md](references/writing-instructions.md)，按其中的证据边界、三段结构、洞察和表达要求写作。

在当前报告目录的 `.report-loop-v2/versions/v0.md` 保存可编辑的 Markdown 初稿 V0，避免把过程稿散落在用户交付目录。正文不得包含内部来源号、分析过程、写作规则、Judge 说明或工具状态。

V0 保存完成之前，不读取 Report Loop 执行卡，不调用、测试或解释 Judge、Rewriter 与 Memory Agent。V0 必须由主 Agent 完成，不能委派给 Rewriter；写作前不要把 Memory 注入写作上下文。

### 第 3 步：启动 Report Loop

确认 V0 文件存在后，读取并直接执行 [loop-orchestration.md](references/loop-orchestration.md)。根据已确认的三项输入、对应用户原文、初稿 V0 和素材路径启动原生 Sub-agent 流程。全过程维护单一隐藏状态；会话恢复时继续未完成阶段，不重复启动。必须等待 Resolution、全部 Judge 和必要的 Rewrite 完成，不得提前结束任务，也不得把 Resolution Plan 或 Judge JSON 当作交付物。

宿主不得事前阅读 Sub-agent Prompt、运行测试或自行替代 Judge 与 Rewrite。完成后交付最终报告和版本记录目录，并简要说明评测版本数、改写次数、最佳版本和最终得分；不要展示内部 JSON、详细 Judge 过程或工具日志。

### 第 4 步：处理用户反馈

用户对已交付报告提出修改意见时，先直接修改当前报告，不重新运行 Report Loop；只有用户明确要求重新评测时才再运行。Memory 已开启时，修改成功后按 [memory-orchestration.md](references/memory-orchestration.md) 委派 `report-memory-agent-v2` 执行 `operation=capture`，再交付修改结果；Memory 关闭时直接交付，不 Capture，也不反复询问用户是否开启。

Judge 反馈和自动改写不得进入 Memory。除处理用户明确提出的记忆开关或管理要求外，主 Agent 不直接维护 Memory；也不得因用户反馈修改 Skill、Base Rubrics、Expert 文件或 WorkBuddy 原生通用 Memory。

## 故障与交付边界

- Report Loop 任一环节失败时，按 `loop-orchestration.md` 保留 V0 和可用的历史最佳版本；宿主不得接管中间 Judge 或 Rewrite。
- Memory 已开启但 Capture 失败时，不回滚已完成的报告修改，也不得通过热改 Expert 或写入 WorkBuddy 通用 Memory 补偿。
- 交付最终可编辑报告和版本记录目录，并明确说明 Report Loop 的评测版本数、改写次数、最佳版本和最终得分；不展开内部 JSON、详细评分过程或工具调用日志。
