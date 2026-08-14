# OpenHarness · Skill 评测与迭代平台（Web）

页面化工作台把「需求 → 生成 v0 → 导入数据 → Runner CLI 批量生成/导入报告 → 模型批量 Judge → 迭代出下一版」串成一条链路。Web 层为 Python 标准库 + 单页原生 JS；真实报告生成可选 WorkBuddy CLI 或 Codex CLI，真实评分需要配置 Judge 模型。

## 启动

```bash
cd app
python3 server.py                 # 默认 http://127.0.0.1:8080
# 可选: --port 8000  --host 0.0.0.0
```

打开浏览器访问 `http://127.0.0.1:8080`。Mock 评测无需 API key；真实报告既可以手工粘贴，也可以通过页面一键调用 Runner CLI 自动生成并导入。

### 实时评测看板与数据契约

平台顶部的“实时评测看板”打开同源页面 `/dashboard/`。平台与看板共享
`persistence.base_dir()` 指向的实验目录：默认是 `app/sessions/`，部署或测试环境可通过
`OPENHARNESS_SESSIONS_DIR` 覆盖。前端始终使用稳定的虚拟路径 `app/sessions`，不会依赖宿主机绝对路径。

All experiments use the same `app/sessions/<sid>/` storage contract. Session metadata declares
`experiment_owner`, `experiment_data`, and `experiment_optimizer` in `meta.json` or `state.json`;
the Dashboard does not maintain per-user paths or Session ID allowlists.

每次实验变更都会即时写入 `state.json`，报告追加到 `outputs.jsonl`，Judge 结果追加到
`check_judgments.jsonl`（兼容旧 `judgments.jsonl`）。看板每 2 秒检查文件树摘要；摘要变化后重新读取对应会话并刷新版本、Case、报告和评分。`GET /api/local/config` 可用于检查当前生效的物理目录、数据集路径和允许读取的文件契约。
Data v1, v2, and v3 all use this same Session pipeline. A Runner import writes the
report plus its compact `generation_trace` into the corresponding `outputs.jsonl`
row. A Judge import writes checks, reasoning, hashes, and `judge_trace` into the
corresponding `check_judgments.jsonl` row. The Dashboard therefore has no
owner-specific or data-version-specific ingestion branch.

### 一键 Runner CLI 配置

默认配置已经对应当前仓库；需要覆盖时设置：

```bash
export OPENHARNESS_WB_DATASET_V1=../data/research-report/v1/data.json
export OPENHARNESS_WB_DATASET_V2=../data/research-report/v2/data.json
export OPENHARNESS_WB_DATASET_V3=../data/research-report/v3/data.json
export OPENHARNESS_WB_SKILL_PATH=../skills/research-report
export OPENHARNESS_WB_MODEL=deepseek-v4-pro-ioa
export OPENHARNESS_WB_PARALLEL=20
export OPENHARNESS_WB_MAX_REPORT_RETRIES=3
export OPENHARNESS_WB_OUTPUT=../generation_runs

# WorkBuddy CLI 不在 PATH 时设置
export OPENHARNESS_WB_CLI_PATH=/path/to/workbuddy

# 改用 Codex CLI Runner（默认 gpt-5.6-sol / medium）
export OPENHARNESS_RUNNER_LLM_BACKEND=codex
export OPENHARNESS_RUNNER_CODEX_MODEL=gpt-5.6-sol
export OPENHARNESS_RUNNER_CODEX_REASONING_EFFORT=medium
export OPENHARNESS_CODEX_CLI_PATH=/path/to/codex  # 已在 PATH 时可省略

python3 server.py --host 127.0.0.1 --port 8080
```

其他可选项：

- `OPENHARNESS_WB_TIMEOUT`：单 case 超时，默认 900 秒；
- `OPENHARNESS_WB_STALL_TIMEOUT`：无输出超时，默认 180 秒；
- `OPENHARNESS_WB_MAX_CONCURRENT_JOBS`：全局同时运行的批任务数，默认 1；
- `OPENHARNESS_SESSIONS_DIR`：Session 落盘目录，测试/多实例隔离时使用。

模型 Judge 配置：

```bash
export ANTHROPIC_API_KEY=...
export ANTHROPIC_BASE_URL=https://api.anthropic.com
export ANTHROPIC_JUDGE_MODEL=claude-opus-4-8
export LLM_API_STYLE=anthropic          # 第三方 OpenAI 兼容网关填 openai
export OPENHARNESS_JUDGE_PARALLEL=20    # 默认 20；可在 Web UI 调整
```

API 后端的输出 token 预算可按角色拆分，避免为了长篇 Optimizer 改写而把
Judge 的并发预留也一起放大：

```bash
export LLM_MAX_TOKENS=8000                 # Judge/其它 API 调用的 fallback
export LLM_DIAGNOSIS_MAX_TOKENS=6000       # Optimizer 全局失败诊断
export LLM_OPTIMIZER_MAX_TOKENS=12000      # Optimizer 目标级结构化 Patch
export LLM_GUARD_MAX_TOKENS=2000           # 仅 llm_scratch V0 红线检查
export OPTIMIZER_MAX_INSTRUCTION_CHARS=8000 # 候选 Skill 正文总字符上限
export OPTIMIZER_MAX_NET_GROWTH_CHARS=200   # 单轮相对 champion 净增长上限
export OPTIMIZER_MAX_PATCH_OPERATIONS=6     # 单轮 add/replace/delete 总操作数
```

Optimizer 空响应会在调用层按 `LLM_REWRITE_RETRIES` 重试；若仍为空，API
返回 `code=empty_llm_response`，并在 Session、event 与迭代资源日志中记录
`finish_reason`、token 用量和耗时等脱敏元数据，不保存 reasoning 原文。

Judge 与 LLM Optimizer 都支持三种调用方式：`api`、`workbuddy`、`codex`。
选择 Codex CLI 时默认使用 `gpt-5.6-sol` 和 `medium` 推理力度；Web UI
可分别为 Judge 与 Optimizer 调整推理力度。Codex CLI 不在 `PATH` 时设置：

选择 `llm_scratch` 生成 V0 时，V0 正文起草及其红线完整性守卫固定使用
Codex CLI `gpt-5.6-sol`、`medium`；Rubric 仍从仓库受控模板加载，不由
模型自由改写。

```bash
export OPENHARNESS_CODEX_CLI_PATH=/path/to/codex
export OPENHARNESS_JUDGE_CODEX_MODEL=gpt-5.6-sol
export OPENHARNESS_JUDGE_CODEX_REASONING_EFFORT=medium
export OPENHARNESS_OPTIMIZER_CODEX_MODEL=gpt-5.6-sol
export OPENHARNESS_OPTIMIZER_CODEX_REASONING_EFFORT=medium
```

Judge/Optimizer 的 Codex 调用使用非交互、临时会话和只读沙箱。Codex Runner
同样使用无状态临时会话，但只对每个 case 的隔离 workspace 开启
`workspace-write`，以便写入 `deliverables/report.md`；冻结 Skill 与材料之外的
OpenHarness 文件不会放进该 workspace。

调研汇报数据统一安装在 `data/research-report/v1|v2|v3/`，每个目录的 `data.json` 是 Runner、Judge 和 Dashboard 共用的唯一入口。Session `meta.json` 中的 `experiment_data.id` 决定使用 v1、v2 还是 v3；原始 source 始终以 `data.json` 所在目录为相对路径根。这些目录已被 Git 忽略，只用于本地运行。`GET /api/generation/config` 可检查三个实际路径。

`OPENHARNESS_WB_DATASET_V1/V2/V3` 可分别覆盖三个入口。旧的 `OPENHARNESS_WB_DATASET` 仍保留兼容；若设置，会作为未单独配置版本的统一 fallback。页面显示“运行配置不可用”时，优先检查 dataset、Skill 和 CLI 路径。

报告生成和 Judge 的默认并发均为 20。当前版本不设置人为安全上限；实际并发不会超过待处理 case 数量，并受本机资源、Runner CLI 和模型服务容量约束。

Web UI 可从当前 Runner 后端支持列表中选择模型并设置最大并发；WorkBuddy 默认 `deepseek-v4-pro-ioa`，Codex 默认 `gpt-5.6-sol` / `medium`。任务会记录后端、模型和推理力度；「仅重试失败 case」默认显示原任务模型和并发。一次任务耗尽内部重试后，仍可创建新的失败 case 重试任务。

> 内置样例：算数字型读 `data/report_assistant/dataset.jsonl`（没有先跑 `python3 ../data/report_assistant/build_dataset.py`）；调研洞察型读 `data/research_assistant/dataset.sample.jsonl`。「用内置样例」按钮按会话产品自动选。

## 两类产品

平台按需求描述（或 `product_id`）自动识别两类产品，各用各的 rubric 与维度：

| 产品 | 触发 | 维度 |
|------|------|------|
| **算数字型 report-assistant** | 经营月报 / 周报 / 简报等 | 数据准确性 / 完整性 / 洞察 / 简洁性（4 维）|
| **调研洞察 research_insight** | 需求含"调研/洞察/素材/访谈/高管报告"等，或 `product_id=research_insight` | 可回溯性 / 结构 / 逻辑与故事线 / 提炼与洞察 / 覆盖度 / 表达（6 维）|

## 页面输入（左列自上而下）

1. **需求描述 → 生成 V0**：填一段对产品的描述，点「生成 V0」。调研洞察产品固定读取 `skills/research-report` 唯一基线，并按基线实际内容初始化已启用 directive；其它产品仍由 generator 生成 v0。
2. **导入数据**：调研报告可直接点「加载当前 WB 数据集」，也可粘贴 JSONL、JSON 数组或 `openharness-wb/v1` 的 `{cases:[...]}`。统一数据中的 `human_report` 保存人工报告，但不会发送给 WB 生成模型或模型 Judge；Judge 使用 `structured_data` 核验报告。
3. **一键真实生成**：中列「真实运行 · WB CLI」点击「一键生成并导入报告」，前端显示逐 case 进度；case 启动后立即显示为生成中，无有效报告自动额外重试 3 次，每份成功报告产出后立即导入冻结版本。
4. **批量模型 Judge**：点击「批量 Judge 全部 case」。系统以有限并发为每个 case 单独调用模型，并把逐-check结果汇总为六维分。

## 一键生成并导入报告

```text
前端按钮 → GenerationJob → OpenHarness Runner → WorkBuddy
→ Artifact Validator → 条件重试 → Session 批量幂等导入
```

行为约束：

- HTTP 启动立即返回，页面通过轮询更新，不会被长任务阻塞；
- 同一个 Session 同时只允许一个真实生成任务；
- 任务启动时完整复制 `skills/research-report`，只在现有 `references/instructions.md` 中写入当前版本新增 directive，并冻结版本、基线 hash 和执行目录 hash；
- 生成期间禁止替换数据、修改 Rubric、推进 Skill 或手工覆盖报告；
- WorkBuddy 每个 case 的 `repetition=1`，只有没有有效报告时才重跑，最多额外 3 次；
- 通过 `deliverables/report.md` 验收的报告才会导入；
- 每次 attempt 完成并捕获报告/清单后删除临时 workspace，不重复保留原始素材副本；
- trace 保留聚合 `2_events.jsonl`、请求、结果和 stderr；不落盘逐 round 的 `stdout.jsonl`，并过滤逐 token `stream_event`；
- 多个报告只触发一次 `evaluate()` 和一次 Session 保存；
- 部分成功会保留并导入成功报告，页面可「仅重试失败 case」；
- 服务重启后历史任务仍可查看；执行中的任务标为 `interrupted`，不会静默重复执行。

报告生成和模型 Judge 仍是两个显式步骤，不会自动推进 Skill。`skills/research-report` 是调研报告的唯一结构与基础指令来源；每次生成从该基线复制，并叠加当前 Session 累计打开、且基线尚未包含的 directive。

## 批量模型 Judge

Web UI 已切换为 `model_only`：不再提供人工维度评分、人工逐-check标注或 Judge 校准面板。Rubric 的 checks 仍然保留，它们是模型 Judge 的评分标准。

```text
一次点击
  → 当前 Skill 版本的全部 case
  → 每个 case 独立 Prompt（按维度路由的任务信息 / structured data + 报告 + rubric checks）
  → 最多 OPENHARNESS_JUDGE_PARALLEL 路并发
  → 单 case 失败隔离
  → 成功结果一次性写入 Session 并重评
```

行为约束：

- 全部 case 报告到齐后批量 Judge 按钮才启用，后端也会拒绝不完整批次；
- 模型调用或 JSON 解析失败只影响对应 case，整批其它 case 继续；
- 模型必须返回 rubric 中每一条 check，否则该 case 判为失败；
- Judge 期间报告或 Skill 版本发生变化，旧结果拒绝写入；
- 调研洞察会话必须全部 case 完成模型 Judge，才允许点击「生成下一版」；
- 完成前不展示 mock 占位分的曲线和失败聚类。

## 核心交互节奏

- 每导入/推进一次，中列展示**当前版本的 skill**（打开了哪些 directive、来自什么优化提议、结构冻结部分），右列刷新分数曲线、失败模式和当前 rubric。
- `llm_rewrite` 会话的当前版本全部 case Judge 完成后，点 **「▶ 生成下一版 skill」**：optimizer 先汇总全部 case 的 check 级 Failure Inventory，并为最多 5 个主要候选按 check 等额抽取可回放证据。第一次调用 Diagnosis LLM，要求覆盖所有主要候选、结合历史冷却信息完成归因并选择唯一主目标；若根因是 `data`、`judge`、`replay_protocol` 或 `mixed`，到此阻断且不浪费 Patch 调用。第二次调用 Patch LLM，只提供该主目标的 3–5 条完整「报告原句—素材证据—Judge 判定—期望改法」，且不得重新选目标，只能返回 `add/replace/delete` 结构化 patch。平台本地校验目标一致性、证据逐字复制、红线保留声明、唯一锚点及总字符/净增长/操作数预算；任何 `add` 都必须配套删除一条重复规则。候选随后进入真实生成与 Judge，Gate 始终与历史 champion 比较。硬失败持平时，dev overall 至少提升 0.02 且通过 `test` holdout 才允许采纳；新建 LLM loop 默认连续 8 个候选未产生新 champion 后停止。
- **生产 Skill 与 Harness 严格隔离**：V0 起草只接收去标识化的内容要求，不接收 rubric 结构、权重、分数、Gate、champion、holdout 或采纳策略；Patch LLM 也只接收可执行的目标规则。`production_skill_policy.py` 在 V0、Patch 和编译三层拒绝评测元数据；编译器会清理旧会话的历史评分章节，并从最终 `instructions.md` 删除 OpenHarness 占位标记和 directive ID。完整 rubric、Diagnosis、Judge 和 Gate 记录仅存于 Harness 状态与迭代日志。
- **每一版 skill 和 rubric 都呈现在页面上**：版本条可点，看板分数曲线逐版累积。
- 优化器无更多可提议 → 提示**收敛/平台期**，并诊断是否需要回去改结构。

## Rubric 可编辑（成败杠杆）

左列「编辑 Rubric」可改维度权重和 overall 目标，保存为**新的 rubric 版本**（r1/r2…）并立即重评。改权重会看到 overall 与达标情况随之变化——这就是"rubric 定义什么是好"的直接体现。

## 与 harness 的关系

本 app 是 `harness/` 的**模型评测编排 + 页面外壳**，不重复实现算法：

| app 文件 | 职责 |
|---------|------|
| `generator.py` | 需求描述 → v0 skill + rubric（离线启发式 / Claude 路径）|
| `session.py` | 会话组合入口；把 harness 的自动 `run_loop` 拆成页面可逐版驱动的步骤 |
| `session_generation.py` | 真实报告的批量、幂等导入 |
| `generation_models.py` | GenerationJob/Case 状态契约 |
| `generation_jobs.py` | 后台执行、进度、取消、重试、版本校验 |
| `judge_batch.py` | 当前版本全部 case 的独立 Prompt、并发模型 Judge 与失败隔离 |
| `server.py` | stdlib `http.server`，JSON API + 托管页面 |
| `index.html` | 单页 UI（输入 + 版本演进 + 报告生成 + 批量 Judge + 看板）|

算法本体（runner/judge/clustering/optimizer）复用 `harness/`。Web 应用只保留模型 Judge 链路，不包含人工评分与 meta-eval 校准逻辑。

## API（供调试）

| 方法/路径 | 作用 |
|-----------|------|
| `POST /api/session` `{requirement, product_id?}` | 建会话，生成 v0 |
| `GET /api/session?id=` | 当前会话完整状态 |
| `POST /api/data` `{id, rows? / use_sample?}` | 导入数据 |
| `POST /api/rubric` `{id, weights?, target?}` | 编辑 rubric（存新版本）|
| `POST /api/advance` `{id}` | 全部 case 模型 Judge 完成后生成下一版 skill |
| `POST /api/import_output` `{id, case_id, report_text, version?}` | 存平台跑出的真实报告文本 |
| `POST /api/import_judgment` `{id, case_id, scores:{dim:score}, reasoning?, version?}` | 存平台 LLM-judge 六维分（覆盖 mock）|
| `POST /api/run_judge_batch` `{id, version?, case_ids?}` | 并发 Judge 当前版本绑定数据集（可选子集）；越界 case_id 会被拒绝，返回 summary/results/state |
| `GET /api/generation/config` | 查看 WB 运行配置与预检状态 |
| `POST /api/generation/start` `{id, case_ids?, idempotency_key?, model?, parallel?}` | 仅在当前版本绑定数据集内，按指定模型/并发后台生成并自动导入；越界 case_id 会被拒绝 |
| `GET /api/generation?id=<job_id>` | 查询任务/逐 case 状态 |
| `GET /api/generation?session_id=<sid>` | 查询 Session 最近任务和历史 |
| `POST /api/generation/retry` `{job_id, idempotency_key?, model?, parallel?}` | 按可选新模型/并发仅重跑未导入 case |
| `POST /api/generation/cancel` `{job_id}` | 请求在当前 CLI 轮次结束后安全停止 |
| `GET /api/sample_data` | 返回内置样例数据集（按会话产品）|
| `GET /api/sessions` | 列出所有已恢复会话 |

## 说明与边界（v0 演示范围）

- **落盘持久化**：需求描述、每一版 skill/rubric、报告和模型 Judge 结果都落盘，重启自动恢复（见下）。
- **离线确定性**：mock 后端的输出质量由 skill 的 directive 决定，judge 按 rubric 锚点对照 human report 打分，所以"打开正确 directive → 分数上升"是 rubric 的必然而非脚本。算数字型走 `MockBackend`，调研洞察型走 `ResearchMockBackend`（吐报告文本+signals）。
- **Mock 无需 API key**：真实报告可手工粘贴，也可由本机 WorkBuddy CLI 生成；Judge 仍需单独配置相应 LLM key。
- **调研 v0 使用唯一基线**：`generator.py` 不再为调研产品另造 Skill 结构；v0 directive 状态直接读取 `skills/research-report/references/instructions.md` 的声明。
- **优化目前只做 L1**（打开 directive）；L2（few-shot）/L3（memory）与结构级优化是后续。

## 落盘与恢复

每个会话落在 `app/sessions/<sid>/`：

| 文件 | 内容 |
|------|------|
| `meta.json` | 不可变元信息：sid / **原始需求描述** / product_id / 创建时间 |
| `events.jsonl` | **追加式完整历史**，包括 `generation_import` / `run_judge_batch` / `version_adopted` 等事件。|
| `state.json` | 最新完整快照（需求/rubric/所有版本 skill/current/opt_history/数据）。派生量恢复后重算。|
| `outputs.jsonl` | 平台跑出的**真实报告文本**（按 版本×case 追加）。恢复时重建 `report_outputs`。|
| `judgments.jsonl` | 平台 LLM-judge 对真实报告的**六维评分**（按 版本×case 追加）。恢复时重建 `report_judgments`。|
| `check_judgments.jsonl` | 批量模型 Judge 的逐-check结果与 reasoning。|
| `generation_jobs/<job_id>.json` | WB 后台任务状态、冻结版本/hash、逐 case attempt 与导入结果。|
| `iterations/<version>/manifest.json` | 单轮关联 ID、父子版本、输入 hash、生成/Judge run ID 与状态。|
| `iterations/<version>/optimizer_summary.json` | Optimizer 的全量 Failure Inventory 摘要、Diagnosis 候选与唯一主目标、完整 3–5 条目标实验样例、结构化 patch、字符预算实耗、指令 diff 与两阶段模型调用摘要；未生成候选的阻断诊断也追加在 champion 版本。|
| `iterations/<version>/dialogue_contract.json` | 各 case 的追问/回答/缺失字段、交付完整性与报告静态指标。|
| `iterations/<version>/gate_decision.json` | 候选 vs 历史 champion 的完整分数向量、词典序硬失败、holdout、失败 case 变化及 current/champion 指针。|
| `iterations/<version>/resource_usage.json` | 生成、Judge、Optimizer 的调用数、重试、耗时与字符量汇总。|

- **每次变更即写盘**（create/import/rubric/judge/advance 之后立即），崩溃/重启不丢。
- **迭代日志不复制大文本**：报告、Prompt 和完整 Judge 明细仍以现有 JSONL/job 文件为准；五个迭代文件只保存 hash、统计和相对引用，便于按 `iteration_id` 串起整轮。
- **重启自动恢复**：`server.py` 启动时扫描 `sessions/` 全部载入，页面可继续之前的迭代。
- 右列「操作历史（已落盘）」面板实时展示 events 时间线。
- `GET /api/sessions` 列出所有已恢复会话。
- 想清空：删掉 `app/sessions/` 目录即可。

## 回归测试

```bash
python3 -m unittest discover -s ../harness/tests -p 'test_*.py' -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
node --check app.js
```
