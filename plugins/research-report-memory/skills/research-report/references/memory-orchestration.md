# research-report Memory 调度契约

本文件只定义写作流程与 Memory 的协作方式，不定义报告内容，也不得出现在报告正文中。写作方法以 `SKILL.md` 和 `instructions.md` 为准；Memory 的提炼、更新与存储规则以 `research-report-memory-curator` 为准。

## 写前 Recall

- 先完成 research-report 第 0 步的三项需求澄清。全部确认后、开始素材分析和首次写作前，立即通过 Agent/Task 委派 `research-report-memory-curator` 执行 `recall`，传入已确认任务、Audience 和 Project。
- 委派必须是确认后的下一项实际操作；不得只输出“准备召回/接下来召回”后结束，不得由主 Agent 搜索或直调 Recall MCP。
- 只有 Sub-agent 返回 `MEMORY_RECALL_COMPLETED` 才算完成。随后把返回的 Memory Context 与 research-report Skill 一起用于写作，不重复询问已经确认的内容。
- 应用优先级固定为：本轮用户明确要求 > `project` > `audience` > `core` > 本 Skill。L2 是写作上下文，L3 同时作为写作自检清单和本轮 Judge Rubrics；无关、过时或被高优先级要求覆盖的记忆忽略。

## 反馈后 Capture

- 每次用户明确评价报告写法或提出可复用写作要求后，固定按“完成当前修改 → 以 `operation=capture` 委派 `research-report-memory-curator` → Memory Agent 即时 review L0–L3 → 再交付或总结”的顺序执行。普通报告反馈永远走 `capture`；即使它与已有 Memory 冲突，也由 Capture 内的 `update/merge` 原子处理，不得改走 `manage` 或先调用 `forget`。
- 向 Memory Agent 提供本轮反馈、任务、Audience/Project、必要的修改前后语境和修改结果。还必须提供 **L0 原始对话窗口**：从用户正在评价的上一条 Assistant 可见输出开始，到本轮用户反馈结束，逐字复制其中的 user/assistant 消息，不得用摘要替代。通常 2–6 条、最多 8 条；不包含 System Prompt、推理、工具调用或工具结果。若消息过长，只能截断 Assistant 内容，并明确说明省略范围；用户反馈必须完整保留。
- `contextBefore / contextAfter` 是便于提炼的摘要，`conversationExcerpt` 才是用于审计和重新提炼的原始证据，两者不能相互替代。主 Agent 不自行提炼或整理记忆，也不直接管理 L1–L3。
- Memory Agent 判断 `ignored` / `pending` / `stored` / `unchanged`，并在同一次委派中按需更新 L0–L3。若返回 `ACTIVE_RUBRICS`，立即用于本轮后续 Judge，不等待定时维护。
- 交付前检查本轮写作反馈是否已处理。收到 `MEMORY_CAPTURE_COMPLETED` 后再交付；没有写作反馈时不发起 Capture。
- 饮食、爱好、身份等与报告写作无关的反馈不记录。只有用户明确要求查看、纠错、重新分类、合并或删除 Memory 本身时才使用 `manage`；用户明确要求忘记某项写作记忆时，由 `manage` 执行 forget。

## 故障处理

- Memory Sub-agent 必须返回明确的 `MEMORY_RECALL_FAILED` 或 `MEMORY_CAPTURE_FAILED`。
- 发生故障时，主 Agent 如实说明本轮未读取或未写入记忆，然后继续原写作任务；不要自行补写 Memory，也不要把故障说成“没有匹配记忆”。

## 多轮闭环

初稿和后续每轮修改均遵守同一链路：落实写作反馈 → Memory Agent 处理 → 使用最新 Memory/Rubrics 继续写作或 Judge → 交付。
