# Report Agent V2 · WorkBuddy 普通插件测试版

本包用于对照验证普通插件安装后的子代理可用性。它与 V2 专家使用相同的 2 个 Skill、6 个 Agent 和 Base Rubrics；不包含 MCP、Hook、外部 CLI、平台启动脚本或任何用户记忆。0.3.0 在第 0 步默认委派资料整理员，核验复用或更新素材目录内的 `structured_data.json`，不改变 Judge/Rewrite 流程。

本目录由专家源码的 `scripts/build-plugin.mjs` 生成，请勿单独修改工作流文件。开发时修改 `report-agent-v2-expert/` 后运行 `npm run build:plugin`，同步生成普通插件和发布目录。

## 安装与使用

1. 解压压缩包，在 WorkBuddy 的普通插件管理入口添加本地插件市场目录：选择包含 `.codebuddy-plugin/marketplace.json` 和 `plugins/` 的文件夹，不要选择专家导入入口。
2. 安装并启用市场 `report-agent-v2-local` 中的 `report-agent-v2`。完整标识为 `report-agent-v2@report-agent-v2-local`。添加市场不等于安装、启用插件。
3. 新建普通会话，选择 `research-report-agent-v2` Skill，输入：“基于XX文件夹的素材，写一篇‘XX’主题的研究报告，篇幅X页”。初稿仍由当前主 Agent 撰写。

如果当前 WorkBuddy 版本没有本地插件市场入口，请保留版本号和界面反馈，不要自行编辑安装注册表或把文件复制进全局 agents 目录。

测试时不要选择“报告专家V2”，也不要在同一会话中同时启用来自 `my-experts` 和本测试市场的两份 V2；两者有相同的 Skill 和 Agent 名称。无需卸载现有专家，也不要修改或清空现有记忆。

## 安装后先做一次轻量验证

在单独的普通测试会话中，不选择写作 Skill，输入：

> 请验证 report-agent-v2 插件的子代理是否可用。分别委派 report-evidence-agent-v2、report-memory-agent-v2、report-resolution-judge-v2、report-dimension-judge-v2 和 report-writer-v2，仅要求各自回复 AVAILABLE。不要读取素材、执行记忆操作、修改文件或开始报告写作。请逐项报告真实返回；不可用就停止，不用通用子代理替代，也不要修改插件或安装配置。

这只是一次安装验证，会产生少量模型调用。成功仅证明子代理可调用，不代表完整 Report Loop 已通过。随后另开普通会话，选择 Skill 测试真实报告，检查 Resolution、Judge、Rewrite 及最终得分。

若返回 `Task agent ... is not available`，请提供 WorkBuddy 版本和该会话启动日志中关于 `report-agent-v2@report-agent-v2-local` 的安装、加载结果及可用 Agent 名单，不要直接重复跑整篇报告。

## 范围与限制

- 包中无平台二进制，Windows / macOS 使用同一包；两端真实安装与完整运行仍需验证。
- 默认启用写作记忆，可明确要求关闭。与专家版共用当前用户主目录下可见的 `ReportAgentMemory/`：`MEMORY.md` 带 revision 版本号，保存设置、索引和当前 L2B；`L0-episodes/`、`L1-atoms/` 保存来源，不生成 history。不依赖 WorkBuddy 原生 Memory，不自动扫描或清空旧记忆。
- Reflection 仍为到期后首次调用时补做，不新增后台定时任务。
- 普通插件不会把全局默认 Agent 改成报告专家；只有选择该 Skill 或匹配其写作请求时才进入报告流程。
