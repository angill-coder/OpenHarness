# Report Agent V2 原始源码

用于二次开发和封装，不是可直接安装的 Codex、Claude Code 或 WorkBuddy 插件。初始导出基线为 `7f7f598`；未包含同期其他任务尚未提交的篇幅与 Writer 修改。

这是一套以 Prompt 和文件契约驱动的工作流，不是独立运行的 Agent 框架。保留原始文件和相对路径，便于与已验证的 WorkBuddy 版本比较；没有附带安装清单、市场注册、头像、MCP、Hook 或外部模型 Runner。原 Prompt 中的 WorkBuddy 表述和工具调用语法仍属于待适配内容，不代表其他宿主已支持。

## 内容

| 目录 | 内容 |
| --- | --- |
| `agents/` | 主调度、Evidence、Writer、Memory、Resolution Judge、Dimension Judge，共六个角色 |
| `skills/research-report-agent-v2/` | 主流程、写作指令、评分与状态、目录和交付约定 |
| `resources/evidence/` | 清洗规则、数据 Schema、增量素材指纹 Python 脚本 |
| `rubrics/` | 原始 Base Rubrics，保持内容不变 |

入口先读 `skills/research-report-agent-v2/SKILL.md`，再按其链接读取对应执行卡。流程保持：Evidence → 用户确认 → Writer V0 → Memory 候选 → Resolution 冻结标准 → 分维 Judge / Writer 改写 → 主 Agent 判断 Gate 并交付。反馈默认直接修订，再记录记忆；自动 Judge 反馈不进入记忆。

## 封装时需要适配的边界

| 原始约定 | 封装方需要提供 |
| --- | --- |
| Agent frontmatter 中的 `skills`、`tools`、`model`、`effort`、`maxTurns` 等 | 按目标宿主映射注册字段和权限，不能假定直接复制就有效；模型 ID 需确认可用 |
| `AskUserQuestion` | 宿主确认工具；无对应工具时用普通对话提问并等待回答，保留先展示完整观点、再简短确认的交互 |
| 委派子代理、后台等待与完成标记 | 真实创建、等待、检查工具返回及输出文件；不能把“已启动”当成“已完成” |
| `resume=writerAgentId` | 同一 Writer 的续写能力；不支持或无法恢复时，按 writer-orchestration.md 用文件上下文重建，不能伪造 ID |
| Judge 隔离和按维度并行 | 独立于 Writer 上下文；并行受限可顺序执行，仍覆盖冻结 Plan 的全部动态维度 |
| 文件读写、素材解析和 Python 调用 | 宿主授权范围内的工具；素材指纹脚本需 Python 3.9+ 标准库，不能因缺少解析能力静默丢素材 |
| `ReportAgentMemory/` | 用户主目录下可见、跨宿主的显式文件记忆；不替换成宿主原生 Memory，不随安装包分发真实记忆 |

不要改变 Base Rubrics、评分公式、候选采纳条件、数据版本绑定和历史版本不可覆盖规则。Reflection 仍是每日 16:30 后首次 Memory 调用时补做，不是无人值守定时任务。

最低验收：整理论据、确认输入、Writer 初稿、Resolution 与动态维度 Judge、至少一次改写、最终交付、反馈 Capture、素材变更后的增量更新。必须验证真实调用和文件结果；静态测试不能证明宿主端已跑通。

## 验证与维护

原始包不分发 tests、evals 或 npm 工程配置。论据指纹脚本只需 Python 3.9+ 标准库；封装方应在目标宿主验证上述完整流程。

本目录是独立可复制的导出快照。当前上游流程仍在 `report-agent-v2-expert/` 维护，仓库根目录执行 `node report-agent-v2-expert/scripts/build-source.mjs` 可刷新导出；该命令会覆盖同名文件，因此同事应在自己的分支或副本做宿主适配，不要重新导出覆盖适配结果。今后若上游删除文件，需要在 review 中同步移除导出中的旧文件。
