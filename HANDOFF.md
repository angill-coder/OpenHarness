# OpenHarness 交接文档（HANDOFF）

> 给「完全没有上下文」的下一个 agent。**先完整读这份，再动手。** 最后更新：2026-08-11（见文末 §12 增量）。
> 用户要求：**一律用简体中文交流**（曾误用日语被纠正）。

---

## 0. 一句话现状

平台的「调研洞察汇报助手」已具备真实报告生成、逐维 Judge、LLM 自由改写 Optimizer、候选 Gate 和 WebUI 实验闭环。默认 `rubric_research.json` 保持原版，迭代版 **v2.3（六维 25 checks）** 独立保存在 `v2_rubric_research.json`；`research-report` Skill 使用四项开场输入（背景、待验证假设、重点素材、报告篇幅）和三段式交付结构。真实项目以 20-case 训练集及独立 testing 数据开展多版本实验；新 Session 会冻结创建时的 Rubric 和 Skill，判断某次实验时必须读取该 Session 的 `state.json` 与 `_session_skills`，不能只看仓库文件名推断。

---

## 1. 项目是什么

**OpenHarness** = 一个 **eval 驱动的 Skill 自动优化平台**（当前为 Git 仓库；以实际 checkout 路径为准，不依赖历史机器的绝对路径）。
闭环：Runner → Judge（LLM-as-judge，需人工标注做 meta-eval 校准）→ 失败聚类 → Optimizer（`switch_search` 或 `llm_rewrite`）→ 候选 Gate → 版本化 Store → 回归看板。

**铁律共识（用户认同，别违反）**：
- **结构定质量上限**：flow、交互协议、交付结构和 memory schema 由人设定；Optimizer 可以改写标记区内的 instructions，但不能修改冻结的任务契约和结构外壳。
- **rubric 和数据是杠杆，不能外包给弱工程师**；human_report 与人工分是用户不可外包的核心。
- **judge 校准一致率 ≥0.85 才允许开 Optimizer**（离线 `run_loop` 强制；app 的 advance 不强制）。

**两个产品并存**（按 `rubric["product"]` / `product_id` 派发，互不干扰）：
- `report-assistant`（**算数字型**经营月报，旧，`data/report_assistant/`）——4 维：data_accuracy/completeness/insight/conciseness。
- `research_insight`（**调研洞察汇报助手**，当前重点）——6 维（见 §3）。

环境随 checkout 所在机器变化：生产部署历史见 §9；本地实验可能运行在 macOS。不要从 HANDOFF 推断密钥、端口或依赖是否可用，启动前检查实际环境变量、`app/server.py --help` 和 WebUI 配置接口。密钥只通过环境变量或本地 gitignored 启动脚本注入，禁止写入仓库。

---

## 2. 架构：harness（离线引擎）+ app（Web 平台）

### harness/（离线引擎、数据准备与确定性评分）
- `schemas.py` SkillArtifact/EvalRecord。`store.py` 版本化。`runner.py` 批量跑+判分。`calibration.py` judge↔人工一致率（±1 容差，门槛 0.85）。`clustering.py` 失败聚类。`optimizer.py` 反思式提议（`FORBIDDEN={keyword_emphasis,buzzword_emphasis}` 挡 reward-hack）。`loop.py` 编排。`dashboard.py` 看板。
- `backend.py`（**已去 API**，平台即运行时，`get_backend` 不再看 key）：
  - `MockBackend` 算数字型（原样）。
  - `ResearchMockBackend` 六维型：按 23 个 `RESEARCH_DIRECTIVES`（22 质量 + 1 FORBIDDEN 的 buzzword_emphasis）输出**报告文本 + signals**。**signals 是 judge/clustering 的唯一事实源**。
  - `RecordedBackend` 真实型：按 (version, case_id) 查用户粘贴的真实报告文本，signals 为空。
- `judge.py`：保留 mock/离线评分，并提供 `dim_from_checks` 将真实 check 判定汇总为六维分。每项 `met/partial/miss` 对应 `1/.5/0`，维度分为 `1 + 4 × mean(checks)`；任一红线 check 为 `miss` 时该维封顶 2。
- `artifacts/rubric_research.json` = 默认六维 rubric 原版；`artifacts/v2_rubric_research.json` = v2.3 迭代版（25 checks）；`artifacts/rubric.json` = 算数字型（勿动）。
- `run_demo_research.py`：**离线自测闭环**（合成多 case + 模拟专家分）→ 校准 0.933、dev overall 2.17→4.56、采纳 12 版、buzzword_emphasis 被 gate 拒。`run_demo.py`：旧产品，2.58→4.75 不回归。**这俩是"闭环逻辑对不对"的试金石，改完 harness 先跑它们。**

### app/（stdlib HTTP + 单页原生 JS）
- `generator.py`：需求 → v0 Skill + Rubric；`research_insight` 会冻结任务契约，并把四项 intake 与篇幅换算写入 Skill。
- `session_core.py` / `session_eval.py` / `session_label.py`：分别负责 Session 状态、评测/候选结算和人工标注；新 Session 将母本 Rubric 复制到 `state.json`，后续改母本不会自动刷新旧 Session。
- `generation_jobs.py`：为目标版本编译 `_session_skills`，调用 WorkBuddy 生成全部 case 报告并写入 `generation_runs`。
- `judge_batch.py`：默认 `per_dimension`，每个维度独立调用 Judge；成功维度可保留，重试时只补缺失维度。报告或 Rubric 变化会使旧 Judgment 失效。
- `llm_client.py`：统一支持 `api`、`workbuddy`、`codex` 三种 LLM backend；Judge 和 Optimizer 可分别选择 backend、model 和 Codex reasoning effort。
- `server.py`：真实 Judge 入口是 `POST /api/run_judge_batch`；旧 `/api/run_judge` 已停用并返回 410。`POST /api/advance` 生成下一版候选并经真实评分 Gate 决定采纳或回滚。
- `persistence.py`：`sessions/<sid>/` 保存 `state.json`、`events.jsonl`、`check_judgments.jsonl`、输出与人工标签；诊断历史实验优先读这些落盘事实。

---

## 3. 六维 Rubric v2.3（research_insight 迭代版）

| 维度(字段) | 权重 | 目标 | 红线/封顶 | checks |
|---|---|---|---|---|
| 可回溯性与支撑充分 `traceability` | 0.28 | ≥4.2 | 任一红线 miss → 本维封顶 2；维度 <3 则 case 不合格 | T1论断有据/无编造🔴 T2忠实转述🔴 T3冲突不混用🔴 T4单源推断校准 T5不足留白🔴 T6口径完整 |
| 结构 `structure` | 0.15 | ≥4.0 | <3 触发人工复检 | S1摘要质量 S4摘要↔正文 S5章节结论先行 |
| 逻辑与故事线 `narrative` | 0.12 | ≥3.8 | <3 触发人工复检 | N2主线与论证推进 N4概念/口径一致 N5冲突呈现与决策含义 |
| 提炼与洞察 `insight` | 0.22 | ≥3.6 | — | I1提炼规律 I2归因/机制有效 I3趋势与风险校准 I4建议洞察有效 |
| 覆盖度 `coverage` | 0.08 | ≥4.0 | 答不了的问题不算遗漏 | V1关键问题覆盖与深度 V2必需段落齐 V3关键 Claim 无遗漏 |
| 表达与受众契合 `expression`（反向） | 0.15 | ≥3.8 | E5 miss → 本维封顶 2；维度 <3 人工复检 | E1精炼易扫读 E2结构化呈现 E3表图规范 E4遵守用户篇幅 E5风格禁令🔴 E6终稿化/严谨/易懂 |

- v2.3 的机器口径来源是 `harness/artifacts/v2_rubric_research.json`；共 **25 条 checks**，红线为 **T1/T2/T3/T5/E5**。默认 `rubric_research.json` 保持原版，两份文件不得互相覆盖；实际实验以 Session 冻结的 Rubric 为准。
- 每条 check 先单独判 `met/partial/miss`，再汇成一个 1–5 维度分；overall 为六维加权平均。目标 overall=4.0，Judge↔人工校准门槛=0.85。
- v2.3 将高度重叠项合并：例如摘要数量/结论性/排序并为 S1，主线/推进并为 N2，趋势/异常校验并为 I3。`noise_source_ids` 等 benchmark 专用检查移出主 Rubric，不再作为独立主 check。

**human_report 四条边界原则（用户拍板，建新 case 沿用）**：
1. 策略启示应答（列 expected_insight）、具体动作/投放留白（列 unsupportable_questions，硬答扣①）；
2. `noise_source_ids` 只用于 benchmark/数据质检，放**明显无关**素材；它不再是 v2.3 主 Rubric 的独立 check，但写作仍不得拿噪声充数；
3. 「≥2 独立信源否则降级待验证」只对**推断/归因类**（第三方面板单源可定论；仅访谈的机制类须标"定性/待验证"）；
4. 「趋势将持续/线性外推未来」算 `unsupported_extrapolation` 越界扣①。

---

## 4. 数据资产与会话（现状）

- `data/research_assistant/dataset.jsonl` 保留 3 条早期真实尺子（DS 时长、Surge AI、AI 产品留存）及坏样本/人工校准资产，主要用于离线回归和历史校准，不再是唯一正式实验入口。
- 当前真实项目数据按 `data/research-report/v1|v2|v3/data.json` 管理，这些运行数据默认被 Git 忽略。Session `meta.json.experiment_data.id` 决定数据版本；`OPENHARNESS_WB_DATASET_V1/V2/V3` 可分别覆盖路径，旧 `OPENHARNESS_WB_DATASET` 仅作 fallback。
- **不要凭目录名猜正在使用的数据**：启动后调用 `GET /api/generation/config`，核对 dataset、Skill、CLI 和可移植输出根。20-case 训练集与 testing 数据必须使用不同 `data.json`/Session，不能混写评测结果。
- 每个 case 的 `data.json` 是 Runner/Judge/Dashboard 共用入口，包含 turns、input_files、delivery_constraints 等。写作 Agent 读取 `materials/00_structured_data.json`；若有 ground truth，允许在构造阶段补充/纠错该文件，但 ground truth 本身不得作为额外文件暴露给写作 Agent。
- `app/sessions/` 中的会话是历史快照，不应假定数量固定。常见历史会话包括 `research-calib`、`research-run`、`president-report-llm`、`3d8fe03d` 等；新实验应新建 Session，避免修改旧实验的 Rubric、Skill 或评分。
- `make_bad_variants.py` 与 `bad_variants.*.jsonl` 是早期缺陷校准工具；其中 selection bias、noise 等标签可能仍用于 benchmark，但不代表 v2.3 存在同名独立 check。

---

## 4.5 部署给下属的报告生成 skill（Claude Code Agent Skill：research-report）

**这是生成真实报告的 Skill 母本**，位于 `skills/research-report/`（`SKILL.md` + `references/instructions.md`）。实验运行时会按 Session/版本编译并冻结到 `generation_runs/_session_skills/<session>/<version>/...`；诊断某版报告必须查看冻结副本，不能假定它等于当前母本。安装到个人 Skill 目录后通常需要新会话才会重新扫描。

- **四项开场输入**：①汇报背景 ②待验证假设 ③高质量/重点素材 ④报告篇幅。可以在一次回复中集中确认；用户已提供的只补缺。假设只能验证、部分支持、无法验证或证伪，不能迎合。
- **篇幅预算**：用户直接给字数时优先遵守；给页数时按每页不超过约 1000 个中文可见字符折算，表格单元格文字计入，Markdown 标记和空白不计。未指定时默认 3 页以内/3000 字以内；这是上限，不为凑满注水。
- **三段式交付结构**：①核心摘要（≤3 条结论）②核心发现（归因/机制放进对应发现）③启示与建议（趋势/风险放进相关行动语境）。不增加研究过程、素材清单或工作流状态章节。
- **证据呈现**：写作和逆向核验时内部保留 S-xxx/C-xxx 等来源定位；最终高管正文**不展示来源编号**。用户要求依据或证据表时单独提供，不混入正文。
- **终稿语言**：删除“待复算”“单一信源待验证”“证据不足建议补充研究”等中间工作流标签；真正影响决策的边界需转译为“当前能判断什么、不能判断什么、如何验证”。
- **v2.3 方法**：先建立论断卡/证据底稿，再确定唯一决策主线和不超过 3 项核心判断；每项按“判断 → 证据 → 原因/机制 → 边界/反例 → 决策含义”组织。表图仅在存在比较价值时使用，空单元格必须说明“未采集/不适用”等状态。
- `OPENHARNESS_EDITABLE_START/END` 内是 Optimizer 可改写区；四项 intake、三段结构、任务契约和版本 manifest 在结构层冻结。历史 directive（包括 `disclose_sample_bias`、`drop_noise`）仍可作为生成规则存在，但不等同于 v2.3 的独立 check。

## 5. ⚠️ 致命坑（务必记住）
1. **默认与迭代 Rubric 分文件保存**：新 Session 默认仍把 `rubric_research.json` 复制进 `state.json`；v2.3 位于 `v2_rubric_research.json`，必须在目标实验中显式选择/导入。WebUI 导入只替换并冻结当前 Session，不限制 Rubric `product` 与 `Session.product_id` 一致；评分维度、Judge 模式和 backend 跟随导入 Rubric，默认文件与其他 Session 不受影响。任何 Rubric 都不会自动刷新旧 Session；直接改 Rubric 文件或某个 `state.json` 后必须重启对应 server，WebUI 导入则即时更新内存并落盘。
2. **Rubric/报告变化会使旧 Judgment 失效**：批量 Judge 写入前后都有版本与 prompt SHA 守卫；不要把旧 `check_judgments.jsonl` 当成当前版本结果。真实结果看 `check_judgments.jsonl`，不是 mock 六维分。
3. **Judge 默认逐维调用**：`per_dimension` 会保留成功维度并只重试缺失项。遇到部分失败直接重跑同一版本，不要清空已成功结果或另造重复 Session。
4. **LLM Rewrite 候选不是提前采纳**：`pending_idx` 指向待评候选，`current_idx` 仍是父版；生成和 Judge 完整后才经 Gate 采纳/回滚。页面显示“候选已生成”不等于“已采纳”。
5. **改前端 JS 后做语法检查**：当前脚本在 `app/app.js`，运行 `node --check app/app.js`。改 Python 后用任务专用 `PYTHONPYCACHEPREFIX` 执行 `compileall`，避免系统缓存目录权限干扰。
6. **不要硬编码服务端口或误杀其他实验**：启动前确认 `--port` 和目标 Session；停止时只结束对应 PID/端口。Rubric 或 Session 状态变更后需要重启对应 WebUI 服务。
7. **密钥不得写入命令记录、代码或 HANDOFF**：只从环境变量/本地 gitignored 配置读取。历史文档里出现的 provider 名称不代表当前凭证仍有效。

---

## 6. 怎么运行

```bash
cd <当前-checkout>/app
python3 server.py --host 127.0.0.1 --port <独立端口>
```
- 真实循环：选择 Session → 确认数据集/Skill/Rubric 冻结副本 → 生成当前版本全部 case → `run_judge_batch` → 查看失败维度 → `advance` 生成候选 → 再生成/Judge → 等 Gate 结算。
- Judge/Optimizer 的 backend 与 model 在 WebUI 或 API 参数中独立选择；实验记录必须保留实际 backend/model/reasoning effort。
- 做校准：对同一批报告逐 check 填人工 `met/partial/miss`，与 `check_judgments.jsonl` 对比；一致率未达到 0.85 时先修 Rubric/Judge，不应解释 Optimizer 优劣。
- 离线自测：`cd harness && python3 run_demo_research.py`（六维）/ `python3 run_demo.py`（旧产品回归）。

---

## 7. 下一步 TODO（按优先级）

1. 用 v2.3 的 25 条 checks 补做人工校准，特别关注合并后的 S1/N2/I3/V1 是否仍有稳定区分度。
2. 在独立 testing 数据上复跑关键历史 Skill 版本，报告 generation model、Judge model、Rubric snapshot 和篇幅预算，避免跨实验口径混用。
3. 观察 E4 的可见字符算法对表格密集报告是否合理；页数不是排版页数，当前统一按每页约 1000 可见字符评估。
4. 继续检查 Structured Data 去重后是否遗漏关键时间点、冲突和口径；测试集与 20-case 训练集保持物理分离。
5. 为 v2.3 收集足够的高/中/低质量报告后，再决定是否继续合并 checks 或调整红线；不要仅凭单次 loop 分数改 Rubric。

---

## 8. 关键文件地图
- Rubric 文件：`harness/artifacts/rubric_research.json`（`research_insight` 默认原版）、`harness/artifacts/v2_rubric_research.json`（v2.3 迭代版）与 `harness/artifacts/rubric.json`（旧 `report-assistant`）；各文件独立保存。
- 生成 Skill 母本：`skills/research-report/SKILL.md` + `skills/research-report/references/instructions.md`；Session 冻结副本位于 `generation_runs/_session_skills/`。
- 真实 Judge：`app/judge_batch.py`、`app/server.py`、`app/llm_client.py`；结果落 `app/sessions/<sid>/check_judgments.jsonl`。
- Optimizer/Gate：`app/optimizer02.py`、`app/optimizer_pipeline.py`、`app/session_eval.py`、`app/session_core.py`。
- 数据构造与质检：`harness/_data_prepare.py`、`harness/_data_audit.py`、`harness/data_workflow.py`、`harness/data_quality_assets/`、`harness/CASE_DATASETS.md`。
- 实验事实：`app/sessions/<sid>/state.json`、`events.jsonl`、`generation_runs/`；判断实际 Skill、Rubric、模型和分数时以这些文件为准。
- 记忆：`~/.tclaude/projects/-Users-angill-Documents-New-project-OpenHarness/memory/`（openharness-project / backend-six-dim-refactor / user-prefers-simplified-chinese）。

---

## 9. 2026-07-20/21 增量（新机器 + iOA 鉴权 + 若干调整）

**机器/环境变了**：现在在 **Linux 新机 `/data/home/angillwang/OpenHarness`**（HANDOFF 早期写的 `/Users/angill/...` 是旧 Mac）。**Python 3.11.6，装了 `cryptography`**。同机还有**另一个独立项目 `/data/home/angillwang/ai-strategy-hub`**（FastAPI+Vite），它才是 nginx(443, `www.strhub.woa.com`)→`127.0.0.1:8000` 经公司 iOA 网关暴露的应用；**OpenHarness 与它无关**，但其 `backend/app/middleware/tai_auth.py` 是本次 iOA 接入的参考母本。

**① 端口**：`app/server.py` 默认端口 8765→**8080**。

**② 判分 LLM key 已配**：`app/start_real.sh`（**gitignored，含真实密钥，勿提交**）导出 `ANTHROPIC_API_KEY`(bianxie 中转 sk-…)、`ANTHROPIC_BASE_URL=https://api.bianxie.ai`、`LLM_API_STYLE=openai`、`ANTHROPIC_JUDGE_MODEL=claude-opus-4-8`（bianxie 若不认此 id 需改）。`_call_opus` 已支持第三方中转。

**③ iOA(TAI) 登录鉴权 + 按账号隔离标注历史**（本次大改，参照 ai-strategy-hub）：
- 新增 `app/auth.py`：解 `X-Tai-Identity`（JWE, alg=dir/enc=A256GCM，AppToken 前 32 字节作 AES-256 密钥），取 `LoginName` 作账号。token 走环境变量 `TAI_APP_TOKEN`/`TAI_APP_ID`（在 start_real.sh 里，值 `CRI7…`）。
- `server.py`：**所有 `/api/*` 严格鉴权，无有效身份一律 401**（`index.html` 公开）；新增 `GET /api/me`；登录账号透传进所有写接口与 `view(account)`。
- `session.py`：`human_checks/human_labels` **按账号命名空间**；`evaluate()` 只算账号无关基础分并暂存 `cur["_recs"]`，**人工分叠加+校准在 `view(account)` 现算**（线程安全，专家互不覆盖）；旧快照经 `_migrate_human_labels` 归 `_legacy`。
- `persistence.py`：`check_labels.jsonl` 每条带 `account`；`load_check_labels` 恢复为 `{version:{account:{case:{check}}}}`。judge 记录仍单份不分账号。
- `index.html`：401→整页"请经 iOA 网关访问"；启动 `GET /api/me` 显示登录人。
- **致命坑**：现在**直连 `:8080` 一律 401**（严格模式，用户拍板），本地自测也得经 iOA 或用离线脚本 `/tmp/oh_auth_test.py`（用测试 token 验证解密/隔离，不连服务器）。**未加角色限制**：任何 iOA 登录用户都能标注、各自留痕。
- **待用户/运维做的基建**：iOA 控制台把 OpenHarness 登记为独立应用（确认 Token=`CRI7…`）；加 nginx server 块反代其域名→`:8080` 并 `proxy_set_header X-Tai-Identity $http_x_tai_identity;`(+`X-Tai-Identity-Mode`)。
- **重启**：`bash app/start_real.sh --host 0.0.0.0`（Claude 的权限分类器会拦含密钥的启动脚本，须用户自己跑）。

**④ rubric T1 口径调整**：应用户要求"正文不写引用出处、但论断仍须有据可回溯"。改了生成 skill（`skills/research-report/`）与 v2 T1 判据（现保存于 `harness/artifacts/v2_rubric_research.json`）、相关 Session 快照、judge 提示词和落地文档——T1 从"论断挂出处"改为"论断可回溯(有据)，正文不印出处不扣分"。

**⑤ 报告生成 skill 母本微调**（`skills/research-report/`，本机路径，未装到 `~/.claude/skills`）：正文不印行内 `[S-xxx]`（改自检时核对可回溯）；禁止把"结论先行/归因"等写作原则字样当小标题写进正文。

**⑥ 打印**：`index.html` 加 `@media print`——专家人工标注表打印时白底黑字、字体放大、隐藏左右列。

---

## 10. 2026-07-28 增量（第二个优化器策略：LLM 自由改写 + 策略可插拔架构）

> ⚠️ **本次推翻了旧铁律"Optimizer(MVP) 只翻 directive、不动结构"**。现在结构层仍冻结，但 **instructions 这一层放开给 LLM 自由改写**。旧的翻开关策略原样保留、行为逐字不变，两个策略并存。

**动机**：翻开关优化器（`harness/optimizer.py`）动作空间被锁死在 23 个预定义 directive，探索空间太低；`research-run` 收敛终版 v16（`app/sessions/research-run/final_skill_v16.json` + `skills/research-report/DRAFT_from_v16.md`）暴露"收敛=开关翻尽"且因 mock 未覆盖 tag 顺手关掉 5 条红线的回退。用户要求：新增"由 LLM 写每一版 skill"的优化器，每轮把上一版关键信息传过去以避免回退。

**架构：策略层多份并存 + 评测流水线唯一共享**
- **策略层**（每份一个文件，同一接口 `propose(session, cur_skill, failures, context)` / `apply_proposal`）：`harness/optimizer.py`=`switch_search`（旧，翻开关）、`app/optimizer02.py`=`llm_rewrite`（新，LLM 自由改写整段 instructions）。经 `app/optimizer_registry.py` 的 `STRATEGY_REGISTRY` 派发。Session 加字段 `optimizer_mode ∈ {switch_search, llm_rewrite}`。
- **共享评测流水线**（策略无关，一份）：`compile_session_skill`(freeform 分支) → `generation_jobs`(WB CLI 生成) → `judge_batch`(真实判分) → `_apply_recorded` → gate。**runner/生成/判分/编译本就与策略无关**，`optimizer02` 只接在"产候选"这一个接缝上。

**关键机制：`pending_idx` 与 `current_idx` 解耦（防回退地基 + switch_search 零回归开关）**
- `current_idx`=当前最优/回滚锚点；`llm_rewrite` 产出的候选以 `adopted=False` 加入 versions、`pending_idx` 指向它，**绝不提前移动 current_idx**。
- 新增 `session_core._eval_target()` = `versions[pending_idx] if pending_idx is not None else _current()`；流水线读点（`generation_jobs`、`server` run_judge_batch 的 ver 默认/守卫/回写守卫、`view()`）全改指它。`pending_idx is None` 时退化为 `_current()`，**switch_search 全链路逐字不变**。
- **异步自动 gate**：候选真实判分完成后，`server` run_judge_batch 收尾调 `session_core.settle_pending_candidate()`：临时评估候选与父版真实分 → `optimizer_pipeline.evaluate_gate`（目标维/overall↑ ∧ 其它维不回退超 `no_regression.drop_tolerance` ∧ 无新红线）→ 采纳则 current_idx 移到候选、否则天然回滚（指针留父版，候选标 rejected）。verdict+reasons 两种都写 opt_history + `candidate_settled` 事件。`advance`/重启时兜底探测未结算的 pending 先 settle。

**迭代记忆（carry-forward，防回退核心，喂给 LLM）**：`optimizer_pipeline.build_optimizer_context()` 装配 {rubric 六维锚点+checks+红线+target、current_best(全文+每维分)、must_preserve(≥target 的维+未失败的 check)、open_failures(failure_report)、history(逐版改动+verdict+overall delta)、tried_rejected、guardrails}。

**红线守卫**（`optimizer02._redline_guard`）：候选生成后、进 WB 生成前，动态读取当前 Session Rubric 中所有 `redline:true` 的 checks，核对候选是否保留对应规则；任一被删则当场拒（不烧生成预算）。v2.3 文件为 **T1/T2/T3/T5/E5**，但每个 Session 仍以其冻结 Rubric 为准。

**freeform 表示与编译**：LLM 产出整段可编辑区正文，落 `skill.instructions.prose` + `mode="freeform"`。母本 `skills/research-report/references/instructions.md` 用 `<!-- OPENHARNESS_EDITABLE_START/END -->` 框住可改写质量方法；结构层的四项 intake、三段结构、任务契约、manifest 和 VERSION_RULES 在标记外，LLM 不可动。`skill_compiler` 的 freeform 分支整体替换可编辑区；无 `mode` 键则走旧 directive 路径。

**LLM 调用抽取**：`server.py:_call_opus/_extract_json` 抽到 `app/llm_client.py`（`call_llm`/`extract_json`），server 保留同名别名（判分链路字节等价），断 app→server 循环依赖，judge 与 optimizer02 共用同一条判分 LLM 线（需 `ANTHROPIC_API_KEY`）。

**新会话 `research-llm`**（`optimizer_mode=llm_rewrite`）：`app/seed_research_llm.py` 一次性建，复用 research-run 同 3 case，v0=母本可编辑区全文(freeform)。**用途=跑新策略闭环**（区别于 research-run 的翻开关 demo）。

**UI（第一版，单会话内）**：`index.html` 建会话加"优化器策略"下拉；`app.js` 的 `render()` 按 `optimizer_mode` 换按钮文案「✍️ LLM 改写下一版」、显示 pending 候选提示条 + freeform 正文 + rationale + gate verdict。**多会话分栏对比是紧跟的第二步**（后端已铺好：pending/candidate 全 per-session，候选是真实 version、天然支持并排 diff）。

**跑法（llm_rewrite 人在环闭环）**：开 `research-llm` → 先对 v0 跑 WB 生成 + 批量真实 Judge（否则 advance 被 gate 卡"未判分"）→ 点「✍️ LLM 改写下一版」得 pending 候选 v1（current_idx 不动）→ 对 v1 跑生成 + 判分 → 判分收尾自动 settle 采纳/回滚 → 看 opt_history 的 verdict。**需真实 LLM key**（判分 + 改写 + 守卫都走它）。

**回归**：`run_demo_research.py`(2.17→4.56/15 版) 与 `run_demo.py`(2.58→4.75/6 版) 均不变、harness `.py` 零改动；`app/tests`+`harness/tests` 57 全绿（新增 `test_llm_rewrite.py` 9 条覆盖 gate/freeform 编译/守卫/settle 采纳回滚）；四个磁盘会话 restore 一致。

**顺手修的数据坑**：`app/sessions/research-run/events.jsonl` 第 20 行两个 JSON 对象被写在同一行（历史 append 漏换行），导致 `load_events` 崩、research-run 打不开——已按 JSONL 拆回逐行（用 `json.raw_decode`），与本次功能无关但必修。

**新增/改动文件清单**：新增 `app/llm_client.py`、`app/optimizer_pipeline.py`、`app/optimizer02.py`、`app/optimizer_registry.py`、`app/seed_research_llm.py`、`app/tests/test_llm_rewrite.py`；改 `app/session_core.py`（游标/字段/结算/view）、`app/session_eval.py`（advance 派发 + `_advance_llm_rewrite`，switch_search 逻辑原样搬进 `_advance_switch_search`）、`app/skill_compiler.py`、`app/directive_registry.py`、`app/generator.py`、`app/server.py`、`app/generation_jobs.py`、`app/index.html`、`app/app.js`、`skills/research-report/references/instructions.md`（插 EDITABLE 标记）。

---

## 11. 2026-07-30 增量（总裁汇报助手 LLM 实验会话）

**新会话**：`app/sessions/president-report-llm/`，`product_id=research_insight`、`optimizer_mode=llm_rewrite`，已导入 `data/20260727_real_project_package/data.json` 的 20 条真实项目 case。创建脚本为 `app/seed_president_report_llm.py`，检测到同名会话时会拒绝覆盖。旧近似会话 `b27e80a8` 保留不动。

**需求契约 / V0**：
- `generator._research_requirement_contract()` 会把需求语义化为 `skill.instructions.requirement_contract`；2026-08-11 的母本契约包含：总裁受众；用户先给话题+原始素材；补问汇报背景、hypothesis、材料重点分布和报告篇幅；摘要/关键发现/启示三段式；素材文件只读且不编造/篡改；有比较价值时用表图；结论先行、精炼严谨。`president-report-llm` 创建更早，其冻结契约可能仍是三项输入，需读取 Session 状态确认。
- 契约与 `instructions.prose` 分开存。`skill_compiler` 编译 freeform 时固定拼成“契约 + LLM 可改写质量规则”；`optimizer02` 只改 prose，不能把产品需求迭代丢失。页面会分别展示“冻结任务契约”和“可改写质量规则”。
- 该会话创建时复制了当时的 Rubric；随后母本已升级为 v2.3，因此不能再用历史 SHA 或当前母本推断该会话实际 Rubric。请直接读取 `app/sessions/president-report-llm/state.json`。

**会话级 early-stop（与 rubric.target 分离）**：
- 新增 snapshot 字段 `optimizer_stop={overall_target,max_no_improvement}` 与 `optimization_progress`，API/UI 建会话可配置；本会话为 `overall_target=4.8`、`max_no_improvement=4`。
- 仅在真实 Judge 完整后看分，mock 占位分不触发停止。当前已采纳版 overall ≥4.8 即停；否则每个候选结算后，只有“候选被 Gate 采纳且 overall 创历史新高”才清零 streak，Gate 拒绝或 overall 未创新高都累计，连续 4 版即停。停止后后端禁用 advance，UI 展示原因。
- stop 是实验编排条件，**不改** rubric 自身 overall target=4.0，也不影响 `switch_search` 会话。

**失败重试**：报告生成仍是每 case 最多 3 次重试（共 4 次尝试）；通用 Judge/LLM 默认从 0 改为 2 次传输重试（`LLM_RETRIES` 可覆盖）；LLM Rewrite 独立默认 2 次（`LLM_REWRITE_RETRIES` 可覆盖）。HTTP 408/429/5xx、超时/网络错误会指数退避；整批 Judge 部分失败仍可在 UI 再点一次续跑未完成 case。

**沙箱与验证**：
- 历史 V0 编译路径和 hash 只对当次运行有效；重新编译或 Skill 母本变化后必须以 `_session_skills` 当前目录和事件记录为准。
- 当时的 App 46 项、Harness 20 项测试均通过；这是 2026-07-30 的历史验证记录，不代表当前分支测试数量。
- 本地服务启动方式：`cd app && source ./start_real.sh && python3 server.py --host 127.0.0.1 --port 8080`。打开页面后选 `president-report-llm`，先跑 v0 的 20-case 生成 + 批量真实 Judge，再点 LLM 改写下一版；之后按同样循环推进，满足任一 early-stop 自动停。

---

## 12. 2026-08-08—11 增量（Codex Provider、Rubric v2.3、篇幅预算、Structured Data 清洗）

### 12.1 Codex CLI Judge / Optimizer Provider（PR #23，已合入 Main）

- `llm_client.py` 新增 `codex` backend；当前支持 `gpt-5.6-sol`，reasoning effort 支持 low/medium/high/xhigh/max/ultra。
- Judge 与 Optimizer 分别保存所选 backend、model、reasoning effort；运行历史不能只写“GPT-5.6”，必须同时记录调用方式和 effort。
- Codex CLI 使用临时、只读工作目录和标准输入 prompt；`OPENHARNESS_CODEX_CLI_PATH` 可覆盖可执行文件发现路径。

### 12.2 Rubric v2.2 → v2.3（PR #24）

- v2.2 曾扩展到 36 条 checks；v2.3 将高度重叠项合并为 **25 条**，维度权重和 overall 目标不变。
- 当前 checks：T1–T6、S1/S4/S5、N2/N4/N5、I1–I4、V1–V3、E1–E6；红线为 T1/T2/T3/T5/E5。
- 关键口径：T1 合并“论断有据”和“无事实编造”；T2 保留忠实转述；T4 的单源降级主要约束归因/机制/普遍性推断，权威统计面板的直接事实可单源陈述；I3 合并趋势、风险、异常验证与外推边界；E6 增加中间分析状态向最终汇报语言的转化。
- v2.3 同步重写 `research-report` 方法：证据底稿 → 决策主线 → 洞察 → 终稿 → 逆向核验。标题可简短，关键是标题或首句能表达章节中心判断。

### 12.3 用户控制报告篇幅（PR #24）

- intake 从三项变为四项；固定数据交互只需模拟用户回复“控制在 X 页以内”，换算规则由 Skill/平台负责。
- case 可带 `delivery_constraints={max_pages,max_chars,chars_per_page,counting_rule}`；数据生成器默认 3 页、每页 1000 个中文可见字符。
- Judge 为 expression 维传入 `delivery_constraints` 和确定性计算的 `report_stats`。`visible_chars` 去除 Markdown 标记与空白、计入表格单元格文字；E4 直接比较 `visible_chars` 与 `max_chars`，不再主观估固定页数。

### 12.4 Structured Data 清洗与重建（PR #24）

- `input_files` 中目标为 `materials/00_structured_data.json` 的文件视为旧 Structured Data/发布目标，**不会再作为普通 Source 回灌给生成 Worker**；其余 source 才进入证据重建。
- Worker 先清洗、去重、合并和提炼，再输出 Evidence。访谈要还原“问题—回答—限定条件”，过滤寒暄、口头禅、无答案问题和重复追问。
- 同义逐字稿/整理稿合并为一条高密度 Evidence，并在 `source_ref` 保留多个可回查位置；不再输出 `company_views`、`timeline`、`weekly_updates` 等重复索引层。
- “一页周报约 30–80 条 Evidence”只是常见密度参考，不是硬上限；是否保留取决于该条是否影响事实、趋势/机制、冲突、口径或证据边界。

### 12.5 PR #24 验证与已知基线问题

- PR 分支：`codex/rubric-v23-and-structured-data`；目标 Main；包含 v2.2、用户篇幅、v2.3 和 Structured Data 清洗四个功能 commit，未重复包含 PR #23 的 Codex Provider。
- 相关回归：App 96 项、Harness 30 项通过；Python compileall、`node --check app/app.js`、`git diff --check` 通过。
- 当前 Main 基线有两项 Dashboard 路径契约测试同样失败；本机安装的 macOS WorkBuddy 还会影响一项 Windows CLI 发现测试。相关文件未被 PR #24 修改，不能把这三项误判为本次回归。

---

## 13. 2026-08-13 增量（Rubrics Loop Feedback AI 验收）

- 验证实验默认运行 **2 轮 Skill 迭代**，每轮仍完整复用现有 Runner → Judge → Skill Optimizer → Gate；`skill_iteration_rounds` 可在 1–5 之间调整，默认 2。
- 两轮完成后进入 `Feedback Acceptance Evaluator`：逐条读取原始 Feedback、baseline 报告、两轮迭代报告、对应 Rubrics 变更与 Judge 信号，返回 `followed / partially_followed / not_followed / unable_to_judge`、稳定性、失败层级、报告原文证据和下一步建议。它不自动改 Rubrics。
- 验收证据必须逐字存在于对应 Markdown 报告；模型返回的虚构引文会被过滤。结果落在 `sessions/<sid>/rubrics_loop/acceptance/<experiment_id>.json`，并同步到 Experiment 记录和历史列表。
- 断点续跑是硬约束：Skill Loop 完成后先落 `loop_completed_at`；AI 验收失败时只重试验收，不重跑 Runner/Judge。每条 Feedback 独立落盘，重试只补未完成条目。服务重启后，旧的 queued/running Experiment 会自动标记为可从断点重试。
- 同一 Candidate + Rubric SHA 禁止重复创建验证实验，失败必须原地重试原 Experiment Session，避免重复消耗 Runner/Judge/Optimizer token。
- Rubrics Loop 第三阶段现为“验证与决策”，展示 Skill 迭代进度、Feedback AI 验收证据和三个人工动作：采纳新 Rubrics、继续优化、保留原 Rubrics。
- 关键文件：`app/feedback_acceptance.py`、`app/rubrics_loop.py`、`app/server.py`、`app/rubrics_loop_ui/`、`app/tests/test_feedback_acceptance.py`。
