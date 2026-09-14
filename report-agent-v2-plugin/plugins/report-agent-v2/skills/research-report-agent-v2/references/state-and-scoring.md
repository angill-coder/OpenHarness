# Report Loop V2 状态、评分与采纳规则

本文件把原版 Runtime 中必须保持稳定的机械规则交给主 Agent 执行。这里不包含报告质量判断；质量判断仍由 Resolution Judge 和 Dimension Judge 完成。

## 1. 单一运行状态

首次委派 Writer 前在本轮工作区创建 `Agent运行记录/评测与改写记录/run-state.json`，以 `drafting` 记录初稿阶段；V0 核验完成后进入 `resolving`，此时才设置 startedAt 为当前时间、deadlineAt 为 60 分钟后，初稿不占用 Loop 时间预算。下列路径用于新任务，恢复已有任务时沿用已记录的原路径：

```json
{
  "schemaVersion": 1,
  "runId": "report-agent-v2-<本轮唯一ID>",
  "stateRevision": 0,
  "status": "drafting|resolving|judging|rewriting|completed",
  "writerAgentId": null,
  "dataVersion": "D1",
  "dataSha256": "<共享论据表的真实 SHA-256>",
  "startedAt": null,
  "deadlineAt": null,
  "baseRubricVersion": "...",
  "memoryRevision": "...",
  "resolutionStatus": "pending",
  "currentBestVersion": null,
  "nextVersion": 1,
  "noImprovementStreak": 0,
  "judgedVersions": [],
  "stopState": {"stopped": false, "code": null, "reason": null}
}
```

每次更新前重新读取状态并核对本阶段开始时的 `stateRevision`；一致才写入并将其加一，不一致说明另一流程已经推进，停止当前写入并从最新状态恢复。每完成一个阶段就先更新状态文件，再进入下一阶段。所有 JSON 使用 UTF-8 完整写入，不在用户交付目录散落状态。恢复会话时先读该文件；已完成 Resolution 的阶段再读取冻结 Plan：

- `drafting`：按 [Writer 调用与续写](writer-orchestration.md) 核验已有 V0 或恢复写作，不重复覆盖初稿；
- `completed`：不得自动继续 Judge 或 Rewrite；用户明确提出的反馈修订可续用 Writer 修改交付稿，但不改写旧 Judgment 或沿用旧评分；
- `resolving`：从 Resolution 继续；
- `judging`：只补齐当前版本尚未完成的维度；
- `rewriting`：核对目标版本不存在后继续 Rewrite；
- 当前阶段必需的文件缺失、互相矛盾或冻结 Plan 已变化：停止自动循环，交付已完成 Judge 的历史最佳版本并说明状态无法安全恢复。

版本文件一经写入不得覆盖。V0 首次完成有效 Judge 后自动成为历史最佳；后续候选必须经过采纳门槛。若目标版本文件已存在，不得复用同一版本号。

`writerAgentId` 只保存宿主工具实际返回的可恢复 ID，更新状态时保留它，不在进入 Loop 时重置。旧状态没有该字段时视为 null，保留其余运行记录；不得用后台 taskId 或 Writer 自报文本代替。

dataVersion/dataSha256 在本轮开始时绑定，不能随共享数据变化而静默替换；每个 Judgment 和报告版本说明都记录实际绑定值。恢复时先校验，无法取得原数据则不能继续旧评测。数据变化导致本次输出未验证时停止并说明，不把旧评分嫁接到新数据；已有历史结果仍保留其原版本标识。

## 2. Judgment 持久化与校验

每个版本的聚合评测写入 `Agent运行记录/评测与改写记录/judgments/<version>.json`。每个 Dimension Judge 必须覆盖该冻结 Dimension 的全部 Check，且每个 Check 恰好一次；只允许：

```text
met = 1.0
partial = 0.5
miss = 0.0
```

缺失 Check、额外 Check、重复 Check、非法状态或无法解析 JSON 时，该维度调用无效，最多重试 3 次。仍失败则以 `judge_unavailable` 停止，不由主 Agent补造判断。

对每个维度确定性计算：

```text
dimensionScore = 1 + 4 × average(checkValues)
```

- 分数限制在 `1.0–5.0`，保留 3 位小数；
- 该维度任一 `redline=true` Check 为 `miss` 时，维度分最高为 `2.0`；
- 维度分低于冻结 Dimension 的 `hardFloor` 时，记录 hard floor 失败；
- `overall = Σ(dimensionScore × weight)`，保留 3 位小数；
- Judge 返回的任何自报分数都忽略，只使用 Check 状态计算。

聚合 Judgment 至少保存：全部 Check 状态及证据、各维度分、overall、redline failures、hard floor failures、strengths、issues、revisionDirectives 和 Judge 模型信息。

## 3. Revision Brief

不要把全部原始 Judge 输出直接塞给 Writer。主 Agent从历史最佳版本的 Judgment 生成 `Agent运行记录/评测与改写记录/revision-briefs/<version>.json`：

- `repair`：所有非 `met` Check 的维度、要求、状态和原因；
- `preserve`：所有 `met` Check 对应的已达成要求与具体优点；
- `avoid`：此前被拒绝候选中，相对其父版本发生回退的 Check；
- `userRequirements`：用户确认的本轮要求；
- `userOverrides`：因用户明确要求而应保留的结构、表达或交付形式。

续用 Writer 时只追加当前历史最佳报告路径、该 Revision Brief、冻结 Plan（首次或变化时）、素材边界变化与简短改写记录；不得从被拒绝候选继续改写。恢复失败后的新 Writer 仍需完整输入，见 [Writer 调用与续写](writer-orchestration.md)。

## 4. 候选采纳门槛

当前历史最佳版本中存在非 `met` Check 的维度为目标维度；若没有可识别目标，则所有维度都是目标维度。候选必须同时满足：

1. 至少一个目标维度相对历史最佳提高超过 `0.001`；若无可比较目标，则 overall 提高超过 `0.001`；
2. 任一非目标维度下降不超过 `0.15`；
3. 没有新增 redline failure；
4. overall 存在且不低于历史最佳。

四项全部成立才采纳，更新 `currentBestVersion` 并把 `noImprovementStreak` 清零；否则拒绝，历史最佳不变，`noImprovementStreak + 1`。把每项判断及分差保存为该版本的 `adoptionGate`，不得只写“有改善”。

## 5. 停止与取消

每次完成 Judgment 和采纳判断后依次检查：

- 历史最佳 overall 达到 `5.0`，且没有 redline/hard floor 失败：`target_reached`；
- `noImprovementStreak >= 2`：`no_improvement`；
- 当前时间达到 `deadlineAt`：`time_budget_exhausted`；
- Judge 最多重试后仍不可用：`judge_unavailable`；
- Writer 失败或无法安全写入新版本：`rewrite_unavailable`；
- 数据版本或来源指纹变化，无法核验本轮输入：`data_version_changed`；
- 用户明确要求停止：`user_cancelled`。

停止后将 `status=completed`，保存 stop code/reason，只交付完成 Judge 的历史最佳版本。除以上原因外，不得提前结束；也不设置固定 Rewrite 轮数上限。
