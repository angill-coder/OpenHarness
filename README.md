# 研究报告专家团

面向 WorkBuddy 的 **Team 型专家团**。用户提交访谈、问卷、数据或文档后，专家团按标准 SOP 完成资料清洗、报告撰写、自动评测迭代改写和交付审校；长期写作记忆默认启用，并从用户后续反馈中沉淀 Memory Rubrics，用户可明确要求关闭。

## 团队编制

| 成员 | 名字 | 角色 | 职责 |
|------|------|------|------|
| `report-team-lead` | 芮淑 | 主理人 | 确定目录、按阶段调度成员、汇编交付 |
| `report-evidence-agent` | Evan | 成员 | 资料清洗：指纹扫描、增量解析、维护论据表与数据版本 |
| `report-writer` | Wesley | 成员 | 报告撰写：确认写作输入、完成初稿、提交 Report Loop |
| `report-reviewer` | Rena | 成员 | 交付审校：核验交付物、按反馈直接修改报告 |
| `report-memory-agent` | Mnemo | 成员 | 写作记忆：resolve / inspect_sources / capture / manage / reflect / settings |

## 标准工作流程

```
用户 → 主理人 TeamCreate
        │
        ├── Phase 0  主理人亲自        确定素材目录、报告目录、报告标识
        ├── Phase 1  evidence-agent   指纹扫描 → 增量清洗 → 登记数据版本
        ├── Phase 2  report-writer    确认输入 → 初稿 → Report Loop 自动评测
        ├── Phase 3  report-reviewer  交付审校 → 可交付 / 需修订
        ├── Phase 4  report-reviewer  反馈修订 → 新 vN（不新建 Loop）
        │            memory-agent     记忆沉淀
        ├── Phase 5  大改重启 Loop     沿用同一报告标识 → loop-002
        └── Phase 6  素材更新          扫描变化 → 询问是否重写
        │
        └── 主理人汇编 → 交付用户
```

所有跨成员信息流经主理人中转，成员之间不直连；主理人不代写任何成员的专业产出。

## Report Loop（自动化评测组件）

Report Loop **不是团队成员**，而是 `research-report-loop` Skill 内置的已验证自动化组件：

1. **Resolution Judge**：判断哪些 Memory Rubrics 适用于本轮（`additional` / `interpret` / `ignore`），冻结 Resolution Plan 与 compiled rubric
2. **六维 Judge**：6 个隔离进程并行评测（traceability / structure / narrative / insight / coverage / expression），共用同一份冻结的 rubric
3. **持久 Rewriter**：常驻 CLI 流串行执行 Rewrite → Judge，直到 5 星、连续 2 轮未采纳或 60 分钟超时
4. **交付**：最佳候选发布为 `报告目录/<报告主题>-vN.md`

把 Judge 与 Rewrite 保留在 Python Runner 内，是为了保证**同一轮评测中 Rubrics 严格一致冻结**——这是报告质量的核心机制。主理人与任何成员都不得自行执行 Judge 或 Rewrite。

### 启动方式：Hook 直调，不经 MCP

```
report-writer 写入 job.json
  ↓
PostToolUse Hook（hooks.json 注册，matcher "*"）
  ↓
capture-checkpoint.mjs post-tool 识别出合法 Job
  ↓
spawn 自己 run-loop-worker → new ReportLoopLauncher()
  ↓
spawn run-python.sh → report_loop/runner.py
  ↓
返回结果文件路径 + 一条后台等待命令
  ↓
report-writer 用 Bash(run_in_background=true) 等待
收到 <task-notification> → TaskOutput 读一次完整 JSON
```

**插件不声明任何 MCP 服务。** Agent 在沙箱内跑不了 60 分钟的重活，Hook 在沙箱外；Job 写入即自动触发，不依赖模型主动选中某个工具。`ReportLoopLauncher` 是普通 TS 类，被 esbuild 打进 `dist/capture-checkpoint.mjs`，只依赖 `node:` 内置模块。

WorkBuddy 的 Team 型专家会加载 `hooks.json`（已与平台侧确认）。由于没有 MCP 后备路径，`hooks.json` 损坏就意味着 Loop 静默不启动——因此 `build:team` 会校验三个事件的注册、命令指向 `bin/capture-checkpoint.mjs`、launcher 与 Python Runner 随包、以及 bundle 无外部 import；任一不满足则拒绝出包。

### 模型与隔离

- Writer / Rewriter 使用 WorkBuddy 主对话中用户实际选择的模型；Hook 记录 session，Runner 从主会话 trace 读取 `requestModelId`，不让 Agent 猜模型 ID
- Judge Provider 默认 `workbuddy`，可在 Job 中选 `codex`。仅在调用失败、空响应或 Judge JSON 不合约时熔断到当前主模型；**评分低不触发回退**
- 每个维度、每轮 Judge 使用独立 CLI 进程与上下文（`--tools "" --max-turns 1`）
- Rewriter 只看脱敏后的 revisionBrief，**看不到 Judge 原文**，避免改写迎合打分细节

### 更换 Judge 模型

Judge 模型默认**锁定**，与用户当前选择的模型无关。这是刻意设计：写作记忆靠稳定评分校准——记忆管理员判断某条反馈值不值得沉淀为 L2B，前提是分数跨会话可比。Judge 跟着会话模型漂，评分就成了移动靶。

模型只在一个地方定义：`report_loop/core/judge_model.py`

```python
JUDGE_MODEL = "gpt-5.6-sol"        # 平台升级时改这里
JUDGE_EFFORT = "medium"
CODEX_JUDGE_MODEL = "gpt-5.6-sol"
CODEX_JUDGE_EFFORT = "medium"
```

`build-release.mjs` 解析这个文件而非重复声明，所以**平台升级只改 1 行**，Node 与 Python 两侧不会漂移。有测试断言构建脚本里不得出现模型字面量。

三级覆盖，优先级从高到低：

| 层级 | 入口 | 适用场景 |
|---|---|---|
| Job 字段 | `judgeModel` / `judgeEffort` | 用户明确要求换模型的单次任务 |
| 环境变量 | `RESEARCH_REPORT_LOOP_JUDGE_MODEL`、`..._WB_MODEL`、`..._CODEX_MODEL`、`..._JUDGE_EFFORT` | 部署方临时切换、灰度验证 |
| 锁定常量 | `judge_model.py` | 默认值 |

不传任何覆盖时行为与锁定完全一致。`effort` 启动前校验，非法值立即报错而不是循环中途失败。

**可比性追踪**：任何覆盖或降级都在结果 JSON 里留痕——`judgeModel`、`judgeModelPinned`、`judgeFallbackUsed`、`scoresComparableToHistory`。后者为 `false` 时主理人必须向用户明示本轮得分与历史不可比。

## 写作记忆（三层结构）

记忆存在**用户主目录下可见的** `~/ReportAgentMemory/`，全部为 Markdown，用户可直接查看和修改。

```
~/ReportAgentMemory/
├── MEMORY.md           设置（enabled / lastReflectionAt）、revision、active L2B
├── memory-history.md   按 revision 追加的变更记录
├── L0-episodes/        EP-*.md，每条 Episode 单独保存
└── L1-atoms/<scope>/   L1-*.md，按 Scope 保存并保留 sourceEpisodeIds
```

| 层 | 内容 |
|----|------|
| L0 Writing Episode | 用户反馈及必要上下文，用于审计、核验和重新提炼 |
| L1 Atom Memory | 由 Episode 支撑的一条精简原子证据 |
| L2B Memory Rubric | 稳定、可观察、值得长期影响 Judge 的用户评判标准 |

- Scope 分 `core`（跨项目受众仍成立）/ `audience`（因特定受众才成立）/ `project`（换项目即失效）；当前任务的 audience/project 元数据**不能反推 Scope**
- `MEMORY.md` 顶部的 `revision: N` 是唯一版本号，只在持久化修改成功时递增；**不提供历史回滚**
- **不维护全量索引或快照**：按 ID 在目录查找，靠 L2B → sourceL1Ids → L1 → sourceEpisodeIds → L0 的来源链回溯
- 记忆只新增独立的 Memory Rubrics，**不修改 Base Rubrics**；红线与硬门槛不可被弱化
- 写作成员**不直接 Recall 记忆**。L2B 只通过 Judge 影响后续报告，避免个性化记忆污染写作上下文
- Memory 关闭时：resolve 不返回候选、capture/reflect 不写入，已有文件保留不变；用户显式 manage 仍可执行

### 两条调度链

- **实时 Capture**：Hook 识别已交付报告后的明确写作反馈，提醒先修改报告，再由主理人委派 `report-memory-agent` Capture（传稳定 `captureId`，重试复用）。Judge 反馈、Judge 分数和自动改写**不进入 Memory**
- **到期补做 Reflection**：**不依赖后台调度**。记忆管理员在 capture 等操作开始时检查 `lastReflectionAt`，过 16:30 且今日未复盘（或上一自然日未复盘）则先补做一次，同日不重复；长期未使用不逐日补齐

## 产物目录

```
<素材目录>/
├── 原始素材……
├── structured_data.json          共享论据表（EV-001…）
├── 数据版本说明.md                D1/D2… + 当前素材指纹清单
└── 报告/
    ├── <报告主题>-v1.md           Loop 交付与直接改写共用版本号，旧版保留
    ├── <报告主题>-v2.md
    ├── 版本说明.md                版本、时间、dataVersion、修改要求、评测结果
    └── .report-agent/            内部记录，默认隐藏、不交付
        └── <报告主题-首次创建时间>/
            ├── 素材处理/r001/
            ├── loop-001-时间戳/
            │   ├── 本轮需求.md
            │   ├── 候选报告/R0.md R1.md …
            │   └── 评测与改写记录/{run-state,resolution-plan}.json
            │                      judgments/RN.json  revision-briefs/RN.json
            └── loop-002-时间戳/    大改重启 Loop，与首轮并列保留
```

**同一报告的多次 Loop 共用一个报告标识**：用户看完报告要大改时沿用已有标识，在其下新建 `loop-002`，评测历史与版本号都不断。`RN` 与 `vN` 无数字对应关系——候选每轮从 `R0` 重新编号。

路径由代码推导而非 LLM 决定：Runner 从已校验的素材路径推导锚点，`outputPath` 越界直接拒绝。

## 配置文件

| 文件 | 随包 | 读者 | 作用 |
|---|---|---|---|
| `.codebuddy-plugin/plugin.json` | ✅ | WorkBuddy | **Team 型清单**：`expertType` / `teamInfo` / `members` / 展示字段 |
| `settings.json` | ✅ | WorkBuddy | **指定主理人**，规范第 2.2 节标 ★ 必须 |
| `.codebuddy-plugin/marketplace.json` | ❌ | `install:local` | 本地市场索引；字段与 `plugin.json` 同源，有测试校验 |
| `package.json` | ❌ | npm | 构建与测试脚本、开发依赖 |
| `package-lock.json` | ❌ | npm | 依赖锁定 |
| `tsconfig.json` | ❌ | tsc | 类型检查，`include: hooks/**/*.ts` |

包里另有一份 `package.json`，是 `build-release.mjs` 生成的**精简运行时声明**（`<name>-runtime`），只写 `engines`——安装后不需要 npm，Hook bundle 自包含。

## 文档分类

四类文档的判据是**谁读、要不要随包分发**：

| 位置 | 读者 | 随包 | 内容 |
|---|---|---|---|
| `skills/*/references/` | Agent（运行时） | ✅ | 执行契约：目录约定、Job 构造、记忆调度、写作规范 |
| `resources/*/references/` | 特定成员（运行时） | ✅ | 成员专用规范，与同目录脚本、schema 一起作为资源包分发 |
| `docs/` | 人（开发与排查） | ❌ | 实现架构、设计取舍；`build:team` 会剔除 |
| 脚本顶部注释 | 人（维护脚本） | 随脚本 | usage 与副作用说明，不单独成文 |

**prompt 里不得引用 `docs/`** —— 那些文件不在安装目录里，Agent 读不到。有测试断言这条：遍历所有 agent MD 与 skill 文档，出现 `` `docs/ `` 即失败。

`skills/references/` 与 `resources/references/` 的区别：后者和可执行脚本（`source_inventory.py`）、schema 打包在一起，作为整体交给 `report-evidence-agent`；前者是纯文本执行卡。

## 仓库结构

```
report-agent-v3/
├── .codebuddy-plugin/plugin.json   # Team 型配置（expertType / teamInfo / members）
├── settings.json                   # 指定主理人
├── agents/                         # 主理人 + 4 位成员
├── avatars/                        # 团队头像 + 各成员头像
├── skills/research-report-loop/    # 共享技能（SKILL.md + 三份 references）
├── rubrics/                        # Base Rubrics（不可变，v2.4 / 六维 26 checks）
├── resources/evidence/             # 随包分发给 evidence-agent
│   ├── scripts/source_inventory.py #   scan / confirm / check
│   ├── references/                 #   cleaning-rules / output-contract / source-changes
│   └── structured_data.schema.json
├── report_loop/                # Python Report Loop 全部业务逻辑
│   ├── runner.py                   #   Job 校验 → 路径推导 → 驱动循环 → 发布 vN
│   └── core/                       #   workspace / memory_store / judge_* / rubric_* / runtime
├── hooks/                          # 沙箱外启动器
│   ├── hooks.json                  #   UserPromptSubmit / PostToolUse / Stop
│   ├── capture-checkpoint.mjs      #   识别 Job → 拉起 Python；识别反馈 → 提醒 Capture
│   ├── lib/                        #   launcher / relevance / memory-settings
│   └── test/                       #   TS 测试
├── tests/                          # Python 测试
└── scripts/                        # 构建与本地安装
```

三个运行时数据目录都在插件外：`<素材目录>/报告/`（产物）、`~/ReportAgentMemory/`（记忆）、`~/.research-report-loop/`（Hook 状态与 run 索引）。

## 开发

```bash
npm install
npm test            # TS 测试 + Python 测试
npm run syntaxcheck
npm run build:team  # 产出可提交的 Team 型 zip
```

`build:team` 会在打包前按《WorkBuddy 专家开发规范》v2.4 第十节自检 30+ 项：
`agentName` / `teamInfo` / `members[]` / `settings.json` 四方 ID 对齐、
Agent frontmatter **无 `tools` 字段**、主理人 MD 含「团队协作机制（铁律）」全部章节且列出每位成员、
头像 512×512 且 ≤500KB、`displayDescription.zh` 40–50 字、
`tags` 与 `quickPrompts` 各 3 条双语、`defaultInitPrompt` 与 `quickPrompts[0]` 一致、
`categoryId` 合法、无硬编码 Token。任一项不通过则构建失败。

构建产物在 `release/`（已 gitignore，可随时删除后重建）：

```
release/
├── report-agent-v3-1.0.0/         最终 Team 包，install:local 装的就是它
├── report-agent-v3-1.0.0.zip      提交审核用
└── report-agent-v3-1.0.0.zip.sha256
```

`build:team` 内部先由 `build-release.mjs` 产出带平台后缀的 staging，再裁剪成 Team 包
（删 `docs/`、`hooks/` 展平为 `hooks.json`、`run-node` 移入 `bin/`），校验通过后自动删除
staging——避免 `release/` 里留下两个看起来都像成品的目录。排查打包问题时用
`node scripts/build-team.mjs --keep-staging` 保留中间产物。

本地安装到 WorkBuddy：

```bash
npm run install:local   # 装到 ~/.workbuddy/plugins/marketplaces/my-experts/
```

## 提交

产物：`release/report-agent-v3-1.0.0.zip`，可提交 WorkBuddy 专家市场审核。

**提审前待办**：`avatars/` 下 6 张头像目前是同一张占位图，需替换为符合各角色定位的图；`plugin.json` 的 `author.email` 需填真实邮箱。
