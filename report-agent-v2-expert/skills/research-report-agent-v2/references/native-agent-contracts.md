# Native Sub-agent 调用契约

本文件用于开发和调试 V2；正常写作只需按 `SKILL.md` 的步骤调用。

| Sub-agent | 输入 | 输出标记或 Schema | 是否写文件 |
|---|---|---|---|
| `report-memory-agent-v2` | operation + task/audience/project + feedback/context/revision | `MEMORY_*` | 只写自己的 Agent Memory |
| `report-resolution-judge-v2` | Base Rubrics + Memory candidates + task + 可选 L1 证据 | `needs_source` 或最终 Resolution Plan JSON | 否 |
| `report-dimension-judge-v2` | 一个冻结 Dimension + report + materials | 覆盖全部 Check 的 Dimension Result JSON | 否 |
| `report-rewriter-v2` | best report + plan + revisionBrief + target path | `REPORT_REWRITE_COMPLETED` | 只写目标候选版本 |

主 Agent负责保存运行状态和 Resolution Plan、按固定公式聚合分数、执行候选采纳门槛、选择历史最佳版本和最终交付。子代理之间不直接互相调用，也不共享隐式会话上下文；主 Agent必须传入完成任务所需的最小信息或文件路径。

Resolution 的可选溯源最多一轮：Resolution Judge 返回候选 ID，主 Agent调用 Memory Agent 的 `inspect_sources`，再把准确 L1 证据交回 Resolution Judge。Dimension Judge 不能请求 Memory，也不能给出最终分数。

Memory Agent 的 `memory: user` 是唯一长期状态。其他三个子代理均为无状态认知组件。
