# Report Loop Memory 调度契约

本文件只规定用户反馈如何进入长期写作记忆，不定义报告写法，也不得进入报告正文。

## 系统身份

- 本契约中的 Memory 专指 `report-memory-v2`：L0 Writing Episode、L1 Atom Memory、L2B Rubrics Memory。
- Memory 是持久化用户级功能，默认启用。只有用户明确要求查询、关闭或重新开启时，主 Agent 才可直接调用 Memory MCP 的 `writing_memory_settings(action=status|enable|disable)`；这是主 Agent 唯一可直接调用的 Memory 工具。关闭不会删除已有记忆。
- WorkBuddy 的 `~/.workbuddy/MEMORY.md`、项目 `.workbuddy/memory/**` 和工作日志是宿主原生记忆，不属于本插件，也不能替代本插件 Capture。
- Memory 已开启时，用户明确评价报告写法或提出可复用要求即应 Capture，无需用户额外说“记住”。实时 Capture 只能由宿主通过 Agent/Task 委派 `research-report-memory-curator` 完成；Memory 关闭时不 Capture。

## 写作与 Judge

- 写作前不执行 Memory Recall。宿主 Agent只按本轮用户要求和 `research-report-loop` Skill 写初稿。
- Memory 关闭时，Python Runner 只使用 Base Rubrics，不读取 L2B，也不调用 Resolution Judge 解释 Memory；Memory 开启后才读取候选并冻结本轮标准。
- L2B 变化时，Memory Curator 只维护独立的 Git-backed Memory Rubrics，不修改 Base，也不为 Audience×Project 组合保存完整副本。
- Python Runner 读取 Base 以及 `core / audience / project` Memory Rubric 候选；Provider 不按受众或项目名称做精确匹配。Resolution Judge 根据本轮任务语义判断激活项，必要时读取对应的 `sourceL1`，随后冻结 Resolution Plan 与 compiled rubric，供六维 Judge 共同使用。
- 同一 Loop 中 Memory 即使更新，也不改变已经冻结的 Rubrics；下一次新建 Loop 才读取新 Revision。

## 用户反馈后 Capture

- 仅在 Memory 已开启，且用户明确评价报告写法、直接修改报告或提出写作要求时触发。Judge 反馈、Judge 分数、Agent 自评和自动改写不得触发。
- 固定顺序：直接修改当前报告 → 确认文件修改成功 → 通过 Agent/Task 委派 `research-report-memory-curator` 执行 `operation=capture` → 再交付或总结。反馈修订不重新运行 Report Loop，除非用户明确要求重新评测。
- 向 Curator 提供当前反馈、Task、Audience/Project、必要的修改前后内容，以及从用户正在评价的上一条 Assistant 可见输出到当前用户反馈的 L0 对话窗口。通常 2–6 条、最多 8 条；用户反馈不得截断。
- 主 Agent 不决定 Layer/Scope，不直接调用 Memory MCP，不直接维护 L1/L2B，也不因一次反馈宣称 Rubric 已形成。
- 普通反馈一律走 Capture。即使与旧记忆冲突，也由 Curator 在一次 Payload 中更新或合并；Capture 期间不得改走 Manage 或先调用 Forget。
- 成功结束标记为 `MEMORY_CAPTURE_COMPLETED`；失败标记为 `MEMORY_CAPTURE_FAILED: <reason>`。失败时如实说明，不得把 L0 落盘描述成 L2B 已更新。

## 不可变系统资产

- 写作反馈只能修改当前报告及用户明确指定的交付文件，不得修改已安装插件中的 `skills/**`、`rubrics/**`、`dist/**`、`hooks/**`、`mcp/**`、Manifest、脚本或 README。
- “以后都这样写”表示应由 Curator 判断是否沉淀为长期 Memory，不表示授权修改 Skill 或 Base Rubric。
- 不得把 `~/.workbuddy/MEMORY.md` 或项目 `.workbuddy/memory/**` 当作 Capture 失败后的备用写入通道。
- 插件故障只报告明确错误。只有用户明确提出开发或调试插件时，才在源码仓库修复并发布新版本；不得热改安装副本或自行重启 Connector。

## Memory 管理

- 用户明确要求开启、关闭或查询状态时，由主 Agent 调用 `writing_memory_settings`；普通反馈不改变当前开关状态。
- 只有用户明确要求查看、纠错、重新分类、合并或删除 Memory 本身时，才委派 `operation=manage`。
- 即使 Memory 已关闭，用户仍可显式查看、纠错或删除已有记忆；Curator 使用 `purpose=manage` Recall，不会让这些记忆参与写作或 Judge。
- 用户明确要求忘记某项写作记忆时，由 Curator 核验后执行 Forget。主 Agent不得自行删除或改写 Memory。

## 优先级

- 本轮用户明确要求决定当前交付目标。
- Base Rubrics 的红线与硬门槛不能被 Memory 删除或弱化。
- Scope 候选从 `core + audience + project` 文档读取；受众/项目名称是否同义、语义重复、适用场景和冲突均由 Resolution Judge 结合任务解释，六维 Judge 不再各自重解释 Memory。
- 当前任务的 Audience/Project 只作为 Resolution Judge 的判断上下文，不能反推已有记忆或新反馈的 Scope；Scope 仍由 Curator 根据反馈语义判断。
