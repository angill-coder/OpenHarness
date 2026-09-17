# 专家团调用契约

主理人使用 TeamCreate → Agent → SendMessage 协作，不使用旧式匿名 Sub-agent、MCP、Hook 或外部模型 CLI。只有主理人创建团队；已有团队则沿用，不 spawn 主理人自己。专业产出由对应成员完成，成员之间不直接通信。

## 派发与回传

首次派发时，Agent 的 `name` 与 `subagent_type` 都填写下表中的注册 ID；不是中文花名。传入任务与所需文件的绝对路径，不假设成员自动获得用户对话。

| 注册 ID | 输入 | 回传 |
|---|---|---|
| `report-evidence-agent` | prepare/update、来源、数据路径、workDir | EVIDENCE 标记、数据版本与路径 |
| `report-writer` | draft/revise/feedback、需求、证据、基线和目标路径 | REPORT_WRITE 标记 |
| `report-memory-agent` | memoryRoot、operation、用户原文和语境 | MEMORY 标记或 JSON |
| `report-resolution-judge` | Base、候选、任务、可选来源证据 | needs_source 或 Resolution Plan |
| `report-dimension-judge` | 一个冻结 Dimension、报告、reportStats、素材、resultPath | 自行保存 JSON，回传 JUDGE_RESULT_SAVED、assignmentId、dimensionId、resultPath |

每次分配附一个唯一 `assignmentId`（如 `<runId>:R0:<dimensionId>:a1`），并指明主理人的实际消息地址。记录 Agent 返回的真实 `agent_id`、`name`、`task_id`；后续 SendMessage 使用真实 name，不猜测自动后缀，不拿 task_id 冒充 agent_id。工具若没有返回某字段就留空。

成员通过 SendMessage 将完整协议结果回传给主理人；短结果原文传递，长内容传完整文件绝对路径，由下一成员读取，不只转述摘要。消息摘要注明 assignmentId。主理人核验发送者、任务、报告版本及数据绑定后采信。spawn 成功、idle、进度消息都不代表专业任务完成。

按宿主返回的等待方式等待成员消息；无需用户再说“继续”。宿主要求暂停等待消息时，这是等待态，不是交付。不得把没有返回的结果当成功，或因消息暂未到达而重复 spawn；也不对团队 task_id 使用仅适用于普通后台命令的 TaskOutput。只有报告流程终止并按执行卡交付，或确实需要用户补充输入时，才结束面向用户的任务。

## 动态 Judge：一个定义，N 个独立实例

同一 Loop 按冻结 Plan 的 `dimensions[]` 为每个维度保留一个独立 Judge，R0 创建，R1/R2 用 SendMessage 向原成员续评。最多 6 个同时执行评测，8 维分成 6+2；空闲成员保留，全部 N 维有效返回后才聚合，不跨维度复用。

每次 Agent 调用仍用 `name=subagent_type=report-dimension-judge`，模型请求为 `gpt-5.6-sol`。宿主对同名新实例可能自动附加后缀，**以返回的实际 name 为准**，分别绑定到当前 runId、RN、dimensionId、assignmentId、报告路径和数据指纹。在现有 run-state.json 的 `team.assignments` 中保存这份对应关系和 pending/completed/failed 状态，不另建一套调度服务。

每份分配同时记录唯一 resultPath，由 Judge 写入单维结果，主理人只读取校验并保存聚合评分。是否完成以当前分配的有效结果文件为准，消息只作通知；准备等待或恢复时核对全部路径，齐全即继续聚合与改写，不等待重复通知。

续评沿用 `team.assignments` 中该维度的真实成员地址，每轮新建分配记录，传入当前报告、reportStats、assignmentId 和 resultPath；上一分配结束后再发下一轮。仅成员不可用时重建，补充冻结维度、上一轮结果及必要背景。历史结果仅供对照，不能计入本轮聚合；纠错重试使用新 assignmentId/resultPath，不覆盖旧文件。重试与停止上限沿用执行卡。

若宿主拒绝同类多个实例，或未返回可区分的身份，不假设并行已生效；说明并行不可用，改为逐个派发并等待、结束实例后再处理下一维。无法确认旧实例已结束、无法获得隔离的新实例时，按 judge_unavailable 停止，不用主理人补造判断。维度不能因此丢失。

## Writer 连续对话与恢复

同一报告只保留一个活动 Writer；初次用 Agent 创建，后续用 SendMessage 发送下一次 draft/revise/feedback。不用 resume 重建命名团队成员，不同时发两个写作任务。被拒绝候选不是下一轮基线，每次明确传入当前历史最佳或当前用户交付稿。

Writer 的真实 agent_id 保留在 writerAgentId，消息地址保存在 team.writerName。新会话先确认旧成员是否仍属于当前团队且可达；不可达才按 Writer 执行卡重建并传完整必要输入，不假定身份跨会话永久有效。核验已有文件与未完成分配，避免重复写稿。

Resolution 的可选溯源仍最多一轮：通过主理人调用 Memory inspect_sources，再用 SendMessage 把证据交回原 Resolution 成员。Memory 同一 memoryRoot 的写入串行；Evidence 同一项目更新也串行。

## 模型与收尾

两类 Judge 的 Agent frontmatter 使用 `model: gpt-5.6-sol`、`effort: medium`，派发时也指定该 model；其余成员继承宿主模型。实际模型以宿主可用性和调度结果为准，若显示回退则记录并告知，不能把请求值当作已验证运行值。不修改用户全局模型设置。

Judge 在整个 Loop 结束后才按宿主支持的团队关闭协议退出，不在每轮评测后关闭；取消、数据变化或时间预算到期时停止后续派发并通知在途成员停止，保留已落文件、不覆盖报告。Writer 可留待同一会话的反馈续写；新会话按文件恢复。成员工具权限由 WorkBuddy 分配，Prompt 中的读写边界仍必须遵守，不能视为沙箱权限控制。
