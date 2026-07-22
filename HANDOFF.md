# OpenHarness 交接文档（HANDOFF）

> 给「完全没有上下文」的下一个 agent。**先完整读这份，再动手。** 最后更新：2026-07-13。
> 用户要求：**一律用简体中文交流**（曾误用日语被纠正）。

---

## 0. 一句话现状

平台的「调研洞察汇报助手」六维评测闭环已**代码就绪并验证跑通**；已用 3 篇真实报告 + 素材建成数据集与校准集；**已把"下属实际用的报告生成 skill"部署成 Claude Code Agent Skill（`research-report`，见 §4.5）**——含开场 3 轮交互 + 3 段报告结构。用户此刻在 app 里跑优化器验证闭环（会话 `research-run`）。**剩下的主要是用户的人工活**：填专家六维分做校准、把坏样本骨架补成正文、用真实任务数据测 skill。

---

## 1. 项目是什么

**OpenHarness** = 一个 **eval 驱动的 skill 自动优化平台**（路径 `/Users/angill/Documents/New project/OpenHarness`，非 git 仓库）。
闭环：Runner → Judge（LLM-as-judge，需人工标注做 meta-eval 校准）→ 失败聚类 → Optimizer（反思式改写，只动 instructions/directives，不动结构）→ 版本化 Store → 回归看板。

**铁律共识（用户认同，别违反）**：
- **结构定质量上限**：flow/subagent/memory schema 由人 v0 设对；Optimizer(MVP) 只翻 directive，不动结构。
- **rubric 和数据是杠杆，不能外包给弱工程师**；ground_truth 与人工分是用户不可外包的核心。
- **judge 校准一致率 ≥0.85 才允许开 Optimizer**（离线 `run_loop` 强制；app 的 advance 不强制）。

**两个产品并存**（按 `rubric["product"]` / `product_id` 派发，互不干扰）：
- `report-assistant`（**算数字型**经营月报，旧，`data/report_assistant/`）——4 维：data_accuracy/completeness/insight/conciseness。
- `research_insight`（**调研洞察汇报助手**，当前重点）——6 维（见 §3）。

环境事实：本机**无 ANTHROPIC_API_KEY、无 anthropic SDK、无 claude CLI**；Python 3.9.6（**只用 stdlib**，无 openpyxl/pandas）、Node 22。机器可读文件用 JSON 非 YAML。读 pdf 用 `pypdf`（可用），读 docx 用 stdlib `zipfile`+`xml`（无 python-docx），xlsx 读不了。

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
- `session.py` 会话编排：产品无关维度（`self.dims` 从 rubric 取）；`import_data`（research 认 `ground_truth`）；`import_output`（贴真实报告文本）；`import_judgment`（贴平台 LLM-judge 六维分，**RecordedJudge**）；`_apply_recorded` 在 evaluate 后把真实报告/评分**覆盖** mock 分（有 judgment 的 case `score_source=recorded`，无则 `mock` 占位）。
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

**ground_truth 四条边界原则（用户拍板，建新 case 沿用）**：
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
每条含 sources[](切片配 S-id) + ground_truth(supported_claims/key_claim_ids/expected_insights/unsupportable_questions/noise_source_ids/traps)。原始素材+抽出的正文在 `data/<案名>/`（`vF报告_正文.md`、`原始素材_正文.txt`）。

**`make_bad_variants.py`**：读某 case 的 ground_truth 半自动派生坏报告骨架+建议六维分+reasoning，`--into-session <sid>` 合并进会话。已支持缺陷：hardanswer/overclaim/single_source/conflict/metric_caveat/selection_bias/outlier/noise/listing/summary/style。生成物 `bad_variants.<case>.jsonl`（骨架含【填写】）。

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
