# Report Memory 调度契约

本文件只规定用户反馈如何进入长期写作记忆，不定义报告写法，也不得进入报告正文。

## 系统身份

- 本契约中的 Memory 专指 `report-memory-agent` 维护的三层结构：L0 Writing Episode、L1 Atom Memory、L2B Memory Rubrics。
- 记忆存放在**用户主目录下可见的** `ReportAgentMemory/`，全部为 Markdown，与宿主产品和插件安装目录无关。用户可以直接查看和修改这些文件。
- `MEMORY.md` 顶部的 `revision: N` 是唯一版本号，用于识别当前状态；`memory-history.md` 按 revision 追加变更记录，**不提供历史回滚**。
- **不维护全量 L0/L1 索引或 MEMORY 快照**：按 ID 在对应目录查找，依靠 L2B → sourceL1Ids → L1 → sourceEpisodeIds → L0 的来源链回溯。
- WorkBuddy 的 `~/.workbuddy/MEMORY.md`、项目 `.workbuddy/memory/**` 和工作日志是宿主原生记忆，不属于本专家团，也不能替代本记忆。
- Memory 默认启用。主理人不直接读写记忆文件，也不自行判断 Layer 或 Scope——所有记忆操作都委派 `report-memory-agent`。

## 写作与 Judge

- 写作前不由写作成员 Recall 记忆。需要本轮评测标准时，由主理人调度 `operation=resolve`，得到 `revision` 与最小必要候选（只含 L2B 与准确的 `sourceL1Ids`，不展开 L1 原文）。
- Memory 关闭时 resolve 返回 `candidates=[]`，Report Loop 只使用 Base Rubrics，已有文件保留不变。
- Resolution Judge 首轮需要精确溯源时，调度 `operation=inspect_sources`，传入本轮 resolve 返回的 `revision`、候选 ID 和待读取的 `sourceL1Ids`。`revision` 已变化时返回 `MEMORY_SOURCE_CONFLICT`，须重新 resolve，**不混用版本**。
- L2B 只新增独立的 Memory Rubrics，不修改 Base Rubrics；Base 的红线与硬门槛不能被记忆删除或弱化。
- 同一 Loop 中记忆即使更新，也不改变已经冻结的 Rubrics；下一次新建 Loop 才读取新 revision。
- 可选的 `dimensionCandidate` 只是供 Resolution Judge 参考的新维度建议，不直接改变 Judge 结构或权重。

## 用户反馈后 Capture

- 仅在 Memory 已开启，且用户明确评价报告写法、直接修改报告或提出写作要求时触发。**Judge 反馈、Judge 分数、Agent 自评和自动改写不得触发。**
- 固定顺序：先由 `report-reviewer` 修改当前报告 → 确认文件修改成功 → 调度 `report-memory-agent` 执行 `operation=capture` → 再交付或总结。反馈修订不重新运行 Report Loop，除非用户明确要求重新评测。
- 向该成员提供稳定的 `captureId`（重试时复用）、当前反馈、修改前后内容、task、audience/project，以及从用户正在评价的上一条 Assistant 可见输出到当前用户反馈的对话窗口（通常 2–6 条、最多 8 条；用户反馈不得截断）。
- 没有明确的用户写作反馈时，该成员返回 `status=unchanged`、`episodeId=null`，不创建 L0/L1/L2B、不推进 revision——这是正常结果。
- 普通反馈一律走 Capture。即使与旧记忆冲突，也由该成员在本次 Capture 内合并、更新或保持不变；**不改走 Manage，也不先删除旧项**。
- 成功标记为 `MEMORY_CAPTURE_COMPLETED`；失败标记为 `MEMORY_CAPTURE_FAILED: <reason>`。失败时如实说明，不得把 L0 落盘描述成 L2B 已更新。

## Reflection

- **不依赖后台定时任务。** `report-memory-agent` 在 capture 等操作开始时检查 `lastReflectionAt`：当地时间已过 16:30 且今天尚未复盘，或上一个自然日仍未复盘时，先执行一次 Reflection 再继续原操作；同一天不重复。
- 长时间未使用时在下一次调用补做，不为补齐空闲日期逐日运行。
- Reflection 复盘尚未处理或上次 Capture 中断的 Episodes，合并重复、修正冲突和 Scope、剔除过时项并精简 L2B。记忆内容无变化时返回 `MEMORY_REFLECTION_COMPLETED status=unchanged`；复盘时间的元数据更新不意味着形成了新 Rubric，也不推进 revision。

## 不可变系统资产

- 写作反馈只能修改当前报告及用户明确指定的交付文件，不得修改已安装插件中的 `skills/**`、`rubrics/**`、`report_loop/**`、`resources/**`、`dist/**`、`bin/**`、Manifest、脚本或 README。
- “以后都这样写”表示应由记忆管理员判断是否沉淀为长期 Memory，不表示授权修改 Skill 或 Base Rubric。
- 不得把 `~/.workbuddy/MEMORY.md` 或项目 `.workbuddy/memory/**` 当作 Capture 失败后的备用写入通道。
- 插件故障只报告明确错误。只有用户明确提出开发或调试插件时，才在源码仓库修复并发布新版本；不得热改安装副本。

## Memory 管理

- 用户明确要求开启、关闭或查询状态时，由主理人调度 `operation=settings`（`status|enable|disable`）；普通反馈不改变当前开关状态。
- 只有用户明确要求查看、纠错、重新分类、合并或删除 Memory 本身时，才调度 `operation=manage`。
- 即使 Memory 已关闭，用户仍可显式查看、纠错或删除已有记忆；这些操作不会让记忆参与写作或 Judge。
- 用户明确要求忘记某项写作记忆时，由记忆管理员核验具体目标和来源后执行；**主理人不得代为判断，也不得自行删除或改写记忆文件**。

## 优先级

- 本轮用户明确要求决定当前交付目标。
- Base Rubrics 的红线与硬门槛不能被 Memory 删除或弱化。
- Scope 为 `core / audience / project` 三选一：跨项目、受众仍成立为 `core`；因特定受众或沟通环境才成立为 `audience`；换项目即失效为 `project`。
- 当前任务的 audience/project 元数据只作为 Resolution Judge 的判断上下文，**不能反推已有记忆或新反馈的 Scope**；Scope 由记忆管理员根据反馈语义判断。
