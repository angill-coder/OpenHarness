# OpenHarness 交接文档（HANDOFF）

> 给「完全没有上下文」的下一个 agent。**先完整读这份，再动手。** 最后更新：2026-08-11（见文末增量 §13）。
> 用户要求：**一律用简体中文交流**（曾误用日语被纠正）。
>
> 🟥 **先读文末 §13（2026-08-11 大更新）再看正文**：远程已合并 PR #17–23，架构大改（三后端 LLM 调用 / gate 重写 / 实时看板 / 数据质量产品 / 评测流程重构）。前面 §2、§5#6、§9 的「无 key / harness 无真 LLM / 端口 8765 / 严格 401 鉴权 / 非 git 仓库 / Linux 机器」等描述**已过时，冲突处一律以 §13 为准**。

---

## 0. 一句话现状

平台的「调研洞察汇报助手」六维评测闭环已**代码就绪并验证跑通**；已用 3 篇真实报告 + 素材建成数据集与校准集；**已把"下属实际用的报告生成 skill"部署成 Claude Code Agent Skill（`research-report`，见 §4.5）**——含开场 3 轮交互 + 3 段报告结构。用户此刻在 app 里跑优化器验证闭环（会话 `research-run`）。**剩下的主要是用户的人工活**：填专家六维分做校准、把坏样本骨架补成正文、用真实任务数据测 skill。

---

## 1. 项目是什么

**OpenHarness** = 一个 **eval 驱动的 skill 自动优化平台**（路径 `/Users/angill/Documents/New project/OpenHarness`，非 git 仓库）。
闭环：Runner → Judge（LLM-as-judge，需人工标注做 meta-eval 校准）→ 失败聚类 → Optimizer（反思式改写，只动 instructions/directives，不动结构）→ 版本化 Store → 回归看板。

**铁律共识（用户认同，别违反）**：
- **结构定质量上限**：flow/subagent/memory schema 由人 v0 设对；Optimizer(MVP) 只翻 directive，不动结构。
- **rubric 和数据是杠杆，不能外包给弱工程师**；human_report 与人工分是用户不可外包的核心。
- **judge 校准一致率 ≥0.85 才允许开 Optimizer**（离线 `run_loop` 强制；app 的 advance 不强制）。

**两个产品并存**（按 `rubric["product"]` / `product_id` 派发，互不干扰）：
- `report-assistant`（**算数字型**经营月报，旧，`data/report_assistant/`）——4 维：data_accuracy/completeness/insight/conciseness。
- `research_insight`（**调研洞察汇报助手**，当前重点）——6 维（见 §3）。

环境事实（**部分已在 2026-07 变化，以 §9 为准**）：本机机器可读文件用 JSON 非 YAML；读 pdf 用 `pypdf`（可用），读 docx 用 stdlib `zipfile`+`xml`（无 python-docx），xlsx 读不了。⚠️ 已过时：现在是**新机器（Linux，`/data/home/angillwang/OpenHarness`）、Python 3.11.6、装了 `cryptography`**；判分 LLM key 已配（bianxie 中转，见 §9），非"无 key"。

---

## 2. 架构：harness（离线引擎）+ app（Web 平台）

### harness/（纯离线、确定性、stdlib）
- `schemas.py` SkillArtifact/EvalRecord。`store.py` 版本化。`runner.py` 批量跑+判分。`calibration.py` judge↔人工一致率（±1 容差，门槛 0.85）。`clustering.py` 失败聚类。`optimizer.py` 反思式提议（`FORBIDDEN={keyword_emphasis,buzzword_emphasis}` 挡 reward-hack）。`loop.py` 编排。`dashboard.py` 看板。
- `backend.py`（**已去 API**，平台即运行时，`get_backend` 不再看 key）：
  - `MockBackend` 算数字型（原样）。
  - `ResearchMockBackend` 六维型：按 23 个 `RESEARCH_DIRECTIVES`（22 质量 + 1 FORBIDDEN 的 buzzword_emphasis）输出**报告文本 + signals**。**signals 是 judge/clustering 的唯一事实源**。
  - `RecordedBackend` 真实型：按 (version, case_id) 查用户粘贴的真实报告文本，signals 为空。
- `judge.py`：`score_report` 按 product 派发 → `score_report_bizreport` / `score_report_research`（读 `report["signals"]` 照六维锚点；traceability hard_floor=3 红线一票否决）。
- `artifacts/rubric_research.json` = 六维 rubric（权重/锚点/gates/target/**checks**）。`artifacts/rubric.json` = 算数字型（勿动）。
- `run_demo_research.py`：**离线自测闭环**（合成多 case + 模拟专家分）→ 校准 0.933、dev overall 2.17→4.56、采纳 12 版、buzzword_emphasis 被 gate 拒。`run_demo.py`：旧产品，2.58→4.75 不回归。**这俩是"闭环逻辑对不对"的试金石，改完 harness 先跑它们。**

### app/（stdlib http.server + 单页原生 JS，无依赖无 key）
- `generator.py` 需求→v0 skill+rubric（`research_insight` 分支读 rubric_research.json）。
- `session.py` 会话编排：产品无关维度（`self.dims` 从 rubric 取）；`import_data`（research 认 `human_report`）；`import_output`（贴真实报告文本）；`import_judgment`（贴平台 LLM-judge 六维分，**RecordedJudge**）；`_apply_recorded` 在 evaluate 后把真实报告/评分**覆盖** mock 分（有 judgment 的 case `score_source=recorded`，无则 `mock` 占位）。
- `persistence.py` 落盘：`sessions/<sid>/` 下 meta.json/events.jsonl/state.json/**outputs.jsonl**(真实报告)/**judgments.jsonl**(真实评分)。启动 `_restore_all` 恢复。
- `server.py` API：`/api/session`(建/查)、`/api/data`、`/api/labels`、`/api/rubric`、`/api/advance`、`/api/import_output`、`/api/import_judgment`、`/api/sessions`、`/api/sample_data`。
- `index.html` 单页 UI：左列（打开已有会话选择器 / 需求→V0 / 导入数据 / 编辑Rubric）、中列（版本演进+生成下一版 / 当前skill / 人工标注表 / 第4步导入报告文本+六维评分）、右列看板（校准/曲线/失败模式/当前Rubric）。`rubricDimsHtml()` 在左中右三处展开六维判据+目标+**checks**。

---

## 3. 六维 rubric（research_insight）

| 维度(字段) | 权重 | 目标 | 红线/封顶 | checks |
|---|---|---|---|---|
| 可回溯性与支撑充分 `traceability` | 0.28 | ≥4.2 | **<3 一票否决**(编造/混用冲突/无据硬结论) | T1挂出处 T2不编造🔴 T3冲突不混用🔴 T4单源降级 T5不足留白🔴 |
| 结构 `structure` | 0.15 | ≥4.0 | 摘要铺陈/对不上=2封顶 | S1摘要≤3纯结论 S2金字塔 S3MECE S4摘要↔正文 |
| 逻辑与故事线 `narrative` | 0.12 | ≥3.8 | 概念矛盾=2封顶 | N1主线贯穿 N2概念口径一致 |
| 提炼与洞察 `insight` | 0.22 | ≥3.6 | 复述/引噪=2封顶 | I1提炼成规律 I2归因/趋势/建议三要素 I3不过度外推 I4剔噪 |
| 覆盖度 `coverage` | 0.08 | ≥4.0 | —(答不了的不算漏) | V1可答全答 V2关键claim无漏 V3必需段落齐 |
| 表达与受众契合 `expression`(反向) | 0.15 | ≥3.8 | **"不是,而是"句式/注水=2封顶** | E1结论先行 E2结构化呈现 E3长度匹配 E4风格禁令🔴 |

- 完整锚点：`调研洞察汇报助手_Rubric落地文档.md`（人读）＝ `rubric_research.json`（机读）。checks 是本维展开的检查点，**仍汇成一个 1–5 分**（不单独打分、不改权重）。
- overall 目标 4.0，校准门槛 0.85。

**human_report 四条边界原则（用户拍板，建新 case 沿用）**：
1. 策略启示应答（列 expected_insight）、具体动作/投放留白（列 unsupportable_questions，硬答扣①）；
2. noise_source_ids 只放**明显无关**（被引用=剔噪失败扣④）；
3. 「≥2 独立信源否则降级待验证」只对**推断/归因类**（第三方面板单源可定论；仅访谈的机制类须标"定性/待验证"）；
4. 「趋势将持续/线性外推未来」算 `unsupported_extrapolation` 越界扣①。

---

## 4. 数据资产与会话（现状）

**`data/research_assistant/dataset.jsonl` = 3 条真实尺子**（唯一正式数据集，勿污染）：
- `rr-ds-timelen` DeepSeek 用户时长分析
- `rr-surge-eff` Surge AI 高人效
- `rr-retention` 元宝/DS/豆包留存
每条含 sources[](切片配 S-id) + human_report(supported_claims/key_claim_ids/expected_insights/unsupportable_questions/noise_source_ids/traps)。原始素材+抽出的正文在 `data/<案名>/`（`vF报告_正文.md`、`原始素材_正文.txt`）。

**`make_bad_variants.py`**：读某 case 的 human_report 半自动派生坏报告骨架+建议六维分+reasoning，`--into-session <sid>` 合并进会话。已支持缺陷：hardanswer/overclaim/single_source/conflict/metric_caveat/selection_bias/outlier/noise/listing/summary/style。生成物 `bad_variants.<case>.jsonl`（骨架含【填写】）。

**三个 app 会话**（`app/sessions/`）：
- **`research-calib`** 27 案 = 3 好报告(真实正文+judge草案分, overall 4.57/4.63/4.85) + 24 坏变体(已补具体正文, overall 3.22~3.78, 10 条红线)。**用途=校准**（judge分 vs 用户人工分）。driven by recorded → **跑不动优化器**。
- **`research-run`** 3 案（v0，纯 mock，未贴报告）。**用途=跑优化器闭环**（用户正在这里点"生成下一版"验证平台 work）。v0 dev≈2.12，一路可爬到 4.x 后收敛。
- **`ds-timelen`** 3 案 = DeepSeek 1 好 + 2 条手工填好的坏（3.22红线/3.18）。**用途=区分度 demo**。

---

## 4.5 部署给下属的报告生成 skill（Claude Code Agent Skill：research-report）

**这是"真实模式下下属实际用来产报告"的 skill**（区别于 harness 里 optimizer 迭代的 skill 结构）。母本在 `skills/research-report/`（`SKILL.md` + `references/instructions.md`），已安装到 `~/.tclaude/skills/research-report/` 和 `~/.claude/skills/research-report/`（本机两处兜底）。用户上传素材说"生成调研洞察汇报报告"即自动触发（靠 SKILL.md 的 description 命中；**新开 Claude Code 会话才会被扫描到**）。

- **内容 = "v-full"（22 质量 directive 全开，buzzword_emphasis 关）**——即 directive 空间上限版。它是**固定**的，不随 optimizer 自动更新；等 optimizer 在真实数据上收敛出最优 directive 子集后，需据此**手动重生成**这个 skill（用 `data/research_assistant/skill_full_运行提示词.md` 那套逻辑）。注意"全开"在真实 LLM 上不一定最优（长度 vs 覆盖/图表 有张力等），最优子集靠平台实测筛。
  - directive 集演进：v0 建时 14 → 2026-07-14 加 3（`verify_no_fabrication` 红线 tag `fabrication_risk`、`note_metric_caveat` tag `metric_caveat`、`disclose_sample_bias` tag `selection_bias`，均归可回溯性，checks T2/T6/T7）→ 2026-07-15 再加/拆到 **22 质量**：`pyramid_summary` **拆成** `summary_format`+`pyramid_body`（结构）、新增 `ensure_narrative_flow`（逻辑主线，N1）、`crosscheck_outliers`（洞察，tag `outlier_confound`，I5）、`cover_key_claims`（覆盖 V1/V2）、`require_rigorous_wording`（表达 E5）。
  - **`require_rigorous_wording` 是"真实-only"**：表达维 2 档被红线占、只剩 3/4/5 容 2 因子，硬加第 3 个会致贪心掩盖卡死；故它**不入 mock 评分**（backend 有 `imprecise_wording` 信号但 judge 不用），只作 directive+skill规则(第16条)+rubric锚点 E5，在真实生成/真实judge起作用。
  - mock 评分设计：coverage/structure/narrative 用**线性/有序评分**保证贪心每步单调可采纳（结构 summary封顶2、余下 body/mece 线性；coverage 三因子线性；narrative 无主线3→概念4→5）。`crosscheck_outliers` tag-gated，demo 里 inert（合成 case 无 `outlier_confound`），靠单测验证。demo 仍 2.17→4.56、采纳 15 版收敛。
- **开场 3 轮交互**（写在 flow step0 = 结构层，所有版本共有、与 directive 无关）：①汇报背景 ②材料假设 hypothesis ③标出高质量重点素材。三项都用进报告；**红线：hypothesis 只验证/证伪、不迎合**（素材不支持就如实证伪，否则违反可回溯性）。
- **3 段报告结构**（`references/instructions.md`）：①核心摘要（≤3 bullet 结论先行）②核心发现（**归因融进每条发现**）③对我们的启示与建议（**趋势判断融进相关建议**）。**不单列**归因/趋势/**素材清单**；来源一律行内 `[S-xxx]`。
- 同步点：`app/generator.py` 的 flow step0 + `RESEARCH_SECTIONS`(3段)、`skill_v0_research.json`/`skill_full_research.json` 的 flow step0、`dataset.jsonl` 各 case 的 `required_sections`(3段) 均已对齐这套交互与结构。**mock 评测不模拟交互、不读 required_sections**（覆盖度信号是 directive 驱动），所以这些改动只影响真实模式的一致性，不动 mock 结果。
- 产出的报告 → app 第4步「导入报告文本」+ LLM-judge 打分「导入六维评分」→ 平台评测。

## 5. ⚠️ 致命坑（务必记住）
1. **改 rubric / 会话 state.json 后必须重启 server** —— 会话是 server 启动时读进内存的，`/api/session` 返回内存态。改磁盘不重启看不到。标准动作：改文件 →`Ctrl+C`→`python3 server.py`→浏览器刷新。（页面内「编辑Rubric」按钮改权重是例外，走内存直改+落盘。）
2. **改 index.html 的 JS 后先语法检查**（曾因误删函数头导致整页 JS 崩、按钮全失效）：
   `python3 -c "import re;open('/tmp/c.js','w').write(re.search(r'<script>(.*)</script>',open('app/index.html').read(),re.S).group(1))" && node --check /tmp/c.js`
3. **mock vs recorded 泾渭分明**：优化器(advance)需要 mock signals（directive 驱动）才能聚类失败→提议；**recorded 报告 signals 为空→优化器直接"收敛"**。所以：跑优化器用"只有数据集、没贴报告"的会话（research-run）；校准用"贴了真实报告+judge分"的会话（research-calib）+ 用户人工分。
4. **真实报告的六维分不会自动算**——需平台 LLM-as-judge 打分后经 `/api/import_judgment` 粘回（RecordedJudge）。harness 里没有真 LLM。
5. **app 的 advance 不卡校准门槛**（离线 run_loop 才卡）。所以能直接跑优化器，但"优化的是未校准的 judge"这点要对用户说清。
6. 后台可能残留测试 server 进程（`kill %1` 在本环境不可靠）；用 `pkill -f "server.py --port 8765"` 清理。

---

## 6. 怎么运行

```bash
cd "/Users/angill/Documents/New project/OpenHarness/app" && python3 server.py   # 默认 8765, 无需 key
# 浏览器 http://127.0.0.1:8765 → 左上「打开已有会话」选 research-run / research-calib / ds-timelen
```
- 跑优化器：开 `research-run` → 看 v0 基线+失败模式 → 反复点「▶ 生成下一版 skill」→ 看曲线爬升、失败消退、直到"收敛"。
- 做校准：开 `research-calib` → 第3步给 27 案填**你的专家六维分** → 右列出一致率 → 差>1 的维度回去对齐锚点。
- 离线自测：`cd harness && python3 run_demo_research.py`（六维）/ `python3 run_demo.py`（旧产品，验不回归）。

---

## 7. 下一步 TODO（按优先级）

1. **用户跑通 research-run 优化器一轮**（进行中）——确认 app 里 v0→收敛 曲线出得来。
2. **用户填 research-calib 的人工分** → 把校准一致率跑到 ≥0.85（这是开 optimizer 的前提；<0.85 的维度回改 `rubric_research.json` 锚点 + 落地文档，然后重启 server）。
3. 坏变体正文核对/微调（`research-calib` 里 24 条已补，但可再打磨）；xlsx/docx 精确数字用户核对。
4. 攒更多真实 case（覆盖高/中/低质量），凑 30–50 条稳校准。
5. 之后才谈：真实数据上的逐版推进（人在环：平台跑vN→粘报告→粘judge分→advance）、L2(few-shot)/L3(memory)优化、结构级优化。
6. 收尾类：把其余五维 checks 也补进 `调研洞察汇报助手_Rubric落地文档.md`（json 已有，人读版目前只补了表达维那张表）。

---

## 8. 关键文件地图
- 规格：`调研洞察汇报助手_Rubric落地文档.md`（六维锚点+checks）、`调研洞察汇报助手_v0设计文档.md`（结构+schema）、`记忆与rubric分期设计.md`（专家反馈分流:rubric/skill-memory/个人-memory 三去处 + 生产vs评判 + 两期设计 + L1/L2/L3——memory层的 spec,尚未实现）。
- 引擎：`harness/*.py` + `harness/artifacts/rubric_research.json`。
- 平台：`app/*.py` + `app/index.html` + `app/README.md`（含「造校准集」流程）。
- **下属用的报告生成 skill**：`skills/research-report/`（母本）→ 已装到 `~/.tclaude/skills/` 和 `~/.claude/skills/`（改后须新开 Claude Code 会话才生效）。相关：`data/research_assistant/skill_v0_research.json`(v0全关)、`skill_full_research.json`(v-full全开)、`skill_full_运行提示词.md`(可粘的系统提示词)、`judge_运行提示词.md`(判分提示词)。
- **真实标注/校准(2026-07-17 加)**：app 支持 ①上传报告文件(md/txt/pdf/docx,`/api/upload_report`) ②逐 check 人工标注(满足/部分/不满足=1/.5/0,`/api/submit_check_labels`,落 `check_labels.jsonl`) ③页面按钮直调 **Opus 4.8** 判分(`/api/run_judge`,stdlib urllib,**需 `ANTHROPIC_API_KEY`+网络**,落 `check_judgments.jsonl`) ④逐 check 校准(人工 vs judge,`view.check_calib`)。维度分由 check 汇总:`judge.dim_from_checks`(1+4·mean,红线 check miss→封顶2)。**只动真实线,mock 优化器仍六维不变。** 会话 `real-eval`=干净标注会话(3案)。
- 数据：`data/research_assistant/`（dataset.jsonl / make_bad_variants.py / bad_variants.*.jsonl / README.md）、`data/<三案>/`（原始素材+正文）。
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

**④ rubric T1 口径调整**：应用户要求"正文不写引用出处、但论断仍须有据可回溯"。改了生成 skill（`skills/research-report/`）与 T1 判据（`harness/artifacts/rubric_research.json`、`app/sessions/real-eval/state.json`、judge 提示词、落地文档）——T1 从"论断挂出处"改为"论断可回溯(有据)，正文不印出处不扣分"。

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

**红线守卫**（`optimizer02._redline_guard`）：候选生成后、进 WB 生成前，用廉价 LLM 逐条核对候选是否仍保留 rubric 里 `redline:true` 的 **T2/T3/T5/E4**；任一被删则当场拒（不烧生成预算）。直接堵住 v16 那种"删红线"回退。

**freeform 表示与编译**：LLM 产出整段可编辑区正文，落 `skill.instructions.prose` + `mode="freeform"`。母本 `skills/research-report/references/instructions.md` 用 `<!-- OPENHARNESS_EDITABLE_START/END -->` 框住「## 硬规则…正反例」（结构层：开场三输入/三段结构/标题/manifest/VERSION_RULES 在标记外，LLM 不可动）。`skill_compiler` 加 freeform 分支：`mode=="freeform"` → 整体替换可编辑区，manifest/version_rules 保持基线原样；无 `mode` 键 → 走原 directive 路径（**switch_search 逐字不变**）。`directive_registry.load_editable_region` 供 v0 取全文。

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
- `generator._research_requirement_contract()` 会把需求语义化为 `skill.instructions.requirement_contract`，本会话固定了：总裁受众；用户先给话题+原始素材；补问汇报背景、hypothesis、材料重点分布；摘要/关键发现/启示三段式；素材文件只读且不编造/篡改；数据密集处图表；结论先行/金字塔/MECE/简洁严谨。
- 契约与 `instructions.prose` 分开存。`skill_compiler` 编译 freeform 时固定拼成“契约 + LLM 可改写质量规则”；`optimizer02` 只改 prose，不能把产品需求迭代丢失。页面会分别展示“冻结任务契约”和“可改写质量规则”。
- 本次**没有修改** `harness/artifacts/rubric_research.json`；新会话 rubric 与该文件逐对象相等，核验 SHA-256（规范化 JSON）均为 `1ffb10b85ca0097186dbb73c54facd6ecc0c88c88b778518ffed89ab0bcda816`。

**会话级 early-stop（与 rubric.target 分离）**：
- 新增 snapshot 字段 `optimizer_stop={overall_target,max_no_improvement}` 与 `optimization_progress`，API/UI 建会话可配置；本会话为 `overall_target=4.8`、`max_no_improvement=4`。
- 仅在真实 Judge 完整后看分，mock 占位分不触发停止。当前已采纳版 overall ≥4.8 即停；否则每个候选结算后，只有“候选被 Gate 采纳且 overall 创历史新高”才清零 streak，Gate 拒绝或 overall 未创新高都累计，连续 4 版即停。停止后后端禁用 advance，UI 展示原因。
- stop 是实验编排条件，**不改** rubric 自身 overall target=4.0，也不影响 `switch_search` 会话。

**失败重试**：报告生成仍是每 case 最多 3 次重试（共 4 次尝试）；通用 Judge/LLM 默认从 0 改为 2 次传输重试（`LLM_RETRIES` 可覆盖）；LLM Rewrite 独立默认 2 次（`LLM_REWRITE_RETRIES` 可覆盖）。HTTP 408/429/5xx、超时/网络错误会指数退避；整批 Judge 部分失败仍可在 UI 再点一次续跑未完成 case。

**沙箱与验证**：
- V0 已编译到 `generation_runs/_session_skills/president-report-llm/v0/ec45e61f6d27/research-report/`，目录 hash=`92d713416dced5fcc26ae85a94692a87b54b3494093ed1c77d451840fdfff7b5`。
- App 46 项测试、Harness 20 项测试全绿；`node --check app/app.js` 与相关 Python `py_compile` 通过；两个离线 demo 仍为 research 2.17→4.56、旧 report-assistant 2.58→4.75。
- 本地服务启动方式：`cd app && source ./start_real.sh && python3 server.py --host 127.0.0.1 --port 8080`。打开页面后选 `president-report-llm`，先跑 v0 的 20-case 生成 + 批量真实 Judge，再点 LLM 改写下一版；之后按同样循环推进，满足任一 early-stop 自动停。

---

## 12. 2026-08-10 增量（每轮五文件可观测日志）

为排查“分数不高但很快停止优化”、Judge/Gate 误判、对话契约丢失和重复调用浪费，新增 `app/iteration_trace.py`。每个实际生成或评测的版本都会在 `sessions/<sid>/iterations/<version>/` 下原子维护且只维护以下五个 JSON：

- `manifest.json`：`iteration_id`、父子版本、输入 hash、Optimizer/Generation/Judge 关联 ID、当前状态和既有大文件相对引用。
- `optimizer_summary.json`：失败证据量、目标维度、指令 diff 指标、rewrite/红线守卫调用 hash、字符数和耗时。
- `dialogue_contract.json`：逐 case 追问/回答/缺失字段、信息未齐时是否仍交付、报告静态指标与 hash。
- `gate_decision.json`：父子完整分数向量和 delta、红线/失败 case 变化、current/champion 指针、现行 gate 与“所有维度均不可回退”shadow decision。
- `resource_usage.json`：生成任务/尝试/报告字节/耗时、Judge 调用/重试/字符量/耗时、Optimizer 调用与耗时。

五文件不复制报告、完整 Prompt 或 Judge 逐 check 明细；原文仍在 `outputs.jsonl`、`check_judgments.jsonl`、`generation_jobs/` 和 generation trace 中。`iteration_id` 同步写入生成 job、Judge judgment、`version_proposed`/`generation_import`/`run_judge_batch`/`candidate_settled` 事件，用于串联整轮。对应单测为 `app/tests/test_iteration_trace.py`。

---

## 13. 2026-08-11 大更新（远程合并 PR #17–23 + 本轮 Mac 实测运维）

> ⚠️ **本节是当前最新事实**。与前面 §2/§5/§9 冲突处，一律以本节为准。

### 13.0 现状勘误（覆盖旧描述）
- **开发机**：当前工作副本在 **Mac `/Users/angill/Documents/New project/OpenHarness`**（§9 写的 Linux `/data/home/angillwang/...` 是另一台/旧环境）。Python 3.12（Framework 版，注意缺 CA 证书，见 13.1）。
- **仓库**：**已是 git 仓库**，远程 `git@github.com:angill-coder/OpenHarness.git`，主分支 `main`（§1「非 git」过时）。更新本地：`git fetch origin main && git merge --ff-only origin/main`（远程基本走 PR，本地纯快进即可；`app/sessions/` 一般不被远程改，本地会话数据不会冲突）。
- **端口**：**8080**（§5#6 的 8765 过时）。
- **鉴权**：**当前临时关闭** —— `server.py` 的 `_account()` 直接返回 `"local"`，iOA 校验整段被注释。本地可直调所有 `/api/*`，无需 `X-Tai-Identity`（§9「严格 401」过时）。恢复鉴权见该函数上方注释。
- **启动**：`cd app && source ./start_real.sh && python3 server.py --host 0.0.0.0 --port 8080`。⚠️ `start_real.sh` **只 export 环境变量、不启动 server**（含真实密钥、gitignored、Claude 权限分类器会拦，须用户本人跑；`source` 时**不要**带 `--host` 参数，否则 `command not found`）。日志默认打到启动它的**终端**（不落盘）；要文件加 `> /tmp/oh_server.log 2>&1`。

### 13.1 三后端 LLM 调用（重大：judge/optimizer 现在真调 LLM，可选后端）
旧 HANDOFF「harness 里没有真 LLM、judge 读 mock signals」**只适用于离线 demo**。app 运行时的判分/优化现在真调 LLM：
- `app/llm_client.py` `call_llm(prompt, backend, model, reasoning_effort, timeout_seconds, retries, max_tokens)`，三后端：
  - `api` —— Anthropic 或 OpenAI 兼容中转（bianxie）。`_call_api` 读 `ANTHROPIC_API_KEY`/`ANTHROPIC_BASE_URL`/`LLM_API_STYLE`(openai|anthropic)/`ANTHROPIC_JUDGE_MODEL`。
  - `workbuddy` —— WorkBuddy CLI（`_call_workbuddy`，禁用 WB 自动记忆）。**judge/optimizer/generation 的默认后端**。
  - `codex` —— Codex CLI（`_call_codex`，PR#23，含 `reasoning_effort`）。
- `app/model_config.py` = 模型配置中心：`SUPPORTED_WB_MODELS`(~26，含 deepseek-v4-pro-ioa、claude-opus-4.8、gpt-5.6-sol、gemini/glm/kimi/minimax…)、`SUPPORTED_API_MODELS`(claude-opus-5 / claude-opus-4.8 / gpt-5.6-sol)、`SUPPORTED_CODEX_MODELS`(gpt-5.6-sol)。默认 `DEFAULT_GENERATION_WB_MODEL=deepseek-v4-pro-ioa`、`DEFAULT_EVALUATION_WB_MODEL=claude-opus-4.8`。
- **关键环境变量**（在 start_real.sh 里设）：
  - 后端：`OPENHARNESS_JUDGE_LLM_BACKEND` / `OPENHARNESS_OPTIMIZER_LLM_BACKEND`（默认 `workbuddy`）。
  - 模型：`OPENHARNESS_{JUDGE,OPTIMIZER}_{WB,API,CODEX}_MODEL`、`OPENHARNESS_{JUDGE,OPTIMIZER}_CODEX_REASONING_EFFORT`。
  - token/超时/重试：`LLM_MAX_TOKENS`(默认 16000，API/Codex 用)、`LLM_OPTIMIZER_MAX_TOKENS`(12000)、`LLM_GUARD_MAX_TOKENS`(2000)、`LLM_TIMEOUT_SECONDS`(180)、`LLM_RETRIES`(2)、`LLM_REWRITE_RETRIES`(2)。
  - judge：`OPENHARNESS_JUDGE_PARALLEL`(20)、`OPENHARNESS_JUDGE_STRATEGY`(per_dimension)、`OPENHARNESS_JUDGE_MAX_RETRIES`(3)。
- 页面可**按请求**选后端/模型（Judge、Optimizer 各一组下拉；前端从 `GET /api/generation/config` 读默认后端来初始化，即改上面 env 就能改页面默认）。
- **SSL**：`_ssl_context()` 优先 certifi → 系统 CA（`/etc/ssl/cert.pem`）→ `SSL_CERT_FILE`。修 macOS Framework Python 无根证书导致的 `CERTIFICATE_VERIFY_FAILED`。

### 13.2 采纳 gate 重写（`lexicographic_champion/v2`，比旧版严得多）
`app/optimizer_pipeline.py:evaluate_gate`（纯函数）：
- **对比基线：历史 champion**（不是父版）。
- **仅两条采纳路径**：① 硬失败 key **词典序严格下降** ∧ 全维不回退超容差；② 硬失败 **持平** ∧ overall 提升 ≥ `MIN_EFFECTIVE_OVERALL_DELTA=0.05` ∧ 全维不回退 ∧ **独立 holdout 不回退**。
- 硬失败 key = `(redline_failures, hard_floor_failures)` 词典序。`target_check` 仅作佐证、不构成第三条路径（防 judge 噪声/单维波动误采纳）。
- 由 `session_core.settle_pending_candidate()` 在候选**真实判分收尾时**调用（llm_rewrite 异步 gate）；switch_search 仍走 session_eval 内联老 gate。旧 gate 是「父版 + 任意维涨 0.001 + 无新红线」，已被彻底取代。

### 13.3 实时评测看板（PR #17/18/20）
- `app/dashboard/*`（纯前端 SPA：experiment-evaluation-tree.html + loader + theme）+ `app/dashboard_api.py`（纯数据层，只读本地文件）**挂在 `server.py` 同进程**，非独立服务。`GET /dashboard`；15 个 `/api/local/*` 只读端点。数据全部来自 `app/sessions/`、`generation_runs/`，不再调 LLM/API。权威数据契约见 `app/dashboard/README.md`。

### 13.4 数据质量产品（新产品线，与调研洞察并行）
- `harness/data_workflow.py`（CLI facade）→ `_data_prepare.py`（造/合数据集）/ `_data_audit.py`（三阶段：结构化提证 → 审计遗漏/冲突/噪声 → 可选修复，用 **Codex CLI**）。资产 `harness/data_quality_assets/`（schema+prompt）。新 agent skill `skills/data-quality-audit/`。评的是**数据准备质量**（非报告质量）。
- `harness/workbuddy_batch/`（8 模块、~2100 行低层批跑引擎）由 `harness/workbuddy_runner.py` façade 统一暴露；外部只经 façade 调用。`harness/backend.py` 仍是 MockBackend/ResearchMockBackend/RecordedBackend 三个（离线 mock 引擎，供 demo/自测）。

### 13.5 评测流程重构 + iteration_trace
- 报告可**直接从 session case inputs 生成**（782f162）；支持多数据集分组（`datasets`/`active_dataset_id`，版本绑 `dataset_id`）；case 级进度流转。
- `app/iteration_trace.py`：每版在 `sessions/<sid>/iterations/<ver>/` 原子维护五文件（manifest/optimizer_summary/dialogue_contract/gate_decision/resource_usage），详见 §12。

### 13.6 会话与数据资产盘点（截至 2026-08-11）
- **会话很多（15 个）**：
  - research_insight 系：`research-run`(18 版翻开关 demo)、`research-calib`(27 案校准)、`ds-timelen`/`real-eval`(标注)、`president-report-llm`(20 案 llm_rewrite)、`verify-ds-tmp`(空)。
  - custom-skill · llm_rewrite 实验系：`3d8fe03d`(v0=llm_scratch 从零写，5 版)、`b27e80a8`、`a722bcb4`(= fork 自 3d8fe03d 的 v0，无旧数据，本轮用 v3 数据 + gpt-5.6-sol 重跑)、`6377ba38`/`89438d72`、以及一批**模型对比会话** `1-api-gpt56-opus5`/`2-api-opus5-gpt56`/`3-api-opus5-alt`/`4-api-gpt56-gpt56`（名字即 judge/optimizer 用的模型组合）。
  - 注：盘点里 `best=None` 多为 view 未判分完/未结算，不代表坏。
- **数据集**：当前 `OPENHARNESS_WB_DATASET` → `data/v3_20260804_real_project_package/data.json`（20 case，真实项目）；旧 `data/20260727_real_project_package/`；`data/research_assistant/`(3 真实尺子)、`data/report_assistant/`(旧算数字型)；及若干 `data/*_test_data/`。`data/` 整个 gitignored（不进库）。

### 13.7 本轮运维实测教训（重要，省得再踩）
1. **bianxie 计费坑（api 后端）**：403 分两类 —— ① `max_tokens` 超该模型单请求上限；② `预扣费额度失败, 余额不足`（**账户没钱**：本轮余额 $0.24 < 单次判分预扣 $0.27，全 403）。judge 是并发的（默认 20），**并发 × 每请求预扣**会成倍烧额度/撞上限。治标=降并发(`OPENHARNESS_JUDGE_PARALLEL`)+降 `LLM_MAX_TOKENS`；根治=**bianxie 充值** 或 judge/optimizer **改回 workbuddy 后端**（公司 CLI，不烧 bianxie 余额）。
2. **重启坑（反复踩）**：改 `start_real.sh` 或代码后**必须真重启**才生效；多次出现「以为重启了、其实是旧/僵尸进程占着 8080」→ 看到过期数据/旧配置。标准动作：`pkill -f "server.py.*8080"` 确认端口空 → source+启动 → `ps eww -p <pid> | grep LLM_MAX_TOKENS` 等**核对新 env 真进了新进程**（`ps` 偶发读数过期，多确认一次）。
3. **本轮修的三个 bug**（已并入远程 main）：SSL 无 CA 回退（llm_client）、`LLM_MAX_TOKENS` 过小致 llm_rewrite 整段 instructions 被截断、`session_core.restore()` 漏初始化 `active_dataset_id`/`datasets` 致**任何会话重启后 advance 崩**（AttributeError）。

### 13.8 关键文件地图（增补，以此为准）
- LLM 后端/模型：`app/llm_client.py`、`app/model_config.py`。
- judge：`app/judge_batch.py`（并发、per_dimension 策略、重试）。
- optimizer：`app/optimizer02.py`(llm_rewrite)、`harness/optimizer.py`(switch_search)、`app/optimizer_registry.py`、`app/optimizer_pipeline.py`(gate/context)。
- 会话：`app/session.py`(组合) = `session_core`+`session_eval`+`session_label`+`session_generation` 四 mixin。
- 生成：`app/generation_jobs.py` + `harness/workbuddy_runner.py`(+`workbuddy_batch/`)。
- 看板：`app/dashboard/` + `app/dashboard_api.py`（`app/dashboard/README.md` 权威契约）。
- 数据质量：`harness/data_workflow.py`/`_data_audit.py`/`_data_prepare.py` + `harness/data_quality_assets/` + `skills/data-quality-audit/`。
- 轨迹：`app/iteration_trace.py`。

### 13.9 生产 Skill / Harness 边界（2026-08-11）
- 新增 `app/production_skill_policy.py`，把 rubric check 投影成无 ID、无权重、无分数的生产执行规则，并统一拦截 rubric/check ID/Gate/champion/holdout/采纳策略等评测元数据。
- `llm_scratch` V0 不再把完整 rubric 传给起草 LLM；红线完整性检查仍是 Harness 内部独立守卫，不写回 Skill。Patch LLM 不再接收完整 rubric、当前分数或 Gate 策略，只接收选中的生产内容要求。
- `skill_compiler` 版本为 `session-skill/v4-production-boundary`：编译时自动清理旧会话中的评分/Gate/采纳章节，最终冻结包删除 `OPENHARNESS_*` 标记和 directive ID，且对所有 Markdown 再做一次生产边界校验。

---

## 14. 2026-08-12 增量（Gate v4 适度放宽）

> 本节覆盖 §13.2 中的 Gate v3 规则。

- `llm_rewrite` 现行规则为 `net_hard_improvement_champion/v4`，仍永远与历史 champion 比较。
- 不再因候选出现任何新 `(case_id, check_id)` 红线失败键就一票否决；失败键新增/解决清单仍完整写入 Gate trace，但只作诊断。
- 硬改善路径改为 Pareto 判定：硬红线总数和维度硬底线总数都不得增加，且至少一项减少。这避免旧词典序规则采纳“红线减少、硬底线反而增加”的交换。
- 补偿性保护：全维回退仍不得超 rubric 容差；本轮目标 check 回退不得超 `0.05`；如已有 test holdout，硬改善路径也要求 holdout 两类硬失败不增、无维度实质回退、overall 回退不超 `0.05`。
- 硬失败持平时的 overall 路径仍保持严格：dev overall 至少提升 `0.05`，且独立 holdout overall 不回退。
- 对历史会话回放：`20555ce0` 的 v1/v4 会改为可采纳，v2 因硬失败恶化拒绝，v3 因目标 check 回退 `-0.10` 拒绝；`7cbf5f29` 的 v1–v4 仍全部拒绝，因为存在真实维度回退、holdout 恶化或 dev 硬失败恶化。历史已结算 verdict 不追溯改写，v4 仅对之后新结算的候选生效。

---

## 15. 2026-08-13 增量（Gate 增益门槛与 early-stop patience）

- `llm_rewrite` 在硬失败持平时的 dev overall 最低有效提升由 `0.05` 调整为 `0.02`；目标 check 与 holdout 的 `0.05` 回退容差不变。
- Gate hydration 会把旧会话 rubric 中持久化的 `min_overall_improvement=0.05` 统一迁移为 `0.02`，历史已结算 verdict 不追溯重判。
- 新建 LLM loop 的默认 `max_no_improvement` 由 4 调整为 8；页面与 seed 脚本同步。
- 当前实验会话 `3f0fae24` 已把 patience 调为 8 并解除停止态，保留当前 streak=4，可继续探索 V5–V8。

---

## 16. 2026-08-13 增量（V0 起草固定使用 Codex）

- 页面选择 `llm_scratch` 创建 V0 时，V0 正文起草与独立红线守卫两次调用均固定为 Codex CLI `gpt-5.6-sol`、`medium`。
- 该配置只影响 V0 专用调用；Runner、Judge 与后续 Optimizer 的模型选择保持原逻辑。
- Rubric 仍从受控模板加载，不交给模型自由生成或改写。
