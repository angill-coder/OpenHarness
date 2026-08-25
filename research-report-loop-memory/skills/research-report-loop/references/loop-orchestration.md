# Report Loop 执行卡

只在初稿 V1 已保存后读取本文件。Report Loop 是已经验证的黑盒组件：按以下模板构造 Job，然后直接启动一次 Python Runner。不要事前阅读 Runner 源码、运行测试、执行 `--help` 或预检。

## 1. 构造 Job

将 Job 保存为当前报告目录下的 JSON 文件。所有文件路径使用绝对路径。

```json
{
  "schemaVersion": 2,
  "originalUserQuery": "用户最初的报告请求",
  "intakeContext": {
    "reportBackground": {"value": "已确认的汇报背景"},
    "materialHypothesis": {"value": "已确认的完整 hypothesis"},
    "priorityMaterials": [
      {"path": "/absolute/path/to/material", "displayName": "material"}
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

不要猜测或填写宿主模型 ID。Job 写入成功后，插件 Hook 会记录本次 WorkBuddy `sessionId`；Runner 据此从主会话 trace 读取准确的 `requestModelId`。同一 workspace 存在多个活跃会话而 session 标识缺失时，Runner 会明确拒绝，不按“最近会话”猜测。

## 2. 直接执行

WorkBuddy 加载 Skill 时会返回 Skill Base Directory；插件根目录是该目录的上两级。直接使用插件自带启动脚本，不搜索其他 Report Loop，不调用 MCP。

- macOS / Linux：`<PLUGIN_ROOT>/scripts/run-python.sh <PLUGIN_ROOT>/mcp/report_loop/runner.py --job <ABS_JOB_PATH>`
- Windows：`<PLUGIN_ROOT>\scripts\run-python.cmd <PLUGIN_ROOT>\mcp\report_loop\runner.py --job <ABS_JOB_PATH>`

命令执行期间等待 Runner 完成，不并行启动第二个 Runner，不由宿主接管 Judge 或 Rewrite。

## 3. 处理结果

Runner 最终输出一个 JSON 对象：

- 成功：交付 `finalArtifactPath` 指向的文件。
- `judge_unavailable` 或 `rewrite_unavailable`：交付返回的历史最佳文件并简要说明自动评测或改写未完成。
- 明确指出 Job 字段缺失或格式错误：只修正该字段并重试一次。
- 其他错误：保留 V1 和已有历史最佳版本，如实报告错误；不要通过阅读源码、运行测试或手工执行 Judge/Rewrite 来接管流程。
