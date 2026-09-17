# Report Loop implementation architecture

本文供插件开发和故障排查使用，不是宿主 Agent 的执行指令。宿主只需遵守 Skill 中的 `loop-orchestration.md` 执行卡。

## Ownership

- `hooks/capture-checkpoint.mjs`：识别已写入的 Job，在宿主侧异步启动薄 Launcher，并返回可注册为 WorkBuddy 后台任务的等待命令；在 TaskOutput 读取完成或失败结果前阻止会话提前结束。
- `hooks/lib/report-loop-launcher.ts`：薄 Launcher，只校验 Job 路径并启动 Python Runner。被 esbuild 打进 `bin/capture-checkpoint.mjs`，不经 MCP。
- `report_loop/runner.py`：校验 Job、创建运行、串联 Judge 与 Rewrite、处理超时和最终交付。
- `core/memory_rubric_provider.py`：只读 `~/ReportAgentMemory/MEMORY.md` 里的 active L2B，并按 ID 读取引用的 L1 来源。
- `core/memory_store.py`：记忆的 Markdown 读写、`revision` 推进与 `memory-history.md` 追加。
- `core/workspace.py`：报告目录约定——报告标识、`loop-NNN` 序号、交付版本 `vN`、越界拒绝。
- `core/rubric_resolution.py`：运行 Resolution Judge，判断当前任务适用的 Memory Rubrics，必要时请求 `sourceL1`。
- `core/rubric_compiler.py`：将不可修改的 Base Rubrics 与本轮 Resolution Plan 编译并冻结。
- `core/judge_batch.py`：按 Rubric Dimension 隔离、并行运行 Judge。
- `core/judge_prompt.py`：生成 Judge Prompt，声明本轮标准已经冻结。
- `core/report_scoring.py`：按 Rubric 权重计算维度分和总分。
- `core/persistent_rewriter.py`：通过一条长期运行的 WorkBuddy CLI stream 保存改写上下文，每次从历史最佳报告开始修改。
- `core/runtime.py`：保存运行状态、最佳版本、停止条件、失败状态和审计产物。

## Runtime behavior

宿主 Agent 写入 Job 后，PostToolUse Hook 在 Agent 沙箱外启动 Runner，并立即返回唯一结果文件路径和后台等待命令。Agent 以 `Bash(run_in_background=true)` 启动等待命令，因此 WorkBuddy 会分配 `task_id`；任务完成后自动注入 `<task-notification>`，Agent 再通过 `TaskOutput` 读取最终 JSON。等待任务只负责文件就绪通知，不参与 Report Loop。Hook 直接在进程内构造 Launcher 并 spawn Python Runner，不经过 MCP——插件不再声明任何 MCP 服务。Team 型专家加载 `hooks.json` 已与平台侧确认；这是唯一启动路径，构建时校验其完整性。认证、Rubric、Judge 和 Rewrite 逻辑均不进入 Hook 或 Launcher。

Runner 启动后读取 Base Rubrics 以及 `core`、`audience`、`project` 下的独立 Memory Rubric 候选。Provider 不做受众或项目名称的精确匹配；Resolution Judge 根据当前任务语义决定激活项，必要时查看对应 `sourceL1`。随后编译并冻结本轮 Rubric，所有维度 Judge 共用该版本。本轮运行期间发生的 Memory 更新只影响下一轮任务。

每个 Judge round 按有效 Rubric Dimension 启动隔离进程。Judge 模型由 `core/judge_model.py` 单一数据源锁定（默认 `gpt-5.6-sol / medium`，Codex 同）；可经 Job 字段或环境变量覆盖，覆盖与降级都会在结果里标记 `scoresComparableToHistory=false`。传输失败、空响应或非法 Judge JSON 会触发运行级 WorkBuddy host-model 回退，低分本身不会触发回退。

Rewrite 与 Judge 串行。插件 Hook 为 Job 记录准确的 WorkBuddy `sessionId`，Runner 再从该主会话 trace 读取 `requestModelId`；Persistent Rewriter 使用这个实际宿主模型和可选 effort。首轮获得本轮初稿与用户上下文，之后保留已清洗的修改反馈和失败尝试；原始 Judge 输出不会直接传入 Rewriter。

Runner 在最佳版本达到 5 分、连续两个候选版本被拒绝或达到 60 分钟时停止。基础设施故障返回已经完成 Judge 的历史最佳版本，并以 `judge_unavailable` 或 `rewrite_unavailable` 标记。最佳候选通过原子替换发布为 `报告目录/<报告主题>-vN.md`（版本号与直接改写共用一套序号，旧版保留），并在 `版本说明.md` 追加一行记录。内部候选 `RN`、评测明细与日志留在 `报告目录/.report-agent/<报告标识>/loop-NNN-时间戳/`，默认隐藏、不交付。
