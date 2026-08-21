# Changelog

## 2.0.0-mvp.35

- Maintenance Recall 只返回 pending Episode、dirty target、疑似重复/冲突 L1 及直接相关的 L2/L3 Item，不再暴露全量记忆快照。
- 没有待处理内容时返回 `noWork=true`，Curator 直接结束，不再空跑 Capture。
- 新增 `documentPatches` 增量接口，按 Item 执行 upsert/remove；新 Capture 与 Maintenance 不再回写整份 L2/L3 文档。
- Snapshot 冲突校验改为只计算存储 revision，不再内部构造全量 Maintenance Snapshot。
- 增加增量文档更新与精简 Maintenance 工作集回归测试。

## 2.0.0-mvp.34

- 普通报告反馈固定委派 `operation=capture`；只有用户明确管理 Memory 本身时才走 `manage`。
- Capture 期间禁止单独调用 `forget`，冲突记忆统一通过同一 Payload 的 `update/merge + targetIds` 原子替换。
- Hook 区分新报告与已有报告修改：修改反馈只触发 Capture，不额外触发 Recall；明确新报告仍保留写前 Recall。
- 增加新报告、修改反馈、延迟 Skill 激活、Memory 管理及 Recall 状态保护测试。

## 2.0.0-mvp.33

- 明确 L1 `update/merge` 必须使用复数数组 `targetIds`，禁止用 `id` 或 `targetId` 代替。
- 为 Curator 增加完整更新示例，并在 `target_ids_required` 时返回可直接修正的字段提示。
- 增加 MCP 与 Prompt 契约回归测试，防止更新型 Capture 再次反复试错。

## 2.0.0-mvp.32

- L0 从“反馈前后摘要”升级为可审计的原始反馈事件窗口：逐字保存 2–6 条、最多 8 条 user/assistant 消息。
- Feedback Capture 强制最后一条原始消息与根字段 `feedback` 完全一致，并要求此前至少有一条 Assistant 输出；摘要字段不再能代替原始对话。
- 原始对话最多 40,000 字符；只允许截断 Assistant 内容，必须显式记录截断与省略原因。新 Episode 标记 `episodeSchemaVersion: 2`，旧 Episode 保持可读。
- 旧 Episode 可通过相同 `externalSourceId` 的 Capture 安全补录缺失原始对话；只填空缺字段，不覆盖已有对话，也不重复更新 L1/L2/L3。

## 2.0.0-mvp.31

- 为 Memory Curator 增加严格的 Feedback Capture 标准 Payload，明确使用 `episode / memories / rule / scopeValue`，且 Episode 由同一次 Capture 自动创建。
- Payload 顶层拒绝 `episodes / l1Memories` 等未知字段，不再静默丢弃后进入误导性的业务错误。
- 缺少 `episode.task` 时返回 `episode_task_missing_in_payload` 和可执行提示，并增加真实 MCP 回归测试。

## 2.0.0-mvp.30

- 更新 research-report Skill 与完整写作 instructions，保留新版三段式结构、主张—证据核验和六类硬规则。
- 将 Recall/Capture 调度从写作 Skill 正文拆到独立 `memory-orchestration.md`，Hook 继续提供确定性流程检查。
- 新增写作 Skill 同步脚本：保存纯上游版本并自动合成轻量 Memory Overlay，后续升级无需手工回填调度规则。

## 2.0.0-mvp.29

- 参考 Letta 的长期学习定位，明确 Curator 的目标不是保存更多内容，而是让未来报告写作、自检和 Judge 更符合用户要求。
- 将“是否会改变未来写作或质量判断”设为记忆价值标准，并明确 L2/L3 是稀缺上下文，只保留稳定、可执行、已整合的当前有效内容。
- 更新时优先 `skip / update / merge`，没有合适承载位置时才 `create`；历史交由 L0 和 Git 保留，不在 L2/L3 中堆积重复旧版。
- 保持专用 Memory 的权限边界：Curator 不修改 Skill、Hook 或宿主配置，不引入 Letta 的自治身份和 Harness 修改能力。

## 2.0.0-mvp.28

- 明确 Scope 是 L1–L3 共同的横向分类，不是 L1 Atom 的专属属性。
- Scope 在 L1 首次确定，L2/L3 继承同 Scope，并在合并、更新或晋升时复核；Scope 更正时同步迁移 L1→L2→L3 链路。
- Scope 判定流程的对象改为“每条进入长期记忆的写作要求”，避免误解为只处理 L1。

## 2.0.0-mvp.27

- 明确 L1 保留为精简的原子证据层：保存单条可核验写作要求及其 Scope 和来源，支撑 L2/L3 与回查，不默认注入写作上下文。
- 将 Scope 判定的主规则和“补充约束”合并为一套连续流程，统一处理会话包装、反事实判断、重复证据、不确定项和重分类。
- 增加 Prompt 契约测试，防止 L1 定位退化或 Scope 规则再次拆成补充段落。

## 2.0.0-mvp.26

- 在分类总览中直接说明三个 Scope 的用途：Core 常驻且跨项目生效，Audience 面向特定受众和汇报环境，Project 面向特定项目。
- 增加契约测试，防止 Scope 简介在运行 Prompt 与语义模板之间漂移。

## 2.0.0-mvp.25

- 为 Gate 0–3 各增加一组报告写作 Good case / Bad case，明确什么情况通过以及什么情况停止继续加工。
- 示例覆盖纯操作授权、一次性段落调整、稳定项目口径和不可判定的主观 Rubric，帮助 Memory Agent 识别边界。
- 增加契约测试，要求运行 Prompt 与语义模板的每个 Gate 都同时包含正反例。

## 2.0.0-mvp.24

- 将 Layer 判定从四层表格改为 L0→L1→L2→L3 的渐进式 Gate 链路，每个 Gate 明确通过条件、停止条件和后续动作。
- L2 使用“整合价值”而非单纯“抽象增量”，兼容稳定的 Project 背景、口径和约束；L3 继续以可观察、可判定的 Judge 标准为门槛。
- 明确进入更高层不等于新增内容，每层先执行 skip/update/merge/create 判断；L2 与 L3 使用不同门槛。

## 2.0.0-mvp.23

- 重构 Memory Curator Prompt 顺序：分类总览 → Scope → Layer → 冲突更新 → Operation → MCP 契约，避免在 Scope/Layer 规则之间穿插调用细节。
- 合并开头重复说明，改为一段简洁的 Layer × Scope 定义；Scope 强调适用范围，Layer 只说明加工用途。
- 同步精简宿主无关的 Memory Agent System Prompt，并增加 Prompt 顺序契约测试。

## 2.0.0-mvp.22

- Maintenance 为当前 V2 Server 生成包含绝对路径的独立 MCP 配置，并使用 `--strict-mcp-config` 启动 WB CLI。
- 定时任务不再依赖 `--plugin-dir` 或全局 Marketplace 发现 MCP，也不再加载旧版 Memory MCP，从根本上避免工具名碰撞。

## 2.0.0-mvp.21

- 定时/手动 Maintenance 启动 WB CLI 时通过 `--plugin-dir` 显式加载当前 V2 插件，不再依赖 CLI 对本地 Marketplace 的自动发现。
- Maintenance 任务明确只允许调用 `research-report-memory-v2-mvp` MCP，禁止误调旧版同名工具。
- Maintenance 任务显式要求同时复查 Layer 与 Scope，而不只处理 dirty Profile。

## 2.0.0-mvp.20

- Prompt 明确“Scope 是三选一，Layer 不是四选一”，同一条有效反馈可形成 L0→L1，并由 L1 分别支持 L2/L3 的纵向处理链。
- 新增 Layer 判定表，分别定义 L0–L3 的核心判断、正向证据、排除条件和示例。
- L2 必须相对 L1 产生抽象增量和写作指导价值；L3 必须重要、稳定、可观察、可判定，并区分 `candidate/active`。

## 2.0.0-mvp.19

- Memory Agent Prompt 在开头明确使用 `Layer × Scope`：纵向 L0–L3 表示加工层级，横向 Core/Audience/Project 表示适用范围。
- 新增 Recall 矩阵：L2/L3 Core 默认读取，Audience/Project 按需读取；L1 默认不暴露给写作 Agent，L0 仅用于审计与重提炼。
- 明确 System Prompt 模板是宿主无关的语义母版，Curator 是 WorkBuddy 实际加载的运行版，两者不会同时叠加进模型上下文。

## 2.0.0-mvp.18

- Memory Agent 使用统一 Scope 判断表：通用可复用写作规则默认归 Core，Audience/Project 必须有正向限定证据。
- “这份报告”、当前任务 Audience/Project 只作为反馈语境，不再被当作 Scope；Capture 与 Maintenance 均执行 Atom 拆分、去语境包装和换项目/换受众反事实测试。
- Project 明确用于项目背景、口径、目标、关键结论、前因后果和项目内部长期约束；一次性要求继续留在 L0。

## 2.0.0-mvp.17

- Capture 计划没有写入/匹配 L1 且没有改变 L2/L3 时返回可重试的 `capture_plan_no_effect`，不再把空结果 Episode 提前标记为 promoted。
- L2/L3 只能引用相同 Scope 的 active L1，阻止 Audience L1 被直接挂入 Core 文档。
- Memory Agent 明确使用 `operationRef -> new:<operationRef>` 契约；`extractor` 继续由 Runtime 自动生成。

## 2.0.0-mvp.16

- Memory Agent 按每个 L1 Atom 的实际适用范围判断 Scope；任务中的 Audience/Project 仅作为语境与 Recall 路由，不再被当作分类证据。
- audience/core 需要用户明确适用性表达或多个独立项目的重复证据；证据不足时默认落 project 或只保留 L0。
- Maintenance/Manage 发现 Scope 过宽或过窄时，应重新分类并同步更新 L2/L3。

## 2.0.0-mvp.15

- V2 使用专属环境变量 `RESEARCH_REPORT_MEMORY_V2_DIR`；继续兼容手动运行时的旧变量。
- 定时 Maintenance 不再把 V2 数据目录注入旧版 `research-report-memory` MCP，避免旧 MCP 在 V2 根目录创建空布局并阻断启动。

## 2.0.0-mvp.14

- 曾加入面向特定内部受众的轻量 Audience 别名兼容；对外分享版本已移除该业务定制，改为使用宿主传入的受众名称。

## 2.0.0-mvp.13

- 默认用户入口从 `~/Documents/Research Report Memory` 改为跨平台的 `~/Research Report Memory`，Windows 对应 `%USERPROFILE%\\Research Report Memory`。
- 不使用 `~/.workbuddy`，保持 Memory 与 WorkBuddy、Codex、Claude Code 等宿主解耦。
- 升级时仅删除目标完全匹配的 mvp.12 Documents 符号链接；同名用户文件、目录或其他链接不会修改。

## 2.0.0-mvp.12

- 默认在用户文档目录创建 `Research Report Memory` 快捷方式，指向 L2/L3 Git-backed Markdown Repository。
- 快捷方式创建为幂等、非覆盖操作；同名文件或目录存在、权限不足时跳过，不影响 Memory 主流程。
- 自定义数据目录默认不创建用户快捷方式，可通过 `RESEARCH_REPORT_MEMORY_SHORTCUT` 指定路径，或设为 `0` 禁用。

## 2.0.0-mvp.11

- L0/L1 统一收进 `l0-l1-memory/`：完整 Episode 位于 `l0-episodes/`，L1 JSONL 位于 `l1-atoms/`，MemoryCore SQLite 位于 `memorycore/`。
- 取消 L0 Episode 的 SQLite 双写；完整 Episode JSON 成为 L0 唯一权威数据，MemoryCore 从 L1 开始承担存储与检索。
- `repositories/` 更名为 `l2-l3-memory/`，Git-backed Markdown 与临时 Worktree 统一归入该目录。
- 新版本启动时自动迁移 mvp.10 目录，并清除 SQLite 中旧的 L0 镜像，不影响 L0 JSON、L1 或 Git 历史。

## 2.0.0-mvp.10

- 产品级目录明确标注 L0/L1：完整 Episode 改为 `l0-writing-episodes/`，TencentDB 内部数据统一收进 `memorycore-l0-l1/`。
- 保留 MemoryCore 固定的 `conversations / records / scene_blocks / vectors.db` 契约，不 fork 上游依赖；新增旧目录自动迁移。
- 补充各运行目录的用途说明，并验证新安装和旧布局迁移。

## 2.0.0-mvp.9

- L2/L3 改为可见 Markdown 单一权威数据；解析器直接读取 Item 标题、正文、Rules、Criterion、Pass、Fail 和 Status。
- 删除隐藏的完整 `memory-item` JSON，仅用 `## <id>` 与 `<!-- sources: ... -->` 保留必要 ID 和来源。
- 所有 Scope 统一使用 `l2-context.md` / `l3-rubrics.md`；初始化时自动迁移旧文件名与旧格式，并用 Git Commit 保留迁移历史。

## 2.0.0-mvp.8

- Memory Guard 收敛为结果门禁：只检查命名 Curator 的 Recall/Capture 成功或失败标记，不再识别 MCP 调用方或阻止直接调用。
- 移除 `writing_memory_recover` 产品入口和宿主 Recovery/candidate 流程；Memory 故障由 Curator 显式返回 `*_FAILED`，Hook 如实提示后放行原写作任务。
- 取消 SessionEnd Hook，避免 WorkBuddy Sub-agent 共享父 `session_id` 时误删主会话状态。

## 2.0.0-mvp.7

- Memory Guard 使用 WorkBuddy Hook 的 `agent_type / agent_id` 识别 `research-report-memory-curator`，放行其 Recall/Capture MCP 调用，同时继续阻止主写作 Agent 越权。
- Curator 的 Hook 生命周期不再误删或污染共享的主会话 Guard 状态。

## 2.0.0-mvp.6

- 删除交付阶段的 OpenHarness 平台提示，使 research-report Skill 对普通用户保持产品无关。

## 2.0.0-mvp.5

- 新增 `writing_memory_capture_payload`：Memory Curator 只传一个 JSON 字符串，既避免 WorkBuddy 破坏 `episode / memories / documents` 嵌套参数，也保持自定义 Agent 可注册。
- 新增 Hook 授权的 `writing_memory_recover`：仅在 Curator 明确失败后开放，宿主只能暂存 L0 与 project L1 candidate。
- Recovery candidate 默认不进入写作 Recall/Judge；后续须由 Memory Agent 复核并显式晋升为 active。
- PreToolUse 在主会话阻止直调正式 Memory MCP，并在 `present_files` 前强制完成 Capture 或受控 Recovery。
- 单次反馈默认按 project 处理；audience/core 需要明确长期表达或多个独立 Episode 证据。
- 增加反馈幂等 ID 和 Capture 计划前置校验，避免重试产生重复 L0，以及无效文档引用在 L1 更新后才失败。

## 2.0.0-mvp.3

- 移除 L1–L3 的 Dimension 字段，记忆只按 `core / audience / project` Scope 组织；兼容读取带旧字段的 V2 L1 数据。
- 每次写作反馈先读取相关 Scope 快照，再即时 review 并按需 Commit L2 Context 与 L3 Rubrics。
- Capture 返回当前 `activeRubrics` 和 Memory Context，供本轮 Report Loop 继续 Judge。
- 定时 Maintenance 改为跨会话深度治理和失败兜底，不再是 L2/L3 正常更新的唯一入口。
- Recall 不再按 Dimension 整组遮蔽低优先级 Scope；非冲突的 core、audience、project 规则同时返回。

## 2.0.0-mvp.2

- 新增 `recallDueNow` 状态，区分“等待需求回答”和“需求回答后必须立即 Recall”。
- `AskUserQuestion` 返回或进入后续需求补充轮次后，Stop Hook 会阻止只输出进度说明便结束。
- 明确要求宿主在同一 turn 委派命名 Memory Sub-agent，不得由主 Agent 搜索或直调 Recall MCP。
- 增加需求回答后提前停止、纯文本补充和取消报告任务的回归测试。

## 2.0.0-mvp.1

- 固定使用 WorkBuddy Memory Sub-agent 执行 Recall/Capture/Maintenance/Manage。
- 增加 L0 Episode、L1 Atom、L2 Context 与 L3 Rubrics 四层记忆。
- L2/L3 使用 `core / audience / project` Scope 的 Git-backed Markdown Context Repository。
- Recall 返回 Writing Context、Self-checklist 和 Judge Rubrics，冲突优先级为 `project > audience > core`。
- Hook 以 Sub-agent 的 Recall/Capture 完成标记作为流程门禁。

## 1.0.3

- L0 Writing Episode 保留 Memory Agent 传入的完整原始反馈，不再以子句筛选结果覆盖原文。
- 分类器只承担写作域过滤和维度识别；补齐“应、应当、需、必须、须、不能、不得”等中文反馈信号。
- 将“可靠性”纳入 traceability 维度，避免“可靠性说明应放附录”被误判为非显式反馈。
- 增加完整反馈落盘及中文要求句式的回归测试。
- 后台维护时间调整为每天 16:30，并修复 launchd 环境找不到 WorkBuddy Node 的问题。

## 1.0.2

- 按 WorkBuddy Agent MD 契约补齐 Memory Curator 的 `displayName`、`profession` 与 `maxTurns`。
- 删除无效的 `model: inherit`；省略模型字段以继承宿主 Agent 模型。
- 保留 WorkBuddy 5.3.12 实际注册自定义 Agent 所需的 MCP `tools` 声明；尽管内置规范称工具应由宿主统一授予，实测删除后 Agent 不会进入可调度注册表。
- 保持“特定管理受众关注北极星指标”为合理的 Audience L1 提炼。

## 1.0.1

- WorkBuddy 安装和升级时自动注册 Memory Agent 定时维护。
- L1→L2 整理时间固定为每天 10:30。
- 保持正常写作 Recall 由宿主 Agent 直接调用；Memory Agent 负责反馈提炼和后台维护。

## 1.0.0

- 与 v0.2.2 并行的插件、MCP、数据目录和发布包。
- L0 Writing Episode、六维 L1 原子规则、`scope × dimension` L2 Profile。
- 独立 Memory Agent；前台反馈委派与 WorkBuddy 定时维护脚本。
- Recall 优先级与同维度 Scope 冲突消解。
- capture 支持 MemoryCore `store/update/merge/skip` 和 `targetIds`。
- Hook 可识别 MCP 直接调用或 Memory Agent 完成标记。
