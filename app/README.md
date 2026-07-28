# OpenHarness · Skill 评测与迭代平台（Web）

页面化工作台把「需求 → 生成 v0 → 导入数据 → WB CLI 批量生成/导入报告 → 模型批量 Judge → 迭代出下一版」串成一条链路。Web 层为 Python 标准库 + 单页原生 JS；真实报告生成需要本机可用的 WorkBuddy CLI，真实评分需要配置 Judge 模型。

## 启动

```bash
cd app
python3 server.py                 # 默认 http://127.0.0.1:8080
# 可选: --port 8000  --host 0.0.0.0
```

打开浏览器访问 `http://127.0.0.1:8080`。Mock 评测无需 API key；真实报告既可以手工粘贴，也可以通过页面一键调用 WB CLI 自动生成并导入。

### 一键 WB CLI 配置

默认配置已经对应当前仓库；需要覆盖时设置：

```bash
export OPENHARNESS_WB_DATASET=../data/20260727_test_data/data.json
export OPENHARNESS_WB_SKILL_PATH=../skills/research-report
export OPENHARNESS_WB_MODEL=deepseek-v4-pro-ioa
export OPENHARNESS_WB_PARALLEL=20
export OPENHARNESS_WB_MAX_REPORT_RETRIES=3
export OPENHARNESS_WB_OUTPUT=../generation_runs

# WorkBuddy CLI 不在 PATH 时设置
export OPENHARNESS_WB_CLI_PATH=/path/to/workbuddy

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

默认读取仓库内的 `data/20260727_test_data/data.json`；该文件已被 Git 忽略，只用于本地运行。`GET /api/generation/config` 可检查生效配置。页面显示“运行配置不可用”时，优先检查 dataset、Skill 和 CLI 路径。

报告生成和 Judge 的默认并发均为 20。当前版本不设置人为安全上限；实际并发不会超过待处理 case 数量，并受本机资源、WB CLI 和模型服务容量约束。

Web UI 可为每次报告生成任务单独填写模型和最大并发；默认模型为 `deepseek-v4-pro-ioa`。任务会记录实际模型；「仅重试失败 case」默认显示原任务配置，也允许在点击前改用新的模型或并发。一次任务耗尽内部重试后，仍可创建新的失败 case 重试任务。

> 内置样例：算数字型读 `data/report_assistant/dataset.jsonl`（没有先跑 `python3 ../data/report_assistant/build_dataset.py`）；调研洞察型读 `data/research_assistant/dataset.sample.jsonl`。「用内置样例」按钮按会话产品自动选。

## 两类产品

平台按需求描述（或 `product_id`）自动识别两类产品，各用各的 rubric 与维度：

| 产品 | 触发 | 维度 |
|------|------|------|
| **算数字型 report-assistant** | 经营月报 / 周报 / 简报等 | 数据准确性 / 完整性 / 洞察 / 简洁性（4 维）|
| **调研洞察 research_insight** | 需求含"调研/洞察/素材/访谈/高管报告"等，或 `product_id=research_insight` | 可回溯性 / 结构 / 逻辑与故事线 / 提炼与洞察 / 覆盖度 / 表达（6 维）|

## 页面输入（左列自上而下）

1. **需求描述 → 生成 V0**：填一段对产品的描述，点「生成 V0」。调研洞察产品固定读取 `skills/research-report` 唯一基线，并按基线实际内容初始化已启用 directive；其它产品仍由 generator 生成 v0。
2. **导入数据**：调研报告可直接点「加载当前 WB 数据集」，也可粘贴 JSONL、JSON 数组或 `openharness-wb/v1` 的 `{cases:[...]}`。统一数据中的 `ground_truth` 作为评测参考：不会发送给 WB 生成模型，但会与报告、Rubric 一起发送给模型 Judge。
3. **一键真实生成**：中列「真实运行 · WB CLI」点击「一键生成并导入报告」，前端显示逐 case 进度；无有效报告自动额外重试 3 次，成功报告批量导入冻结版本。
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
  → 每个 case 独立 Prompt（任务信息 + 报告 + ground truth + rubric checks）
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
- 当前版本全部 case Judge 完成后，点 **「▶ 生成下一版 skill」**：optimizer 读当前失败模式 → 提一个最便宜的改动（L1 打开某 directive）→ dev gate 验证（目标维度↑ 且其它不塌且不引红线）→ 通过则成为新版本，否则记为被拒版本。
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

算法本体（runner/judge/clustering/optimizer）复用 `harness/`。人工标注和 meta-eval 校准代码暂为历史兼容保留，但 Web/API 主链路不再使用。

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
| `POST /api/run_judge_batch` `{id, version?}` | 并发 Judge 当前版本全部 case，返回 summary/results/state |
| `GET /api/generation/config` | 查看 WB 运行配置与预检状态 |
| `POST /api/generation/start` `{id, case_ids?, idempotency_key?, model?, parallel?}` | 按本次指定模型/并发后台生成并自动导入 |
| `GET /api/generation?id=<job_id>` | 查询任务/逐 case 状态 |
| `GET /api/generation?session_id=<sid>` | 查询 Session 最近任务和历史 |
| `POST /api/generation/retry` `{job_id, idempotency_key?, model?, parallel?}` | 按可选新模型/并发仅重跑未导入 case |
| `POST /api/generation/cancel` `{job_id}` | 请求在当前 CLI 轮次结束后安全停止 |
| `GET /api/sample_data` | 返回内置样例数据集（按会话产品）|
| `GET /api/sessions` | 列出所有已恢复会话 |

## 说明与边界（v0 演示范围）

- **落盘持久化**：需求描述、每一版 skill/rubric、报告和模型 Judge 结果都落盘，重启自动恢复（见下）。
- **离线确定性**：mock 后端的输出质量由 skill 的 directive 决定，judge 按 rubric 锚点对照 ground truth 打分，所以"打开正确 directive → 分数上升"是 rubric 的必然而非脚本。算数字型走 `MockBackend`，调研洞察型走 `ResearchMockBackend`（吐报告文本+signals）。
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

- **每次变更即写盘**（create/import/rubric/judge/advance 之后立即），崩溃/重启不丢。
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
