# Report Loop implementation architecture

本文供插件开发和故障排查使用，不是宿主 Agent 的执行指令。宿主只需遵守 Skill 中的 `loop-orchestration.md` 执行卡。

## Ownership

- `mcp/report_loop/runner.py`：校验 Job、创建运行、串联 Judge 与 Rewrite、处理超时和最终交付。
- `core/memory_rubric_provider.py`：读取 Base Rubrics 对应的 core、audience、project Memory Rubrics，并按需读取引用的 L1 来源。
- `core/rubric_resolution.py`：运行 Resolution Judge，判断当前任务适用的 Memory Rubrics，必要时请求 `sourceL1`。
- `core/rubric_compiler.py`：将不可修改的 Base Rubrics 与本轮 Resolution Plan 编译并冻结。
- `core/judge_batch.py`：按 Rubric Dimension 隔离、并行运行 Judge。
- `core/judge_prompt.py`：生成 Judge Prompt，声明本轮标准已经冻结。
- `core/report_scoring.py`：按 Rubric 权重计算维度分和总分。
- `core/persistent_rewriter.py`：通过一条长期运行的 WorkBuddy CLI stream 保存改写上下文，每次从历史最佳报告开始修改。
- `core/runtime.py`：保存运行状态、最佳版本、停止条件、失败状态和审计产物。

## Runtime behavior

Runner 启动后读取 Base Rubrics 以及与 `core`、`audience`、`project` 匹配的独立 Memory Rubrics。Resolution Judge 根据当前任务决定激活项，必要时查看对应 `sourceL1`；随后编译并冻结本轮 Rubric，所有维度 Judge 共用该版本。本轮运行期间发生的 Memory 更新只影响下一轮任务。

每个 Judge round 按有效 Rubric Dimension 启动隔离进程。WorkBuddy Provider 固定使用 `deepseek-v4-pro / medium`；Codex Provider 固定使用 `gpt-5.6-sol / medium`。传输失败、空响应或非法 Judge JSON 会触发运行级 WorkBuddy host-model 回退，低分本身不会触发回退。

Rewrite 与 Judge 串行。Persistent Rewriter 使用 Job 中的 `hostModel.modelId` 和可选 effort，首轮获得 V1 与用户上下文，之后保留已清洗的修改反馈和失败尝试；原始 Judge 输出不会直接传入 Rewriter。

Runner 在最佳版本达到 5 分、连续两个候选版本被拒绝或达到 60 分钟时停止。基础设施故障返回已经完成 Judge 的历史最佳版本，并以 `judge_unavailable` 或 `rewrite_unavailable` 标记。最终文件通过原子替换写入 Job 的 `outputPath`。
