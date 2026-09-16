# Report Agent V2

面向 WorkBuddy 的研究报告助手：先整理结构化论据，由主 Agent 调度 Writer 撰写初稿、续用同一写作会话改写，再由原生子代理完成评测和写作记忆。V2 不依赖 V1 的 MCP、Hook、Python Runner 或外部模型 CLI。

## 四份包体

| 目录 | 用途 |
| --- | --- |
| [report-agent-v2-source/](report-agent-v2-source/) | 不含宿主安装封装的原始工作流源码，供同事封装 Codex / Claude Code 等插件；适配边界见目录内 README。 |
| [report-agent-v2-expert/](report-agent-v2-expert/) | 专家版及完整开发源码，包含 Agents、Skills、Rubrics、构建脚本和测试；后续开发只改这里。 |
| [report-agent-v2-plugin/](report-agent-v2-plugin/) | 同源生成的普通插件市场，可独立安装，无需依赖专家目录；不在此手动修改工作流。 |
| [report-agent-v2-skillhub/](report-agent-v2-skillhub/) | 可直接用于 SkillHub 分发的完整包体，原主 Skill 放在根目录 `SKILL.md`，无需额外入口或构建脚本。 |

四份包体共用 **6 个 Agent、1 个 Skill 和 Base Rubrics** 的工作流。Source 去除宿主安装逻辑，其余仅安装清单和入口路径不同。插件标识均为 `report-agent-v2`，主 Skill 为 `research-report-agent-v2`。选择一种安装形态使用，避免在同一会话同时加载重复的 Agent。

## 功能流程

资料整理 → 用户确认 → Writer 写 R0 → Resolution 冻结动态评测标准 → 分维度 Judge / 续用 Writer 改写 → 将最佳候选发布为报告 vN。

- 第 0 步默认委派 `report-evidence-agent-v2`，核验复用或更新素材目录的 `structured_data.json` 和 `数据版本说明.md`；报告记录数据版本与指纹，不保存数据快照。交付报告默认平铺在素材目录的 `报告/` 下，用户指定位置优先。
- Memory 默认开启，可由用户明确关闭；用户反馈先用于修改报告，再交给 Memory Agent。Memory Rubrics 参与评测，不直接注入首次写作。
- Reflection 在每日 16:30 后首次调用 Memory Agent 时执行或补做，并非无人值守的后台计划任务。
- Resolution 可按任务增减维度，不限制为六维；Base Rubrics 本身保留六个基础维度。

## 开发、同步与构建

开发构建需要 Node.js，安装包本身不包含或调用 Node 启动器。

```bash
npm --prefix report-agent-v2-expert run build:plugin
node report-agent-v2-expert/scripts/build-source.mjs
npm --prefix report-agent-v2-expert test
npm --prefix report-agent-v2-expert run build
```

每次修改以 Expert 为准，同一次提交同步四份包体，再运行测试。Plugin 和 Source 使用上述命令同步；SkillHub 直接提交包体，不提供专用构建脚本。测试检查文件清单与正文一致性，漏同步会失败。

SkillHub 同步只做三处路径适配：主 Skill 放到根目录；`references/` 放到根目录并将 `../../../resources/` 改为 `../resources/`；Agent 中的 `../skills/research-report-agent-v2/references/` 改为 `../references/`。其余 Agent、资源和 Rubrics 原样同步，清单的 `skills` 固定为 `["./"]`，不保留嵌套 `skills/`。

发布时同步 Expert 的 `package.json` 与 Expert、Plugin、SkillHub 三份插件清单的版本号。Source 不新增安装清单，四份内容以同一 Git commit 对应，保持原始源码的分发边界。

安装入口：

- 专家：`report-agent-v2-expert/release/report-agent-v2-expert-0.3.0/`（不提交 Git），用于 WorkBuddy 专家导入；打包时保留隐藏的 `.codebuddy-plugin/`。
- 普通插件：`report-agent-v2-plugin/`，在 WorkBuddy 普通插件入口添加此本地市场，再安装 `report-agent-v2@report-agent-v2-local`。具体步骤见[插件说明](report-agent-v2-plugin/README.md)。
- SkillHub：打包 `report-agent-v2-skillhub/` 的内容，确保压缩包根目录直接包含 `SKILL.md`，并保留 `.codebuddy-plugin/`。不要再套一层目录；不附带本机安装元数据、真实记忆或实验文件。

## 兼容与验证边界

Windows / macOS 使用同一组工作流文件，不附带平台二进制。自动化测试覆盖结构、内容一致性和关键契约，不代表所有 WorkBuddy 版本均已完成真实安装及完整报告实验。

正式交付的 `<报告主题>-vN.md` 和 `版本说明.md` 平铺在用户素材目录的 `报告/` 下，旧交付版保留，不另建历史版本文件夹。内部 RN 候选和运行记录保存在 `.report-agent/<报告标识>/loop-序号-日期时间/`，默认隐藏、不交付；用户反馈默认直接修订，只有明确要求重新评测时才启动新 Loop。每份交付报告记录所用数据版本，既有实验恢复沿用原路径，不搬迁旧数据。长期记忆独立保存在用户主目录下可见的 `ReportAgentMemory/`，各形态共用，不依赖 WorkBuddy 原生 Memory；其中 `MEMORY.md` 保存版本号 revision、设置、索引与当前 L2B，`L0-episodes/` 和 `L1-atoms/` 保存来源，不生成 history。仓库不包含真实报告、实验日志或用户记忆。
