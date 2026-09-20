# 研究报告专家团 V4

面向 WorkBuddy 的 **Team 型专家团**，版本 `1.0.0`。把访谈、问卷、数据和文档整理成一份经过自动评测与迭代改写的研究报告；长期写作记忆默认启用，从用户后续反馈中沉淀评测标准。

## 血缘

- **基线底座**：OpenHarness PR #56 合并后的 `report-agent-v2-expert-team`（V2 分支 HEAD `c5dc9cc`）。expert-team 目录由 `fd3abc9` 引入，随后 8 个提交（角色名替代人格化命名、旧记忆格式归一化、报告目录选址优先级、大改路由到新 Loop 并给两小时预算、数据更新后确认修订等）同属该 PR。
- **继承的不变量**：评分公式、采纳 Gate、停止条件、Base Rubrics 与写作指令沿用 PR #51（`fefcef3`）。`tests/pr51-baseline.json` 用 SHA-256 钉住其中 14 个文件，防止这些规则被静默改动；本分支对受保护文件的改动在测试里显式登记为已批准。
- **本分支新增**：见下方「V4 相对基线的增量」。

## 工作方式

主理人确认需求、调度成员、执行 Gate 与交付；资料整理员准备论据，Writer 完成初稿及改写，Memory 管理长期写作要求，Resolution 冻结本轮标准，Dimension Judge 逐维评测。

- 使用 WorkBuddy 原生 TeamCreate / Agent / SendMessage；**不需要 MCP、Hook、外部模型 CLI 或后台服务**。
- 一种 Dimension Judge 定义按 N 个维度创建独立实例，同维度跨轮通过 SendMessage 续评，最多 6 个同时评测；第 7、8 个维度分批完成。**维度数量不固定为六**，是否新增由 Resolution Judge 结合当前任务决定。
- Writer 通过 SendMessage 延续同一成员上下文。跨会话不可恢复时，用文件中的需求、基线和修改记录重建。
- 两类 Judge 请求 `gpt-5.6-sol` / `medium`。实际能否使用取决于 WorkBuddy 账号与宿主调度；请求配置不是运行证明，显示回退时记录并告知。
- 记忆来自用户写作反馈、明确采纳或提炼委托，不从 Agent 分析反推用户要求；先保留证据，再判断是否形成长期标准。用户后续反馈默认直接改写；明确要求重新评测时才启动新 Loop，此时沿用同一报告标识并给两小时预算。
- 成员不可用时报告失败，不由主理人替代其专业工作。

## V4 相对基线的增量

四个脚本，都在解决「规范写了但没有代码兜底」的问题。

| 脚本 | 解决什么 |
|---|---|
| `resources/report/workspace.py` | 产物路径推导与边界校验。报告标识、loop 序号、vN 编号要跨会话一致，手工推导容易在第二次进来时出偏差；越界路径返回 `WORKSPACE_FAILED` |
| `resources/report/memory_access.py` | Phase 0 探测记忆目录可写性。记忆在工作区之外，若等到 capture 才发现写不了，那轮用户反馈就已丢失 |
| `resources/report/revision_brief.py` | 确定性生成 Revision Brief。brief 是 Writer 能看到的全部评测信息，手工提炼会让同一份 Judgment 得到宽严不一的改写指令 |
| `resources/report/judge_model.py` | Judge 模型唯一数据源。模型字面量原先散落在两个 Judge 的 frontmatter、契约文档与 README 中共 5 处，升级漏改一处会让两个 Judge 用不同模型打分且不报错 |

配套 `scripts/check_judge_model.py` 校验 4 个登记文件与数据源一致，并拒绝未登记文件里出现模型字面量、拒绝非 Judge 成员钉死模型；`scripts/build.py` 打包前必过该校验。

## 产物与数据位置

**报告产物只落在素材所在目录**，不另起工作区：

```text
用户项目文件夹/
├── 原始素材……
├── structured_data.json          共享论据表
├── 数据版本说明.md                D1/D2… 与当前素材指纹
└── 报告/
    ├── <报告主题>-v1.md           交付版本；Loop 交付与直接改写共用序号
    ├── 版本说明.md
    └── .report-agent/            内部记录，默认隐藏、不交付
        └── <报告主题-首次创建时间>/
            ├── 素材处理/r001/
            └── loop-001-时间戳/
                ├── 本轮需求.md
                ├── 候选报告/R0.md R1.md …
                └── 评测与改写记录/
```

同一报告的多次 Loop 共用一个报告标识：用户大改时沿用已有标识、新建 `loop-002`，旧轮完整保留。`RN` 是内部候选，`vN` 是对外交付版本，两者无数字对应关系。

**写作记忆在工作区之外**：用户主目录下的 `ReportAgentMemory/`，跨项目与宿主共用，纯 Markdown，用户可直接编辑。它可能落在宿主可写范围之外，因此 Phase 0 用 `memory_access.py probe` 先确认可写——不可写时由主理人询问用户授权、改用其他位置，或本轮不启用记忆。用户选定的位置会被记下，后续会话不再重复询问。

Reflection 是调用时检查并补做，不提供无人使用时也运行的后台定时任务。

不要让旧版专家与本专家团同时修改同一份报告或记忆。测试需要干净记忆时应明确隔离路径，不自动清空用户内容。

## 安装与验证

```bash
npm test                              # 10 JS + 55 Python，含模型一致性校验
npm run build                         # 打包为 dist/<name>-<version>.zip
sh scripts/install-local-expert.sh    # 构建并安装到本地 WorkBuddy
```

`install-local-expert.sh` 解压构建产物再安装，保证装进去的就是打包校验过的那份；同时退役此前同一专家的旧名目录（`report-agent-v3` 等），避免市场里双列。安装后重启 WorkBuddy，在「专家 → 我的专家」找「研究报告专家团 V4」，确认显示主理人与 5 位成员——6 个角色不等于只能运行 6 个实例。

构建的 zip 用于完整专家团导入，不能当作普通 Skill 复制到 skills 目录。

测试覆盖资源链接、团队注册契约、PR #51 不变资产、评分规则、素材指纹与字数统计脚本，以及 V4 新增的路径边界、记忆授权与 brief 生成。**这些都是脚本层验证**：循环控制、Judge 并行与 SendMessage 回传由 prompt 驱动，仍需真实桌面环境验收，静态测试不能替代端到端通过。验收场景见 `tests/team-scenarios.md`。

## 目录

```text
.codebuddy-plugin/plugin.json   专家团身份与 6 个角色
settings.json                   主理人入口
agents/                         角色指令
skills/report-agent/    主流程及执行卡
resources/evidence/             论据清洗规范、schema 与素材指纹脚本
resources/report/               路径推导、记忆探针、brief 生成、字数统计、模型数据源
rubrics/base-rubrics.json       不变的基础标准
avatars/                        团队及成员头像
scripts/、tests/                仅开发构建与验证，不进入安装包
dist/                           构建的可安装 zip（不提交）
```

头像使用内置图像生成工具生成，统一深青色插画风格，按角色分别表现研究协调、论据核验、写作、记忆管理、标准解析和评测。512×512 PNG，用户可自行替换。

## 待办

`plugin.json` 的 `author.email` 仍是占位值，提交市场审核前需填写真实邮箱。
