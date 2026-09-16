# report-agent-v2

WorkBuddy Native V2。它与现有 `research-report-loop-memory` V1 并列，不覆盖、不复用其 MCP、Hook、Python Runner 或外部 CLI；通过 Skill、原生 Sub-agent 和隐藏状态文件复现 V1 的评分、采纳、停止、恢复和 Memory 行为。

## 当前范围

- 主 Agent 在第 0 步先委派资料整理员生成或核验复用结构化论据，再确认输入并委派 Writer 撰写 R0；
- Memory Agent 在用户主目录的 `ReportAgentMemory/` 管理 L0/L1/L2B，不依赖宿主原生 Memory；
- Resolution Judge 根据任务动态冻结 N 个评测维度；
- 参数化 Dimension Judge 按 N 个维度运行；
- 主 Agent按 V1 公式确定性计分并执行候选采纳门槛；
- Writer 首次完整读取写作指令，用同一写作会话完成初稿与改写；Loop 改写只从历史最佳版本生成候选，主 Agent 保存 writerAgentId、Judgment 与恢复状态；
- 用户反馈后先续用 Writer 修改报告，再 Capture。

V2 不安装平台相关的每日 Automation。Memory Agent 在每日 16:30 后首次被调用时执行当日 Reflection；长期未使用时在下次调用补做，因此不引入 MCP、Hook 或系统计划任务。

## 构建与验证

```bash
npm test
npm run build
npm run build:plugin
```

构建产物位于 `release/report-agent-v2-expert-0.3.0/`，可作为 WorkBuddy Expert 目录检查或打包。构建不会读取或修改 V1，也不覆盖旧版 0.2.0 包。

## 普通插件对照测试包

运行 `npm run build:plugin` 同步仓库同级目录 `../report-agent-v2-plugin/`，并生成 `release/report-agent-v2-plugin-0.3.0/` 本地插件市场。两个输出目录均为生成物，请只在本源码目录修改后重新构建。此命令不修改或重建专家包；安装与测试方法见 [普通插件说明](packaging/plugin-README.md)。

仓库同级 `report-agent-v2-skillhub/` 直接保存可分发的 SkillHub 包，根目录只有一个主 `SKILL.md`。配套子代理缺失时支持原地补齐安装、启用记录，不新增市场或复制缓存。该包不使用专用构建脚本；内容与版本随本源码同步，`npm test` 检查四份包体一致性。

普通插件直接复用本目录的 Agents、Skill 和 Rubrics，仅去掉专家专属的清单元数据，不新增 MCP、Hook 或 CLI，也不修改用户的 WorkBuddy 配置。通过普通会话选择 `research-report-agent-v2` Skill 使用，不设置全局默认 Agent。不要同时加载专家与普通插件的两份 V2。

`npm test` 检查包内声明路径、工作流文件逐字节一致性及安装包结构；这些静态检查不代替 Windows / macOS 的实际子代理调用验证。

产品、插件和主 Agent 的标识统一为 `report-agent-v2`，主 Skill 为 `research-report-agent-v2`。新任务按 [保存位置与交付](skills/research-report-agent-v2/references/workspace-and-delivery.md) 保存到用户项目，恢复旧实验时沿用原路径。旧名称安装与新名称安装不要在同一会话中同时启用。

## 记忆目录

默认位置为当前用户主目录下的可见文件夹：macOS 为 `/Users/<用户名>/ReportAgentMemory/`，Windows 通常为 `C:\Users\<用户名>\ReportAgentMemory\`；实际由系统主目录决定，不固定盘符。专家与插件共用此目录，也不以宿主名称或版本号划分。

```text
ReportAgentMemory/
├── MEMORY.md       revision 版本号、设置、索引和当前 L2B Rubrics
├── L0-episodes/    原始反馈及上下文
└── L1-atoms/       原子证据
```

用户可以直接查看、修改 Markdown；Memory Agent 每次操作重新读取，写前核对人工修改。新写入不生成 history；旧版宿主 Memory、旧目录及历史副本不自动扫描、搬迁或删除。MEMORY.md 的 revision 标识当前版本，不表示保留了可回滚副本。

## 已知待验证项

资料整理由 [report-evidence-agent-v2](agents/report-evidence-agent-v2.md) 独立执行，配套规则和脚本位于 `resources/evidence/`，不再注册 Evidence Skill。复用 OpenHarness 的清洗原则和原始 Evidence Schema，不搬入外部模型 CLI 或 Human Report 质检。共享论据保存在素材目录的 `structured_data.json`，不另存数据快照；每份历史稿及 Judgment 记录 dataVersion/dataSha256，数据变化时停止沿用旧绑定。原始素材和历史报告不覆盖。四份包体共用 6 个 Agent 和 1 个 Skill 的工作流，仅安装封装与入口路径不同。

素材目录的 `数据版本说明.md` 记录 D1、D2…、更新时间、更新内容和当前文件指纹清单，不另建素材清单文件。自带标准库脚本使用宿主已有的 Python 3.9+ 识别新增、删除和内容修改；全部处理并发布成功才登记数据版本。无变化不递增版本；没有可信清单先完整核验。没有 Python 时可逐项检查资料，但不能伪造已完成的版本登记或继续受版本校验保护的 Loop，不自动安装依赖。详见 [素材变化识别](resources/evidence/references/source-changes.md)。

1. 可见记忆目录在 Windows / macOS 实际会话中的权限、跨 Session 复用及人工编辑后的读取；
2. WorkBuddy 实际会话中的 Writer agentId 返回与 resume 续写，以及主 Agent 对同一个 Dimension Judge 的动态 N 次并行调用；
3. 无 Hook 时反馈 Capture 的稳定触发率；
4. Windows 与 macOS 的同包安装；
5. 原生 Sub-agent 方案和 V1 在报告质量、Token、耗时与失败率上的真实 E2E 差异。
