# Memory 行为测试

`npm test` 检查结构、入口约束和现有运行契约。`memory-cases.json` 包含明确委托、选项采纳、普通/含糊反馈，以及任务确认、版本选择、事实纠错等反例；留出用例标记为 `holdout`。预期不传给被测模型。

真实模型测试（会消耗模型额度）：

```sh
WORKBUDDY_EVAL_CLI="<WorkBuddy CLI 的实际入口>" node scripts/eval-memory.mjs "<绝对输出目录>" "<修改前提示词快照目录>"
```

快照目录包含 `agents/report-team-lead.md`、`agents/report-memory-agent.md`、`SKILL.md`、`references/memory-orchestration.md`。可省略快照，只测当前版。`CASES` 可指定逗号分隔的用例 ID，`CONCURRENCY` 控制并行数，默认 3。模型固定为本轮验收要求 `hy4-preview-ioa`，不会改变专家默认模型。

另用 `scripts/smoke-memory.mjs "<全新绝对临时目录>"`，在同一 CLI 环境下测试实际写入、重复 Capture 幂等与无效反馈不写入。此脚本拒绝使用已有目录。执行结果须结合文件与来源链人工复核，不能只看完成标记。

测试不读写真实 ReportAgentMemory；禁用宿主自动记忆、MCP 和会话持久化。决策测试无工具，落盘测试仅开放文件工具。结果保存 model、trace、原始返回、usage 和断言，环境错误不计作行为通过。CLI 实测不代表专家团注册和原生调度也已通过。
