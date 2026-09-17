# Report Loop 执行卡

只在本轮初稿已保存后读取本文件。Report Loop 是已经验证的黑盒组件：按以下模板构造 Job；Job 写入成功后，插件 Hook 会自动在宿主侧启动 Runner——这是唯一的启动方式，没有可调用的 MCP 工具。不要事前阅读 Runner 源码、运行测试、执行 `--help` 或预检。

## 1. 构造 Job

将 Job 保存为 `报告目录/.report-agent/<报告标识>/job.json`。本轮初稿保存在同一报告标识目录下。所有文件路径使用绝对路径。Job 文件名不是触发条件，Hook 会识别任意合法 Job JSON；每轮 Report Loop 只创建并写入一个 Job 文件，写入成功后不得为了匹配固定文件名而复制、改名或再次写入同一 Job。

```json
{
  "schemaVersion": 2,
  "originalUserQuery": "用户最初的报告请求",
  "reportTopic": "报告主题，用于版本文件名与报告标识",
  "reportDir": "/absolute/path/to/报告；主理人未指定时可省略",
  "reportId": "<报告主题>-<首次创建时间>；续写或重启 Loop 时必填",
  "intakeContext": {
    "reportBackground": {"value": "已确认的汇报背景"},
    "materialHypothesis": {"value": "已确认的完整 hypothesis"},
    "priorityMaterials": [
      {"path": "/absolute/path/to/material-or-directory", "displayName": "重点素材"}
    ],
    "userInputEvidence": {
      "reportBackground": "用户消息中的对应原文",
      "materialHypothesis": "用户消息中的对应原文",
      "priorityMaterials": "用户消息中的对应原文"
    }
  },
  "audience": "已确认的受众；没有则为空字符串",
  "project": "当前项目名称；没有则为空字符串",
  "draftArtifactPath": "/absolute/path/to/本轮初稿.md",
  "structuredDataPath": "/absolute/path/to/structured_data.json",
  "dataVersion": "资料清洗返回的数据版本，如 D2",
  "dataSha256": "资料清洗返回的论据表指纹",
  "roundRequest": "本轮用户要求；重启 Loop 时填写大改要求",
  "judgeProvider": "workbuddy",
  "hostModel": {"effort": "当前 effort，可省略；整个字段也可省略"}
}
```

三项 `userInputEvidence` 必须分别保存用户消息中的真实原文。系统、App、工具提供的路径、附件名称或自动摘要不能代替用户确认。没有 `structured_data.json` 时删除 `structuredDataPath`，不要填写不存在的路径。

`priorityMaterials[].path` 可以指向一个具体文件，也可以指向一个素材目录；目录表示其中素材整体优先，不要为了满足 Job 格式递归展开成大量文件项。

不要猜测或填写宿主模型 ID。Job 写入成功后，插件 Hook 会记录本次 WorkBuddy `sessionId`；Runner 据此从主会话 trace 读取准确的 `requestModelId`。同一 workspace 存在多个活跃会话而 session 标识缺失时，Runner 会明确拒绝，不按“最近会话”猜测。

### 交付位置由 Runner 推导

**不要填 `outputPath`。** Runner 从已校验的 `priorityMaterials` 推导素材目录，按 `reportDir`（未传则用 `素材目录/报告/`）发布 `报告目录/<报告主题>-vN.md`，版本号取已有交付版本最大序号加一。Loop 交付与直接改写共用同一套版本号，旧版保留。

只有用户明确指定了交付文件位置时才填 `outputPath`，且必须落在报告目录内——越界 Runner 会直接拒绝。

`reportId` 决定产物归属：**同一报告的多次 Loop 必须传同一个 `reportId`**，Runner 会在该标识下新建 `loop-序号-时间戳/`，序号递增，旧 Loop 目录完整保留。省略 `reportId` 时 Runner 按 `reportTopic` 查找已有标识并沿用；查不到才新建。

### Judge 模型（默认不填）

Judge 模型默认锁定，以保证不同报告之间的评分可比——写作记忆依赖这个可比性来判断反馈是否值得沉淀。**正常情况下不要填 `judgeModel` 或 `judgeEffort`。**

只有用户明确要求更换 Judge 模型时，才添加这两个可选字段：

```json
{
  "judgeModel": "用户明确指定的模型 ID",
  "judgeEffort": "low|medium|high|xhigh|max|ultra"
}
```

填写后本轮评分与历史评分不可比，Runner 在结果中返回 `scoresComparableToHistory: false`，此时必须向用户说明这一点。不要为了“试试看”或猜测更好的模型而填写。

## 2. 等待宿主 Hook

Job 写入成功后，PostToolUse Hook 会在 Agent 沙箱外启动 Runner，并返回唯一的结果文件绝对路径和一条后台等待命令。立即使用 `Bash` 工具执行这条命令，设置 `run_in_background=true`，并保存工具返回的 `task_id`。等待任务完成后，WorkBuddy 会注入 `<task-notification>`；收到通知后调用 `TaskOutput(task_id)` 一次，读取完整 JSON 结果。

后台等待任务只等待结果文件，不会再次启动 Runner，也不参与 Judge 或 Rewrite。不要反复 Read 结果/状态文件，不要自行编写 shell 轮询。

不要执行 ToolSearch 寻找启动工具，也不要并行启动第二个 Loop。Hook 只是宿主侧 Launcher，不参与 Rubric、Judge、Rewrite 或停止条件判断。

## 3. 处理结果

Runner 最终输出一个 JSON 对象。它是内部控制结果，不直接展示给用户：

- 成功：交付 `finalArtifactPath` 指向的交付版本（`报告目录/<报告主题>-vN.md`）；明确说明 `judgedVersions`（评测候选数）、`rewriteRounds`（改写次数）、`bestVersion`（最佳候选）和 `bestScore`（最终得分）。内部候选与评测明细留在 `loopDir`，默认不展示，也不复制到交付目录旁。
- `scoresComparableToHistory` 为 `false` 时：照常交付，但须向用户明示本轮评分所用的 Judge 模型与默认不同（`judgeModel`），因此**与历史报告得分不可直接比较**。`judgeFallbackUsed` 为 `true` 表示默认 Judge 模型不可用、已自动降级到当前主模型打分；这不是报告质量问题，但同样影响可比性。
- `judge_unavailable` 或 `rewrite_unavailable`：交付返回的历史最佳文件并简要说明自动评测或改写未完成。
- 明确指出 Job 字段缺失或格式错误：只修正该字段并重试一次。
- 其他错误：保留本轮初稿和已有交付版本，如实报告错误；不要通过阅读源码、运行测试或手工执行 Judge/Rewrite 来接管流程。
