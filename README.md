# Report Agent V2

面向 WorkBuddy 的研究报告助手：先整理结构化论据，由主 Agent 调度 Writer 撰写初稿、续用同一写作会话改写，再由原生子代理完成评测和写作记忆。V2 不依赖 V1 的 MCP、Hook、Python Runner 或外部模型 CLI。

## 两种交付形态

| 目录 | 用途 |
| --- | --- |
| [report-agent-v2-expert/](report-agent-v2-expert/) | 专家版及完整开发源码，包含 Agents、Skills、Rubrics、构建脚本和测试；后续开发只改这里。 |
| [report-agent-v2-plugin/](report-agent-v2-plugin/) | 同源生成的普通插件市场，可独立安装，无需依赖专家目录；不在此手动修改工作流。 |

两种形态使用完全相同的 **6 个 Agent、2 个 Skill 和 Base Rubrics**，仅安装清单和入口不同。插件标识均为 `report-agent-v2`，主 Skill 为 `research-report-agent-v2`。选择一种形态使用，避免在同一会话同时加载重复的 Agent。

## 功能流程

资料整理 → 用户确认 → Writer 写 V0 → Resolution 冻结动态评测标准 → 分维度 Judge / 续用 Writer 改写 → 交付历史最佳报告。

- 第 0 步默认委派 `report-evidence-agent-v2`，核验复用或更新素材目录的 `structured_data.json` 和 `数据版本说明.md`；报告记录数据版本与指纹，不保存数据快照。报告默认保存到素材目录下的 `报告/报告主题-开始时间/`，用户指定位置优先。
- Memory 默认开启，可由用户明确关闭；用户反馈先用于修改报告，再交给 Memory Agent。Memory Rubrics 参与评测，不直接注入首次写作。
- Reflection 在每日 16:30 后首次调用 Memory Agent 时执行或补做，并非无人值守的后台计划任务。
- Resolution 可按任务增减维度，不限制为六维；Base Rubrics 本身保留六个基础维度。

## 开发、同步与构建

开发构建需要 Node.js，安装包本身不包含或调用 Node 启动器。

```bash
npm --prefix report-agent-v2-expert run build:plugin
npm --prefix report-agent-v2-expert test
npm --prefix report-agent-v2-expert run build
```

`build:plugin` 重新生成仓库的插件目录和发布目录；测试会检查仓库内两种形态逐字节一致。提交时同时提交专家源码与更新后的插件目录。

安装产物（不提交 Git）：

- 专家：`report-agent-v2-expert/release/report-agent-v2-expert-0.3.0/`，用于 WorkBuddy 专家导入；打包时保留隐藏的 `.codebuddy-plugin/`。
- 普通插件：`report-agent-v2-plugin/`，在 WorkBuddy 普通插件入口添加此本地市场，再安装 `report-agent-v2@report-agent-v2-local`。具体步骤见[插件说明](report-agent-v2-plugin/README.md)。

## 兼容与验证边界

Windows / macOS 使用同一组工作流文件，不附带平台二进制。自动化测试覆盖结构、内容一致性和关键契约，不代表所有 WorkBuddy 版本均已完成真实安装及完整报告实验。

新任务将正式报告、历史版本与 `Agent运行记录/` 内部文件分开保存；既有实验恢复时沿用原路径，不搬迁旧数据。每份 `<报告主题>-vN.md` 在版本说明中记录所用数据版本。长期记忆独立保存在用户主目录下可见的 `ReportAgentMemory/`，两种形态共用，不依赖 WorkBuddy 原生 Memory；其中 `MEMORY.md` 保存版本号 revision、设置、索引与当前 L2B，`L0-episodes/` 和 `L1-atoms/` 保存来源，不生成 history。仓库不包含真实报告、实验日志或用户记忆。
