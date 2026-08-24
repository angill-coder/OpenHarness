# Report Loop 调度契约

## 调用顺序

### 创建 Loop

初稿写完后调用一次。MCP 会读取当前版本化 Rubric Set，按 `Base → core → audience → project` 应用 Criterion Overlay 并冻结；宿主 Agent 不需要先 Recall Memory：

```json
{
  "task": "本次报告的完整任务",
  "audience": "已确认的受众",
  "project": "项目或主题名称",
  "artifactPath": "/absolute/path/to/report.md",
  "structuredDataPath": "/absolute/path/to/structured_data.json",
  "targetScore": 5.0,
  "maxJudgedVersions": 3
}
```

`structuredDataPath` 不存在时省略，不要虚构路径。保存返回的 `runId`，后续不得重新调用 start。
同时保存返回的 `rubricSetVersion / rubricResolverHash / memoryRevision / memoryRubricIds` 供审计；Memory 为空或不可用时继续使用 Base Rubrics。

### 提交报告版本

```json
{
  "runId": "report-xxxxxxxxxxxx",
  "artifactPath": "/absolute/path/to/report.md"
}
```

一次 `report_loop_submit` 会同步完成：

1. 冻结当前报告版本；
2. 按当前冻结 Rubric 的 Dimension 并行发起独立 Judge；默认六维，Personal 生效时为七维；
3. 计算维度分和总分；
4. 判断候选版本是否采纳；
5. 判断是否继续修改或停止。

## 处理返回结果

### `nextAction=revise`

- 读取 `bestArtifactPath`，不要假定刚提交的候选版本已被采纳。
- `revisionBrief.repair` 是本轮需要修复的检查项。
- `revisionBrief.preserve` 是当前已通过、修改时不能破坏的质量项。
- `revisionBrief.avoid` 是被拒绝版本引入的回退模式。
- `revisionBrief.userRequirements` 是用户本轮完整任务，修改时必须继续满足。
- `revisionBrief.userOverrides` 是因用户明确要求而豁免的非证据类检查项，不得把它们当作缺陷修回基础 Rubric 的默认写法。
- 只做与 repair 有关的修改，保存报告，再用同一 `runId` 提交。

### `nextAction=deliver`

调用 `report_loop_finish`。最终只交付 finish 返回的 `bestArtifactPath`。

### `decision=rejected`

被拒绝的候选版本仍保留用于审计，但不成为下一轮基线。下一轮必须从返回的 `bestArtifactPath` 重新修改。

## 停止条件

命中任一条件即停止：

- 总分达到 5.0，且无红线或硬门槛失败；
- 连续两个候选版本未被采纳；
- 已评测三个版本；
- 达到最长运行时间；
- Judge 不可用或用户取消后续迭代。

停止不代表最后一次候选必然被采纳；始终以 `bestVersion` 和 `bestArtifactPath` 为准。
