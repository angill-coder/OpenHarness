# WorkBuddy Native Report Loop 执行卡

只在初稿 V0 已保存后读取本文件。主 Agent负责流程编排和结果聚合，但不代替 Resolution Judge、Dimension Judge 或 Rewriter 做各自的判断。

## 1. 建立本轮目录

在当前报告目录维护以下文件；过程状态放进隐藏目录，不散落到用户交付目录：

```text
.report-loop-v2/
├── versions/v0.md
├── .state/
│   ├── run-state.json
│   ├── resolution-plan.json
│   ├── judgments/v0.json
│   └── revision-briefs/v0.json
├── final.md
└── summary.md
```

后续候选依次保存为 `versions/v1.md`、`v2.md`……不要覆盖 V0 或历史最佳版本。

完整执行并持续维护 [state-and-scoring.md](state-and-scoring.md) 中的单一状态、确定性评分、候选采纳和停止规则。V0 保存后立即建立 `run-state.json`；会话恢复时先按状态继续，不重新启动另一轮 Loop。

## 2. 整理本轮评测输入

在调用任何 Sub-agent 前，整理一份本轮输入包，并在后续 Resolution、Judge 和 Rewrite 中保持一致：

- 用户最初的报告请求；
- 已确认的汇报背景、摘要观点假设和重点素材；
- 三项确认各自对应的一段用户消息原文；
- audience、project、篇幅和交付要求；
- V0、重点素材和可选 `structured_data.json` 的绝对路径。

三项用户原文必须来自用户消息。系统、App、工具注入的路径、附件名称和自动摘要只能作为候选信息，不能代替用户确认。

重点素材路径可以指向单个文件，也可以指向整个素材目录；目录表示其中素材整体优先，不要为满足输入格式递归展开成大量文件项。没有 `structured_data.json` 时不要传入该字段，也不得填写不存在的占位路径。发现必要路径不存在时，只修正输入一次；仍无效则停止 Loop，保留 V0 并如实说明。

## 3. 读取 Memory 候选

委派 `report-memory-agent-v2` 执行 `operation=resolve`，传入：

- 当前 task、audience、project；
- 已确认的汇报背景、摘要观点假设和重点素材；
- Base Rubrics 的版本与维度摘要。

Memory Agent 会先处理到期的 Reflection，再返回与当前任务相关的 L2B 候选、可选 `dimensionCandidate` 和对应的 `sourceL1Ids`。不要把这些内容交给主 Agent改写 V0。

Memory 关闭、无候选或 Resolve 失败时，不调用 Resolution Judge；直接把 Base Rubrics 原样规范化为 Base-only Plan，记录 `resolutionStatus=skipped_no_memory|memory_unavailable` 后进入 Judge。

## 4. 冻结 Resolution Plan

仅当存在 Memory 候选时，委派 `report-resolution-judge-v2`，传入完整 Base Rubrics、当前任务和 Memory Agent 返回结果。Resolution Judge 决定每条 Memory Rubric 是忽略、并入、扩充、覆盖个性化冲突，还是形成新维度。

若首轮返回 `status=needs_source`，只对 `inspectSourceFor` 中的候选委派 Memory Agent 执行 `operation=inspect_sources`，按其 `sourceL1Ids` 读取准确 L1；然后把证据补给同一个 Resolution Judge 完成第二次、也是最后一次判断。不得预先读取全部 L1，也不得允许第二次溯源请求。

将其返回的完整 JSON 原样保存到 `.report-loop-v2/.state/resolution-plan.json`。检查：

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

以冻结 Plan 为唯一标准。对 `dimensions[]` 中的每个维度分别委派一次 `report-dimension-judge-v2`；每次只传：

- 一个完整冻结 Dimension；
- 当前候选报告路径；
- 当前任务、受众和篇幅；
- 该维度核验所需的素材或 `structured_data.json` 路径。

有 N 个维度就调用 N 次；并发上限为 6，超过时分批执行。每个 Judge 只评一维，不允许重新解释 Memory 或增加维度。

Dimension Judge 只返回各 Check 的 `met / partial / miss` 判断，不拥有分数决定权。全部返回后，严格按 [state-and-scoring.md](state-and-scoring.md) 统一计算维度分和 overall，并持久化该版本 Judgment。Judge 缺失、重复或增加 Check，或返回无法解析的结果时最多重试 3 次；仍失败则停止循环，交付完成 Judge 的历史最佳版本并说明评测不完整，不由主 Agent补造判断。

## 6. Rewrite 与停止

V0 首次完成有效 Judge 后自动成为历史最佳。达到总分 `5.0`、所有维度为 `5` 且没有 redline/hard floor 失败时结束。否则先按 [state-and-scoring.md](state-and-scoring.md) 生成当前历史最佳版本的 Revision Brief，再委派 `report-rewriter-v2`，传入：

- 当前历史最佳报告与新候选路径；
- 冻结 Resolution Plan；
- Revision Brief 与简短前序改写历史；
- 素材与篇幅边界。

Rewriter 只能从历史最佳版本生成新候选。生成后按同一冻结 Plan 重新执行全部维度 Judge；严格执行四项候选采纳门槛，包括 **overall 不得下降**。拒绝候选仍保留在 `versions/` 供追溯，但不能成为下一轮改写基线；其回退项进入下一份 Revision Brief 的 `avoid`。

满足以下任一条件即停止：

- 达到 5.0 且无门槛失败；
- 连续两个候选没有改善；
- 从 V0 首次 Judge 开始已运行约一小时；
- Rewriter 返回 `REPORT_REWRITE_FAILED: <reason>`。
- 用户明确要求停止。

V2 不另设固定 Rewrite 轮数上限；停止条件沿用 V1 的目标分、连续无改善、一小时时间预算及用户取消语义。

## 7. 交付

将历史最佳版本保存为 `.report-loop-v2/final.md`；在 `summary.md` 记录：

- `judgedVersions`：完成评测的版本数；
- `rewriteRounds`：改写次数；
- `bestVersion`：历史最佳版本；
- 最终各维度分数与 `bestScore`；
- stop code 与简短原因；
- 如有失败，仅记录简短状态。

向用户交付 `final.md`、`versions/` 和简短结果摘要。不要展示 `.state`、Resolution Plan、Judge JSON、Sub-agent Prompt 或内部调用日志。
