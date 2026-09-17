# Report Loop V2 Memory 调度契约

本文件规定长期写作记忆的位置与调用方式，不定义报告写法，也不得进入报告正文。

## 系统身份

- 本契约中的 Memory 专指 `report-memory-agent` 管理的 L0 Writing Episode、L1 Atom Memory 和 L2B Memory Rubrics。
- Memory 是持久化用户级功能，默认启用。用户明确要求查询、关闭或重新开启时，主 Agent 委派 `report-memory-agent operation=settings`；关闭不会删除已有记忆。
- WorkBuddy 的通用用户 Memory、项目 Memory 和工作日志不属于本 Expert，也不能替代 Capture。
- Memory 已开启时，用户明确评价报告写法或提出可复用要求即应 Capture，无需用户额外说“记住”。

## 记忆位置

主 Agent 从系统取得当前用户主目录（macOS 使用 `HOME`，Windows 使用 `USERPROFILE`，也可用系统的 home API），将其中的 `ReportAgentMemory` 解析为绝对路径 `memoryRoot`，传给每一次 Memory 调用，包括 resolve、inspect_sources、capture、manage、reflect 和 settings。不传未展开的环境变量或 `~`，不从当前工作区推算主目录。

这是用户可见、跨项目和宿主共用的真实文件夹，不位于 WorkBuddy、插件或报告工作区内。目录的初始化和读写由 Memory Agent 负责；无法确定或访问时说明原因，不静默换一个位置。切换宿主时沿用同一路径，不另建记忆副本。

### Phase 0 可写性探针

记忆目录在报告工作区之外，可能落在宿主可写范围之外。**必须在真正用到记忆之前确认可写**——若等到 capture 才发现写不了，那一轮的用户反馈就已经丢了：

```text
python3 "<资源目录>/report/memory_access.py" probe
```

- `MEMORY_ACCESS_OK`：照常进行，把返回的 `memoryRoot` 传给每一次 Memory 调用。
- `MEMORY_ACCESS_NEEDS_AUTHORIZATION`：用 `AskUserQuestion` 按返回的 `suggestedPrompt` 和 `options` 询问用户，三个选项分别是授权该目录、改用其他可写目录、本轮不启用记忆。

用户选择改用其他位置时记录下来，后续会话不再重复询问：

```text
python3 "<资源目录>/report/memory_access.py" set-root --path "<用户给的绝对路径>"
```

用户选择本轮不启用记忆时，按 Memory 关闭处理：resolve 不返回候选、capture/reflect 不写入，只用 Base Rubrics 评测，并在交付时说明本轮未使用记忆。不要反复询问，也不要把写入失败当作记忆已更新。

目录包含 `MEMORY.md`（设置、revision 及生效 L2B）、`memory-history.md`（实际变更记录）、`L0-episodes/` 和 `L1-atoms/`。不维护全量索引；历史仅按需查阅，不进入常规评测，不提供自动回滚。旧目录不自动删除。

## 写作与 Judge

- 写作前不执行面向主 Agent 的 Memory Recall。Writer 只按本轮用户要求和写作规则完成 R0。
- Memory 关闭时，Resolution 只使用 Base Rubrics；开启后才读取 L2B 候选。
- L2B 只维护独立 Memory Rubrics，不修改 Base，也不预先决定 Dimension。
- Resolution Judge 根据当前任务判断激活、合并或新增哪些维度；只有它明确请求时，才由 Memory Agent 按准确 `sourceL1Ids` 返回 L1 来源，然后冻结本轮标准。
- 同一 Loop 中 Memory 即使发生变化，也不改变已经冻结的 Resolution Plan；下一次新建 Loop 才读取新 revision。

## 用户反馈后 Capture

- 新增资料、数据纠错和论据更新先按 [evidence-orchestration.md](evidence-orchestration.md) 处理，不作为写作偏好 Capture；混合反馈只向 Memory Agent 传递其中的写作要求及必要语境，不把事实清洗结果当成用户长期偏好。
- 仅在 Memory 已开启，且用户明确表达写作要求、偏好或评价时触发。上下文、报告内容和修改差异仅用于理解用户表达，不能代替用户表达；Agent 自己的分析、建议和评测结论不作为用户记忆来源。没有明确的用户写作反馈时，不调用 Capture。
- 固定顺序：按 [Writer 反馈分流](writer-orchestration.md#反馈分流) 完成直接 Rewrite 或新 Loop → 主 Agent 核验报告 → 委派 `report-memory-agent operation=capture` → 再交付或总结。重大修改也只针对本次用户反馈 Capture 一次，不把 Loop 的自动改写或 Judge 意见当成新反馈。
- 为本次反馈生成一个稳定 `captureId`，重试时必须复用；向 Memory Agent 提供该 ID、用户反馈原文、task、audience/project、必要的修改前后内容，以及用户正在评价的上一条 Assistant 可见输出至当前反馈的对话窗口；通常 2–6 条、最多 8 条，用户反馈不得截断。用户通过选项回答时，同时传递原问题、选项及实际选择，区分 Agent 建议与用户表达。
- 委派只传用户原文与必要语境，不预先概括长期偏好或指定 Layer、Scope、Rubric。Memory Agent 先核验要求是否来自用户，再判断是本次适用还是值得长期生效；主 Agent 按实际结果汇报，不把系统推断称为用户要求。
- 普通报告写作反馈一律走 Capture。即使与已有记忆冲突，也由 Memory Agent 在同一次 Capture 中更新、合并或保持不变；Capture 期间不得改走 Manage，也不得先删除旧记忆。
- 成功结束标记为 `MEMORY_CAPTURE_COMPLETED`；相同 `captureId` 的幂等返回也视为同一次成功。失败标记为 `MEMORY_CAPTURE_FAILED: <reason>`。失败时如实说明，不得把 L0 保存描述成 L2B 已更新，也不得更换 `captureId` 反复提交。

## 不可变资产

- 写作反馈只能修改当前报告及用户明确指定的交付文件，不得修改已安装 Expert 中的 Skill、Base Rubrics、Agent Prompt、Manifest 或 README。
- “以后都这样写”表示应由 Memory Agent 判断是否沉淀为长期 Memory，不表示授权修改 Skill 或 Base Rubrics。
- Capture 失败时，不得写入 WorkBuddy 通用用户或项目 Memory 作为备用通道。
- 只有用户明确提出开发或调试 Expert 时，才允许在源码仓库修复；不得热改安装副本。

## Memory 管理

- 用户明确要求开启、关闭或查询状态时，委派 `operation=settings`；普通反馈不改变当前开关状态。
- 用户明确要求查看、纠错、重新分类、合并或删除 Memory 本身时，委派 `operation=manage`。
- 用户要求立即整理时，委派 `operation=reflect`。
- Memory Agent 在每天 16:30 后首次被调用时补做当日 Reflection；无需主 Agent另行启动后台任务，同一天不得重复。
- 即使自动 Memory 已关闭，用户仍可显式查看、纠错或删除已有记忆。
- 用户明确要求忘记某项写作记忆时，由 Memory Agent 先核验目标及来源，再删除或失效对应项并更新 revision；主 Agent不得直接删除、改写或猜测目标。

## 优先级

- 本轮用户明确要求决定当前交付目标。
- Base Rubrics 的事实真实性红线和硬门槛不能被 Memory 删除或弱化。
- Memory Rubrics 的适用场景、语义重复和冲突由 Resolution Judge 结合任务解释；各 Dimension Judge 不再自行解释。
- 当前任务的 audience/project 只是判断上下文，不能反推新反馈的 Scope。
