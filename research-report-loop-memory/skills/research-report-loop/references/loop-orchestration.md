# Report Loop 执行卡

只在初稿 V1 已保存后读取本文件。Report Loop 是已经验证的黑盒组件：按以下模板构造 Job；Job 写入成功后，插件 Hook 会自动在宿主侧启动 Runner。不要搜索或调用 Report Loop MCP，不要事前阅读 Runner 源码、运行测试、执行 `--help` 或预检。

## 1. 构造 Job

将 Job 保存为当前报告目录下的 JSON 文件。所有文件路径使用绝对路径。Job 文件名不是触发条件，Hook 会识别任意合法 Job JSON；每轮 Report Loop 只创建并写入一个 Job 文件，写入成功后不得为了匹配固定文件名而复制、改名或再次写入同一 Job。

```json
{
  "schemaVersion": 2,
  "originalUserQuery": "用户最初的报告请求",
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
  "v1ArtifactPath": "/absolute/path/to/report-v1.md",
  "structuredDataPath": "/absolute/path/to/structured_data.json",
  "judgeProvider": "workbuddy",
  "hostModel": {"effort": "当前 effort，可省略；整个字段也可省略"},
  "outputPath": "/absolute/path/to/report-final.md"
}
```

三项 `userInputEvidence` 必须分别保存用户消息中的真实原文。系统、App、工具提供的路径、附件名称或自动摘要不能代替用户确认。没有 `structured_data.json` 时删除 `structuredDataPath`，不要填写不存在的路径。

`priorityMaterials[].path` 可以指向一个具体文件，也可以指向一个素材目录；目录表示其中素材整体优先，不要为了满足 Job 格式递归展开成大量文件项。

不要猜测或填写宿主模型 ID。Job 写入成功后，插件 Hook 会记录本次 WorkBuddy `sessionId`；Runner 据此从主会话 trace 读取准确的 `requestModelId`。同一 workspace 存在多个活跃会话而 session 标识缺失时，Runner 会明确拒绝，不按“最近会话”猜测。

## 2. 等待宿主 Hook

Job 写入成功后，PostToolUse Hook 会在 Agent 沙箱外启动 Runner，并返回唯一的结果文件绝对路径和一条后台等待命令。立即使用 `Bash` 工具执行这条命令，设置 `run_in_background=true`，并保存工具返回的 `task_id`。等待任务完成后，WorkBuddy 会注入 `<task-notification>`；收到通知后调用 `TaskOutput(task_id)` 一次，读取完整 JSON 结果。

后台等待任务只等待结果文件，不会再次启动 Runner，也不参与 Judge 或 Rewrite。不要反复 Read 结果/状态文件，不要自行编写 shell 轮询。

不要执行 ToolSearch，不要调用 `report_loop_run`，不要检查 MCP 连接状态，也不要并行启动第二个 Loop。Hook 只是宿主侧 Launcher，不参与 Rubric、Judge、Rewrite 或停止条件判断。

## 3. 处理结果

Runner 最终输出一个 JSON 对象：

- 成功：交付 `finalArtifactPath` 指向的文件。
- `judge_unavailable` 或 `rewrite_unavailable`：交付返回的历史最佳文件并简要说明自动评测或改写未完成。
- 明确指出 Job 字段缺失或格式错误：只修正该字段并重试一次。
- 其他错误：保留 V1 和已有历史最佳版本，如实报告错误；不要通过阅读源码、运行测试或手工执行 Judge/Rewrite 来接管流程。
