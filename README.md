# 报告专家团 V2

基于 OpenHarness PR #51（`fefcef3308d11102ef0282965d2ec7dc5cf67eda`）的独立 WorkBuddy 专家团，版本 `0.1.0`。不包含 PR #52 的重大/小型修改分流，不覆盖原来的 Expert、Plugin、Source、SkillHub 包。

## 工作方式

主理人确认需求、调度和执行 Gate；资料整理员准备论据，Writer 完成初稿及后续改写，Memory 管理长期写作要求，Resolution 冻结标准，Dimension Judge 逐维评测。

- 使用 WorkBuddy 原生 TeamCreate / Agent / SendMessage；不需要 MCP、Hook、外部模型 CLI 或新增后台服务。
- 一种 Dimension Judge 定义按 N 个维度创建独立实例，同维度跨轮通过 SendMessage 续评，最多 6 个同时评测；第 7、8 个维度分批完成。
- Writer 通过 SendMessage 延续同一个成员上下文。跨会话不可恢复时，使用文件中的需求、基线和修改记录重建。
- 两类 Judge 默认请求 `gpt-5.6-sol`，effort 为 `medium`。实际能否使用该模型取决于 WorkBuddy 账号和宿主调度；请求配置不是运行证明。
- 评分公式、采纳 Gate、停止条件、Base Rubrics 和写作指令沿用 PR #51。记忆仅从用户明确的写作反馈提炼，不从 Agent 分析反推用户要求。用户后续反馈默认直接改写，明确要求重新评测才启动新 Loop。
- 团队成员不可用时报告失败，不由主理人替代其专业工作。这是与旧 Evidence 失败后允许主 Agent 解析的区别，遵循专家团正式协作契约。

## 安装与数据

构建的 zip 用于完整专家团导入，不能当作一个普通 Skill 单独复制到 skills 目录。安装后新建会话，确认显示主理人和 5 个专业成员；6 个角色不等于只能运行 6 个实例。

本轮仅构建新包，不修改 WorkBuddy 注册表或现有安装。

报告与结构化论据仍保存在用户项目目录。记忆默认启用，沿用用户主目录下的 `ReportAgentMemory/`，会读取已有 V2 写作记忆；专家团 ID 独立不意味着记忆库隔离。不要让旧版与专家团同时修改同一份报告或记忆。测试需要干净记忆时，应另行明确隔离路径，不自动清空用户内容。

Reflection 仍是调用时检查并补做，不提供无人使用时也运行的后台定时任务。

## 开发与验证

```text
npm test
npm run build
```

测试检查资源链接、团队注册契约、PR #51 不变资产及评分规则，另有原有 Python 计数/素材指纹脚本的回归测试。构建仅打包 agents、skill、resources、rubrics、avatars、manifest、settings 和 README；测试与开发脚本不进入安装包。

本地已核验 WorkBuddy CLI 支持同类成员多实例，并以返回的实际成员名通信；真实桌面专家团运行仍需验收。不能把静态测试当作 Mac/Windows 端到端通过。验收场景见 `tests/team-scenarios.md`。

## 目录

```text
.codebuddy-plugin/plugin.json  专家团身份与 6 个角色
settings.json                 主理人入口
agents/                       角色指令
skills/research-report-loop/ 主流程及执行卡
resources/                    沿用的论据扫描、字数统计和格式定义
rubrics/base-rubrics.json      不变的基础标准
avatars/                      团队及成员头像
scripts/、tests/              仅开发构建与验证
dist/                         构建的可安装 zip（不提交）
```

头像使用内置图像生成工具生成，统一深青色插画风格；按角色分别表现研究协调、论据核验、写作、记忆管理、标准解析和评测。图片已保存为 512×512 PNG，用户可自行替换。
