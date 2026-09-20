# WorkBuddy Native Report Loop 执行卡

只在初稿 R0 已保存后读取本文件。主 Agent负责流程编排和结果聚合，但不代替 Resolution Judge、Dimension Judge 或 Writer 做各自的判断。

## 1. 沿用本轮目录

沿用初稿阶段按 [保存位置与交付](workspace-and-delivery.md) 确定的本轮 `loop-序号-日期时间/`，不在 WorkBuddy 会话目录另起一套。初稿为 `候选报告/R0.md`，后续候选为 `候选报告/R1.md`、`候选报告/R2.md`……下文相对路径均以该轮 Loop 目录为基准，不覆盖 R0 或历史最佳版本。对外报告 vN 在交付时另行编号。

完整执行并持续维护 [state-and-scoring.md](state-and-scoring.md) 中的单一状态、确定性评分、候选采纳和停止规则。沿用初稿阶段的 `run-state.json` 和 `writerAgentId`，R0 核验完成后进入 resolving 并开始两小时时间预算，不重新初始化状态；会话恢复时先按状态继续，不重新启动另一轮 Loop。

## 2. 整理本轮评测输入

在调用任何 Sub-agent 前，整理一份本轮输入包，并在后续 Resolution、Judge 和 Rewrite 中保持一致：

- 用户最初的报告请求；
- 已确认的汇报背景、摘要观点假设和重点素材；
- 三项确认各自对应的一段用户消息原文；
- audience、project、篇幅和交付要求；
- R0、重点素材和可选 `structured_data.json` 的绝对路径。

三项用户原文必须来自用户消息。系统、App、工具注入的路径、附件名称和自动摘要只能作为候选信息，不能代替用户确认。

重点素材路径可以指向单个文件，也可以指向整个素材目录；目录表示其中素材整体优先，不要为满足输入格式递归展开成大量文件项。没有 `structured_data.json` 时不要传入该字段，也不得填写不存在的占位路径。发现必要路径不存在时，只修正输入一次；仍无效则停止 Loop，保留 R0 并如实说明。

使用资料整理员时，传入共享 `structured_data.json`、原始素材根目录及本轮绑定的 dataVersion/dataSha256，不保存数据快照。整轮保持同一数据版本；每次 Writer 和每批 Judge 调用前后按 [evidence-orchestration.md](evidence-orchestration.md) 核验指纹，不重新清洗。指纹变化时停止后续派发，本次结果不得作为旧版本有效评分或候选采纳；保留文件并标记“数据已变，未验证”，不得偷偷改成新版本继续 Loop。相对 `source_ref` 按原始素材根目录解析，论据不是独立于原始来源的第二个信源。

## 3. 读取 Memory 候选

先按 [Memory 调度契约](memory-orchestration.md#记忆位置) 确定 `memoryRoot`；本轮所有 Memory 调用均携带该绝对路径。委派 `report-memory-agent` 执行 `operation=resolve`，传入：

- 当前 task、audience、project；
- 已确认的汇报背景、摘要观点假设和重点素材；
- Base Rubrics 的版本与维度摘要。

Memory Agent 会先处理到期的 Reflection，再返回与当前任务相关的 L2B 候选、可选 `dimensionCandidate` 和对应的 `sourceL1Ids`。不要把这些内容交给主 Agent改写 R0。

Memory 关闭、无候选或 Resolve 失败时，不调用 Resolution Judge；直接把 Base Rubrics 原样规范化为 Base-only Plan，记录 `resolutionStatus=skipped_no_memory|memory_unavailable` 后进入 Judge。

## 4. 冻结 Resolution Plan

仅当存在 Memory 候选时，委派 `report-resolution-judge`，传入完整 Base Rubrics、当前任务和 Memory Agent 返回结果。Resolution Judge 决定每条 Memory Rubric 是忽略、并入、扩充、覆盖个性化冲突，还是形成新维度。

若首轮返回 `status=needs_source`，只对 `inspectSourceFor` 中的候选委派 Memory Agent 执行 `operation=inspect_sources`，按其 `sourceL1Ids` 读取准确 L1；然后把证据补给同一个 Resolution Judge 完成第二次、也是最后一次判断。不得预先读取全部 L1，也不得允许第二次溯源请求。

将其返回的完整 JSON 原样保存到 `评测与改写记录/resolution-plan.json`。检查：

- `dimensions[]` 非空，ID 唯一；
- 权重合计为 `1.0`；
- 每个维度都有 1–5 anchors；
- 每条 Memory 候选在 `memoryDecisions` 中恰好出现一次；
- Base Dimension、criteria、anchors 和 Base Check 原文全部保留，Check ID 唯一；
- Base 的真实性红线、硬门槛没有被删除或弱化；
- `interpret` 只向目标非红线 Base Check 追加本轮场景解释，`additional` 只新增 Memory Check；
- 新增维度时，Base 各维度原有相对权重保持不变。

维度数量不固定为六。Memory Agent 可以提出新维度候选，最终是否新增只由 Resolution Judge 结合当前任务决定。

Resolution Judge 返回失败、重复溯源或未通过上述检查时，记录 `RESOLUTION_FAILED: <reason>`，再使用 Base Rubrics 的原始维度、权重、Check、anchors 和 gates 生成 Base-only Plan；主 Agent不自行解释 Memory。

## 5. 按动态维度 Judge

先按 [统一篇幅统计](report-length.md) 取得当前候选的 `reportStats`（已有该版本的有效统计则直接复用），随报告传给 Judge。收到 `REPORT_STATS_REQUIRED` 时由主 Agent 补齐统计，不让 Judge 人工计数；无法补齐则按评测不完整处理。

以冻结 Plan 为唯一标准。对 `dimensions[]` 中的每个维度分别委派一次 `report-dimension-judge`；每次只传：

- 一个完整冻结 Dimension；
- 当前候选报告路径；
- 当前任务、受众和篇幅；
- 该维度核验所需的素材或 `structured_data.json` 路径。
- 本次分配的 `assignmentId` 和唯一结果文件绝对路径 `resultPath`，使用 `评测与改写记录/judgments/<RN>.<dimensionId>.a<attempt>.json`，先建好目录并记入 `team.assignments`；每次纠错重试使用新的 attempt 路径。

每轮评测全部 N 个维度，并发上限为 6。R0 创建各维 Judge，后续用 SendMessage 续用同维度成员，传入当前报告、reportStats 和独立 resultPath；按 [团队调用契约](native-agent-contracts.md) 保留分配记录，收齐本轮有效结果后才聚合。不同维度不共用上下文，不重新解释 Memory 或增加维度。

Dimension Judge 自行保存各 Check 的 `met / partial / miss` 判断，再通知文件路径，不拥有分数决定权。主 Agent 按冻结维度清单读取当前分配的 resultPath，校验 dimensionId、Check 完整性及报告/数据绑定，通过后标记 completed；不再代写单维结果。收到通知、恢复会话或准备等待时，先核对全部结果文件，不因漏看消息就判定 Judge 未完成。全部有效结果齐全后立即按 [state-and-scoring.md](state-and-scoring.md) 计算维度分和 overall、保存聚合 Judgment，并继续第 6 步，不停在进度汇报。Judge 缺失、重复或增加 Check，或返回无法解析的结果时最多重试 3 次；仍失败则停止循环，交付完成 Judge 的历史最佳版本并说明评测不完整，不由主 Agent补造判断。

## 6. Rewrite 与停止

R0 首次完成有效 Judge 后自动成为历史最佳。达到总分 `5.0`、所有维度为 `5` 且没有 redline/hard floor 失败时结束。否则先按 [state-and-scoring.md](state-and-scoring.md) 生成当前历史最佳版本的 Revision Brief，再按 [Writer 调用与续写](writer-orchestration.md) 用 SendMessage 向原 Writer 发送 `mode=revise`，传入：

- 当前历史最佳报告与新候选路径；
- 冻结 Resolution Plan；
- Revision Brief、上一候选的采纳结果与简短前序改写历史；
- 素材与篇幅边界。

Writer 只能从历史最佳版本生成新候选。生成后按同一冻结 Plan 重新执行全部维度 Judge；严格执行四项候选采纳门槛，包括 **overall 不得下降**。拒绝候选仍保留在本轮隐藏目录的 `候选报告/` 供追溯，但不能成为下一轮改写基线；其回退项进入下一份 Revision Brief 的 `avoid`。

满足以下任一条件即停止：

- 达到 5.0 且无门槛失败；
- 连续两个候选没有改善；
- 当前时间达到 `deadlineAt`（从进入 resolving 起 120 分钟）；
- Writer 返回 `REPORT_WRITE_FAILED: <reason>`。
- 数据或来源变化导致绑定失效：`data_version_changed`，不采纳本次未验证输出。
- 用户明确要求停止。

V2 不另设固定 Rewrite 轮数上限；停止条件为目标分、连续无改善、两小时时间预算及用户取消。

## 7. 交付

按 [统一目录约定](workspace-and-delivery.md) 将最佳 RN 发布到报告目录的 `<报告主题>-vN.md`，在 `版本说明.md` 登记该交付版。正文未变时沿用当前 vN 并追加本次评测，不新增副本；运行状态记录最佳 RN 与交付 vN/路径的对应关系，防止恢复时重复发布。版本说明记录：

- `judgedVersions`：完成评测的版本数；
- `rewriteRounds`：改写次数；
- `bestVersion`：历史最佳版本；
- 最终各维度分数与 `bestScore`；
- stop code 与简短原因；
- 本次交付的 vN、Loop 标识、dataVersion/dataSha256、生成时间、用户要求及修改摘要；内部候选明细仅留运行记录，未评测报告明确标注，不沿用其他版本分数；
- 如有失败，仅记录简短状态。

向用户只交付本次 `<报告主题>-vN.md` 和简短结果摘要，不展示旧稿、内部 RN 候选、`.report-agent/`、Resolution Plan、Judge JSON、Sub-agent Prompt 或内部调用日志。
