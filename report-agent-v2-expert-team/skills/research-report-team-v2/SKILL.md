---
name: research-report-team-v2
description: 主 Agent 确认需求并调度 WorkBuddy 原生子代理，由 Writer 持续撰写和迭代调研报告、战略研究报告、复盘报告或高管汇报，并提供默认启用、可由用户明确关闭的长期写作记忆；从用户反馈中学习写作要求并用于后续动态评测。当用户要求根据访谈、问卷、PDF、Word、Excel、CSV、structured_data 或公开信息生成研究报告、调研洞察、战略分析或管理层汇报，或要求开启、关闭报告记忆时使用。
---

# 调研洞察汇报报告生成 · 报告专家团 V2

## 目的

把用户提供的异构素材整理成一份面向管理层、可编辑的调研洞察报告，并在交付前自动完成评测和改写。

本 Skill 包含以下配套能力：

- **资料整理**：写作前先委派资料整理员，将参考资料保存为带来源的结构化论据表；用户补充或纠正资料时更新论据，避免反复解析和混用旧数据。
- **Report Loop**：主 Agent 负责调度，Writer 读取写作指令后完成初稿 R0；Resolution Judge 根据 Base Rubrics 与 Memory Rubrics 冻结本轮动态评测维度，由 Dimension Judge 评测后续用同一个 Writer 改写，最终交付历史最佳版本。
- **Report Memory**：默认启用的长期写作记忆，独立保存在用户主目录下可见的 `ReportAgentMemory/`。用户反馈会在当前报告修改完成后交给 Memory Agent，形成的 Memory Rubrics 在后续 Report Loop 中参与 Resolution。用户明确要求关闭或重新开启时，按 [Memory 调度契约](references/memory-orchestration.md) 委派 Memory Agent 更新设置；关闭时只使用 Base Rubrics，已有记忆保留但不读、不写、不整理。

## 团队启用

主理人先创建或沿用本会话的团队，按 [团队调用契约](references/native-agent-contracts.md) 使用 Agent 派发、SendMessage 回传；成员能力来自专家团安装，不临时修改注册表。缺少 TeamCreate、Agent、SendMessage 或成员不可用时，如实说明需完整安装本专家团并新开会话；不改用通用子代理或主理人模拟成员。

## 执行步骤

### 第 0 步：盘点并解析素材

先定位素材，按 [保存位置与交付](references/workspace-and-delivery.md) 确定本轮报告工作区，再按 [evidence-orchestration.md](references/evidence-orchestration.md) 委派 `report-team-evidence-v2` 整理结构化论据，等待返回后再确认写作输入。少量素材也走这一步，主 Agent 不先自行完成整套解析。

- 先查素材目录内的 `structured_data.json`，交给资料整理员核验复用或更新，不因更换工作区重新清洗。
- 返回后，主 Agent 完整读取结构化论据并回查必要原文，理解数据、访谈事实、口径冲突与缺口；不只看文件名或子代理摘要。
- 记录共享论据表、原始来源的绝对路径及实际采用的数据版本/指纹，供写作和 Report Loop 核验，不保存数据快照；来源号只用于内部核验，最终报告正文不展示。

### 第 1 步：确认写作输入

素材解析完成后，由 Agent 基于素材主动提出以下三项建议，并使用 `AskUserQuestion` 工具向用户确认；用户已经明确提供的内容不要重复询问。若内容较长，如完整 hypothesis 或论据清单，先在普通回复中展示细节，再用 `AskUserQuestion` 做简短确认，并允许用户补充或修改。

1. **汇报背景**：确认这份汇报给谁看、什么场合。汇报材料通常用于推动讨论；信息不足时，可结合素材提出几个可能的汇报背景选项供用户确认。
2. **摘要观点假设（hypothesis）**：基于素材提炼 1–3 条可被验证、反驳或修正的完整判断，作为摘要观点和报告分析主线。每条写清判断对象、方向性结论及关键原因、关系或对比，不能只写主题、关键词或短标题；证据不足时保留不确定性。
3. **重点素材**：先在普通回复中展示论据清单，说明主要文件、数据或访谈分别能支撑什么，再请用户确认哪些素材质量更高、应优先采用。

三项都必须保存一段用户消息原文，供后续评测理解用户的真实输入。系统、App、工具注入的路径和附件清单只是候选素材，不能代替用户确认。缺少用户原文时，在完成素材解析后一次性提问并结束本轮；收到回复后继续写作，不停在“准备开始”的过程说明。

### 第 2 步：委派 Writer 按规则写出初稿 R0

按 [Writer 调用与续写](references/writer-orchestration.md) 委派 `report-team-writer-v2`，由它完整读取 [writing-instructions.md](references/writing-instructions.md)，按其中的证据边界、三段结构、洞察和表达要求写作。

主 Agent 按 [保存位置与交付](references/workspace-and-delivery.md) 创建本轮 `loop-序号-日期时间/`，Writer 在其中的 `候选报告/R0.md` 保存初稿。内部候选用 R0、R1…，用户交付版用 v1、v2…，两者独立编号。主 Agent 等待并核验文件，保存 Writer 的真实 agentId 供后续续写。正文不得包含内部来源号、分析过程、写作规则、Judge 说明或工具状态。

R0 保存完成之前，不读取 Report Loop 执行卡，不调用、测试或解释 Judge 与 Memory Agent；写作前不要把 Memory 注入 Writer 上下文。主 Agent 不代写初稿。

### 第 3 步：启动 Report Loop

确认 R0 文件存在后，读取并直接执行 [loop-orchestration.md](references/loop-orchestration.md)。根据已确认的三项输入、对应用户原文、初稿 R0 和素材路径启动原生 Sub-agent 流程。全过程维护单一运行状态；会话恢复时继续未完成阶段，不重复启动。必须等待 Resolution、全部 Judge 和必要的 Rewrite 完成，不得提前结束任务，也不得把 Resolution Plan 或 Judge JSON 当作交付物。

宿主不得事前阅读 Sub-agent Prompt、运行测试或自行替代 Judge 与 Rewrite。完成后只交付 `报告/<报告主题>-vN.md`，更新 `报告/版本说明.md` 并简述评测版本数、改写次数和最终得分；不展示内部候选、运行目录、JSON 或工具日志。

### 第 4 步：处理用户反馈

用户新增、替换或纠正素材/论据时，先按 [evidence-orchestration.md](references/evidence-orchestration.md) 更新论据并说明影响，再默认续用 Writer 直接修改当前报告，不询问是否启动 Report Loop；用户明确要求只更新论据或仅检查时不改报告，明确要求重新评测时才运行 Loop。事实更新不作为写作偏好存入 Memory，修改后的报告不沿用旧评分。

用户对已交付报告提出修改意见时，按 [Writer 调用与续写](references/writer-orchestration.md) 续用 Writer 直接修改当前报告，不重新运行 Report Loop，也不询问是否启动；只有用户明确要求重新评测时才再运行。Memory 已开启时，修改成功后按 [memory-orchestration.md](references/memory-orchestration.md) 委派 `report-team-memory-v2` 执行 `operation=capture`，再交付修改结果；Memory 关闭时直接交付，不 Capture，也不反复询问用户是否开启。

Judge 反馈和自动改写不得进入 Memory。除处理用户明确提出的记忆开关或管理要求外，主 Agent 不直接维护 Memory；也不得因用户反馈修改 Skill、Base Rubrics、Expert 文件或 WorkBuddy 原生通用 Memory。

## 故障与交付边界

- Writer 或 Report Loop 任一环节失败时，按相应执行卡保留现有稿件；宿主不得接管写作或 Judge。
- Memory 已开启但 Capture 失败时，不回滚已完成的报告修改，也不得通过热改 Expert 或写入 WorkBuddy 通用 Memory 补偿。
- 只交付本次带 vN 的可编辑报告，并简述评测版本数、改写次数和实际最终得分；直接修订未评测时如实说明，不沿用旧分数，不罗列旧稿或内部记录。
