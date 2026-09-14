# report-agent-v2

WorkBuddy Native V2。它与现有 `research-report-loop-memory` V1 并列，不覆盖、不复用其 MCP、Hook、Python Runner 或外部 CLI；通过 Skill、原生 Sub-agent 和隐藏状态文件复现 V1 的评分、采纳、停止、恢复和 Memory 行为。

## 当前范围

- 主 Agent 在第 0 步先委派资料整理员生成或核验复用结构化论据，再确认输入并亲自撰写 V0；
- Memory Agent 使用 `memory: user` 管理 L0/L1/L2B；
- Resolution Judge 根据任务动态冻结 N 个评测维度；
- 参数化 Dimension Judge 按 N 个维度运行；
- 主 Agent按 V1 公式确定性计分并执行候选采纳门槛；
- Rewriter 只从历史最佳版本生成候选，主 Agent保存 Judgment 与恢复状态；
- 用户反馈后先改报告，再 Capture。

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

普通插件直接复用本目录的 Agents、Skill 和 Rubrics，仅去掉专家专属的清单元数据，不新增 MCP、Hook 或 CLI，也不修改用户的 WorkBuddy 配置。通过普通会话选择 `research-report-agent-v2` Skill 使用，不设置全局默认 Agent。不要同时加载专家与普通插件的两份 V2。

`npm test` 检查包内声明路径、工作流文件逐字节一致性及安装包结构；这些静态检查不代替 Windows / macOS 的实际子代理调用验证。

产品、插件和主 Agent 的标识统一为 `report-agent-v2`，主 Skill 为 `research-report-agent-v2`。报告状态目录继续使用 `.report-loop-v2/`，以兼容已有实验；Memory Agent 名称及 `memory: user` 不变，不迁移或清空既有记忆。旧名称安装与新名称安装不要在同一会话中同时启用。

## 已知待验证项

资料整理采用 [report-evidence-v2](skills/report-evidence-v2/SKILL.md)，复用 OpenHarness 的清洗原则和原始 Evidence Schema，不搬入其外部模型 CLI 或 Human Report 质检打分。共享论据保存在素材目录的 `source/structured_data.json`，跨工作区优先核验复用，资料变化时更新；报告工作区 `.report-loop-v2/evidence/rNNN/structured_data.json` 只保留本轮固定快照。原始素材和历史报告快照不覆盖；这是项目数据，不是长期 Memory。两种包包含完全相同的 6 个 Agent 和 2 个 Skill。

1. `memory: user` 在 WorkBuddy 实际 Expert 会话中的跨 Session 行为；
2. 主 Agent 对同一个 Dimension Judge 的动态 N 次并行调用；
3. 无 Hook 时反馈 Capture 的稳定触发率；
4. Windows 与 macOS 的同包安装；
5. 原生 Sub-agent 方案和 V1 在报告质量、Token、耗时与失败率上的真实 E2E 差异。
