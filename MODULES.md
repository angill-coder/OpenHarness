# MODULES.md — OpenHarness 多人协作模块划分与接口契约

> 本文是**分工与防冲突的执行契约**。先读 `HANDOFF.md`(项目全貌+铁律) 与 `CLAUDE.md`(开机须知)，本文只解决"多人并行怎么不撞车"。
> 与用户及团队**一律用简体中文**。最后更新：2026-07-22（首次建立；已完成 session.py / index.html 两处物理拆分，见 §4）。

---

## 0. 三条最重要的防冲突规则（违反必生冲突）

1. **契约文件单独 PR、全员知会**：`harness/schemas.py`、`report["signals"]` 字段集、`/api/*` 路由形状、`rubric_research.json` 维度/权重/checks —— 这四类是跨模块共享的隐式约定（见 §3）。改它们必须单独 PR，不得夹带在功能改动里。
2. **数据/状态文件串行改**：`harness/artifacts/rubric_research.json`、`app/sessions/*/state.json`（改完必须**重启 server** 才生效，HANDOFF §5.1）。这些文件在 git 里是整体冲突源——只由 **M5 owner** 或单一会话负责人动。
3. **改 `app/app.js` 后先 `node --check app/app.js`**；改 `rubric_research.json` / `sessions/*/state.json` 后重启 server。（HANDOFF §5.2）

---

## 1. 系统天然分层与冲突热点

OpenHarness = eval 驱动的 skill 优化平台，天然分成 **两大件 + 三类资产**：

```
┌─ harness/  离线引擎（纯 stdlib、确定性）——「算法闭环」
│    schemas → store → runner → judge → clustering → optimizer → loop → dashboard
│    backend.py（Mock / ResearchMock / Recorded 三后端）
│    workbuddy_runner.py → workbuddy_batch/*（真实外部报告生成）
│    artifacts/rubric*.json（评测尺子）
│
├─ app/  Web 平台（stdlib http + 单页 JS）——「运行时 / 模型评测」
│    server.py(路由/鉴权入口) · session.py+session_{core,eval,label}.py(会话编排)
│    persistence.py(落盘) · generator.py(需求→v0) · auth.py(iOA 鉴权)
│    index.html(单页 UI 结构+样式) · app.js(前端逻辑)
│
└─ 资产层：rubric/落地文档 · data/(数据集+坏变体) · skills/research-report/(下属用的生成 skill)
```

**冲突热点（多人最容易撞车）**：`session*.py`、`server.py`、`index.html`/`app.js`、`rubric_research.json`、`sessions/*/state.json`。
拆分目标：让这几处尽量**单一 owner 串行改**，其余按清晰边界并行。§4 的两处物理拆分已把 `session.py`(原 635 行) 与 `index.html`(原 592 行) 拆成可多人并行的结构。

---

## 2. 七模块划分

| 模块 | 职责 | 主要文件 | owner 画像 |
|---|---|---|---|
| **M1 评测算法核心** | 判分/聚类/优化器/编排的算法逻辑 | `harness/{judge,clustering,optimizer,loop,runner,store,dashboard}.py` + `run_demo*.py` | 算法/评测强的人 |
| **M2 后端模拟与生成** | signal 模拟、23 directive 集、v0 skill 生成 | `harness/backend.py` + `app/generator.py` | 懂 rubric↔directive 映射的人 |
| **M3 平台服务（后端）** | HTTP 路由、鉴权、落盘、会话编排 | `app/{server,auth,persistence}.py` + `app/session.py` + `app/session_{core,eval,label}.py` | 后端/Web 服务 |
| **M4 前端 UI** | 单页三栏交互、标注表、打印 | `app/index.html`（结构+样式） + `app/app.js`（逻辑） | 前端 |
| **M5 Rubric 与数据资产** | 六维尺子、数据集、坏变体 | `harness/artifacts/rubric_research.json`、`调研…落地文档.md`、`data/research_assistant/*` | **用户本人（不可外包）** |
| **M6 生成 skill 母本** | 下属实际用的 research-report skill | `skills/research-report/*` | 用户 + 提示词工程 |
| **M7 真实外部执行** | Runner→WorkBuddy、产物验收、attempt 重试、provenance | `harness/{workbuddy_runner,external_run_models,report_artifact,run_external}.py` + `harness/workbuddy_batch/*` | 执行框架/基础设施 |

> M3 内部拆分后可 2–3 人并行（core/eval/label 各一 owner，共享 `Session` 组合类）；M4 拆分后 UI（HTML/CSS）与逻辑（JS）可分人。

---

## 3. 接口契约表（跨模块共享约定 —— 冻结，改动走规则 §0.1）

### 契约 A — `harness/schemas.py`：全局数据契约（M1/M2/M3 全依赖）

**`SkillArtifact`**（被版本化/被优化的东西；`structure` 冻结，其余按 L1/L2/L3 迭代）：

| 字段 | 类型 | 说明 | 谁改 |
|---|---|---|---|
| `id` / `version` / `parent_version` | str | 身份与血缘 | store/optimizer |
| `structure` | dict | flow/subagents/memory schema —— **人工设定，MVP 不动** | 人(结构级优化) |
| `instructions` | dict | `{prose, directives{name:bool}}` ← **L1 优化面** | optimizer |
| `few_shots` | list | ← L2 优化（未实现） | — |
| `memory_content` | dict | ← L3 优化（未实现） | — |
| `changelog` | str | 本版改动说明 | optimizer |

**`EvalRecord`**（一条 trace 的评测结果）：
`run_id` · `skill_version` · `dataset_split` · `case_id` · `input` · `trace`(执行过程，聚类/归因靠它) · `output` · `scores{dim:int}` · `judge_reasoning{dim:str}` · `judge_checks{check_id:float}` · `flagged[str]` · `case_failed_gate:bool`(命中红线)。
> app 侧还会在运行期给 record 附加非 schema 字段 `score_source`（`recorded`/`mock`），见 `session_eval._apply_recorded`。加它到 schema 前先知会 M3。

**约定**：schema 字段的增删改**必须单独 PR、全员知会**，绝不夹带在功能改动里。这是最重要的一条防冲突规则。

---

### 契约 B — `report["signals"]` 字段集：M2 ↔ M1 契约

signals 是 **judge/clustering 的唯一事实源**（HANDOFF §2）。`ResearchMockBackend.run` 产出的 signal key 与 M1 `judge.score_report_research` 的锚点必须**成对改、成对 review**，否则 mock 评分与聚类静默错位。

23 个 signal（`harness/backend.py` `ResearchMockBackend.run`），按维度：

| 维度 | signals（true=失分点，由对应 directive 关闭触发） |
|---|---|
| traceability | `conflict_mishandled` `hard_answered_unsupportable` `uncited` `single_source_not_downgraded` `fabricated`🔴 `metric_caveat_unnoted` `sample_bias_undisclosed` |
| structure | `summary_background` `body_not_pyramid` `mece_violation` |
| narrative | `concept_drift` `no_narrative_flow` |
| insight | `insight_restate` `insight_listing` `noise_cited` `overclaim` `outlier_unchecked` |
| coverage | `key_claims_missed` |
| expression(反向) | `bushi_ershi` `no_charts` `length_mismatch` `imprecise_wording`※ `buzzword`(reward-hack) |

- 每个 signal 精确对应一个 `RESEARCH_DIRECTIVES`（23 个，22 质量 + 1 FORBIDDEN `buzzword_emphasis`）。
- ※ `imprecise_wording` **定义但不入 mock 评分**（对应 `require_rigorous_wording`，真实-only 杠杆，HANDOFF §4.5）。M2 加这类"真实-only"信号时须在此标注，M1 不得给它锚点。
- **M2 加新 directive/signal 时**：同一 PR 内同步 M1 的 judge 锚点 + `rubric_research.json` 的 check（M5 联署）。

---

### 契约 C — `/api/*` 路由：M3 ↔ M4 契约

前后端边界。**request/response 形状变更先在此表登记**，M4 才好并行。所有 `/api/*` 严格 iOA 鉴权，无有效身份一律 **401**（`index.html` / `app.js` 公开）。

| 方法 | 路由 | request | response 要点 | Session 方法 |
|---|---|---|---|---|
| GET | `/` `/index.html` | — | HTML | — |
| GET | `/app.js` | — | JS（**新增**，公开静态） | — |
| GET | `/api/me` | — | `{login_name, display_name, email}` | — |
| GET | `/api/session?id=` | query id | 完整会话视图(见 `view()`) | `view(account)` |
| GET | `/api/sample_data?id=/product=` | query | `{rows, n}` | — |
| GET | `/api/sessions` | — | `{sessions:[{id,product_id,requirement,current_version,n_versions,n_cases,created_at}]}` | — |
| GET | `/api/generation/config` | — | WB 配置与 `ready/error` | `GenerationJobService.configuration` |
| GET | `/api/generation?id=/session_id=` | query | 单任务或 Session 最近 20 个任务 | `GenerationJobService.get/list` |
| POST | `/api/session` | `{requirement, product_id?}` | 会话视图 | `__init__` |
| POST | `/api/data` | `{id, rows?/use_sample?/use_configured?}` | 会话视图 | `import_data` |
| POST | `/api/rubric` | `{id, weights?, target?}` | 会话视图 | `edit_rubric` |
| POST | `/api/advance` | `{id}` | 会话视图 + `advance_result` | `advance` |
| POST | `/api/import_output` | `{id, case_id, report_text, version?}` | 会话视图 | `import_output` |
| POST | `/api/import_judgment` | `{id, case_id, scores:{dim:int}, reasoning?, version?}` | 会话视图 | `import_judgment` |
| POST | `/api/upload_report` | `{id, case_id, filename, content_b64, version?}` | 会话视图 | `import_output`(解析后) |
| POST | `/api/run_judge` | — | `410`（单 case Judge 已停用） | — |
| POST | `/api/run_judge_batch` | `{id, version?}` | `{summary,results,state}`（需 Judge key+网络） | `judge_cases` + `set_judge_checks_batch` |
| POST | `/api/generation/start` | `{id, case_ids?, idempotency_key?}` | `202 {reused,job}` | `GenerationJobService.start` |
| POST | `/api/generation/retry` | `{job_id,idempotency_key?}` | `202 {reused,job}` | `GenerationJobService.retry` |
| POST | `/api/generation/cancel` | `{job_id}` | `202 {job}` | `GenerationJobService.cancel` |

> "会话视图" = `Session.view(account)` 的返回结构（`session_core.py`）：`session_id/product_id/backend/detected/n_cases/splits/current_version/rubric/versions/curve/current_eval/current_failures/evaluation_mode/judge_progress/dims/dim_zh/target/can_advance/opt_history/history`。

---

### 契约 D — `rubric_research.json` 维度/权重/checks：M5 对所有人的契约

**谁都不许私改 rubric**（HANDOFF 铁律「rubric 是杠杆不能外包」）。M1 的 judge、M4 的 `rubricDimsHtml`、M6 的 skill 锚点全跟着它走。改完**重启 server**。

| 维度(字段) | 权重 | 目标 | 红线/封顶 | checks |
|---|---|---|---|---|
| `traceability` 可回溯性 | 0.28 | ≥4.2 | **<3 一票否决**(hard_floor=3) | T1–T7 |
| `structure` 结构 | 0.15 | ≥4.0 | 摘要铺陈/对不上=2封顶 | S1–S4 |
| `narrative` 逻辑与故事线 | 0.12 | ≥3.8 | 概念矛盾=2封顶 | N1–N2 |
| `insight` 提炼与洞察 | 0.22 | ≥3.6 | 复述/引噪=2封顶 | I1–I5 |
| `coverage` 覆盖度 | 0.08 | ≥4.0 | —(答不了的不算漏) | V1–V3 |
| `expression` 表达(反向) | 0.15 | ≥3.8 | "不是,而是"/注水=2封顶 | E1–E5 |

- overall 目标 **4.0**。gates: `red_line_traceability` / `no_regression`。
- 模型 Judge 逐条判 checks，再汇成一个 1–5 分（不单独改权重）。

### 契约 E — Runner ↔ WorkBuddy 真实外部执行

- 对外唯一入口：`harness.runner.run_external_cases(ExternalRunRequest)`。
- `ExternalRunRequest`/`ExternalBatchResult` 定义在
  `harness/external_run_models.py`；字段变更按跨模块契约处理。
- `harness/workbuddy_runner.py` 是唯一 façade；Session、Web 和 Loop
  不得直接 import `workbuddy_batch`。
- WB `repetition=1`；无有效报告的条件重试由 façade 负责。
- `status=success` 不代表报告成功；只有 `report_artifact.py` 验收通过
  才能进入 import/Judge。
- `human_report`、rubric、人工/Judge 分不得进入 CaseSpec、workspace
  或生成 prompt。
- `harness/workbuddy_batch/*` 是内部实现，协议调整需同步契约测试。
- Web 侧只调用 façade；`generation_jobs.py` 负责异步 Job、Session 锁、
  版本/hash 冻结和批量导入。
- 当前 Phase A 使用固定 Skill 路径，只验证生成导入，不参与真实版本 Gate。

---

## 4. 大文件拆分现状（第一个 PR，已完成）

为让 M3/M4 内部可并行，两个大文件已物理拆分。**其他人基于拆分后的结构开工。**

### 4.1 `session.py`(原 635 行单类) → 组合式 mixin
`Session` 由 Core/Eval/Label/Generation 四个 mixin 组合。**对外契约不变**：`server.py` 仍只用 `session_mod.Session(...)` 与 `Session.restore(...)`；`session.DIMS`/`_dims_from_rubric` 等模块级名字经 `session.py` 回导出。

| 文件 | mixin | 内容 |
|---|---|---|
| `session_core.py` | `SessionCore` | 状态骨架：`__init__`/`to_snapshot`/`_save`/`restore`/`view`/`_version_view`/`_split_counts` + 版本管理 `_add_version`/`_current`；模块常量 `DIMS`/`DIM_ZH` 与 helper `_dims_from_rubric` |
| `session_eval.py` | `SessionEval` | 跑分与推进：`import_data`/`evaluate`/`_apply_recorded`/`_rec_view`/`_output_summary`/`edit_rubric`/`advance`/`_plateau_note` |
| `session_label.py` | `SessionLabel` | 真实产物与模型评分：`import_output`/`import_judgment`/`set_judge_checks`/`set_judge_checks_batch`/`_norm_checks` |
| `session_generation.py` | `SessionGeneration` | WB 报告批量幂等导入：`import_generated_outputs` |

方法解析顺序 = Core→Eval→Label→Generation（无重名遮蔽）。跨 mixin 调用在组合类上运行时解析。

### 4.2 `index.html`(原 592 行) → 抽 `app.js`
`<script>…</script>` 整体外移到 `app/app.js`，HTML 内改为 `<script src="/app.js"></script>`。`server.py do_GET` 加 `GET /app.js` 静态路由（`text/javascript`，公开不鉴权，与 `index.html` 一致）。

> 拆分后回归验证全绿：`run_demo_research.py`(2.17→4.56/采纳15版)、`run_demo.py`(2.58→4.75/采纳6版)、4 个真实会话 restore+view、`advance`(v0→v1) 全链路、`node --check app.js`。

---

## 5. 协作工程约定

- **分支策略**：按模块开短生命周期分支 `feat/m1-judge`、`feat/m3-server`、`feat/m4-ui` 等。**M2↔M1 的成对契约改动（signal↔judge 锚点）放同一分支同一 PR。**
- **验收闸门（写进 PR 模板）**：
  - 改 `harness/*`：跑 `run_demo_research.py`(六维) + `run_demo.py`(防旧产品回归)——这俩是 M1/M2 的 CI 试金石。
  - 改 M7：额外跑 `python -m unittest discover -s harness/tests -p 'test_*.py' -v`。
  - 改 `app/session*.py`：`python3 -c "import session"` + restore 一个真实会话 + `advance` 冒烟。
  - 改 `app/app.js`：`node --check app/app.js`。
  - 改 `rubric_research.json` / `sessions/*/state.json`：重启 server 复核。
- **敏感文件**：`app/start_real.sh` 已 gitignore（含真实 iOA/LLM 密钥）——**任何人别提交、别让它进 PR**（HANDOFF §9）。Claude 权限分类器也会拦含密钥的启动脚本，须用户自己跑。
- **进程清理**：后台残留测试 server 用 `pkill -f "server.py"` 清理（`kill %1` 在本环境不可靠）。
- **契约变更流程**：动 §3 任一契约 → 单独 PR + 标题标 `[contract]` + 在本文件对应表登记新形状 + 通知受影响模块 owner。
