# 调研洞察汇报助手 · 数据集 + 校准集

对应 `调研洞察汇报助手_v0设计文档.md` Part B。这是 rubric 的另一半——rubric 定义"什么是好"，数据集决定"在什么样本上比好坏"。二者都是专家资产，缺一不可。

> ⚠️ **此产品与 `data/report_assistant/`（经营月报、算数字型）是两类任务**，schema 不同。别混用。

## 文件

| 文件 | 内容 | 谁来动 |
|------|------|--------|
| `dataset.template.jsonl` | **空白模板**，每个字段带 `←` 中文填写提示。复制成 case 照着填。 | 你 |
| `dataset.sample.jsonl` | **一条填好的真实样例**（DeepSeek 使用时长分析），含 2 个 trap，供参照。 | 参考 |
| `human_labels.sample.jsonl` | 校准集格式样例（六维度人工打分）。 | 参考 |
| `dataset.jsonl` | **正式数据集，已建 3 条真实 case**：`rr-ds-timelen`(DeepSeek时长) / `rr-surge-eff`(Surge高人效) / `rr-retention`(元宝/DS/豆包留存)。 | 你(复核/续加) |
| `bad_variants.<case>.jsonl` | 各 case 的**坏报告变体骨架**（`make_bad_variants.py` 生成，含【填写】占位）。 | 你(补正文) |
| `make_bad_variants.py` | 从某 case 的 human_report 半自动派生坏报告骨架+建议六维分，`--into-session` 可并入会话。 | 工具 |
| `human_labels.jsonl` | ← **你产出的校准集**（还没建；也可直接在 app 第3步逐条填专家分）。 | 你 |

三条真实 case 的原始素材 + 抽出的正文分别在 `data/DeepSeek用户时长分析/`、`data/Surge AI 高人效原因分析/`、`data/AI产品用户留存洞察/`（各含 `vF报告_正文.md` 与 `原始素材_正文.txt`）。

## 怎么开始（Phase0 建议路径）

1. **打开** `dataset.template.jsonl` 看字段含义，对照 `dataset.sample.jsonl` 看填好的样子。
2. **手工做 3–5 条真实 case**：DeepSeek 用 2 条、AI Native Agent 落地用 2–3 条，**各埋 ≥1 个 trap**。
   - `input.sources[].excerpt` 填你手头素材里**最关键的原话/数据**（eval 跑的是这段文本，不是原始 pdf/excel）。
   - `human_report`（supported_claims / key_claim_ids / expected_insights / traps）**是你不能外包的核心活**——先读完素材，把"素材真支持的结论"和"好报告应达到的洞察"标出来。
3. 把填好的 case 汇成 `dataset.jsonl`（一行一条），标好 `split`（train/dev/test，test 保留不参与优化）。
4. 每条 case 跑出一版输出后，按 rubric 给它分维度打分，汇成 `human_labels.jsonl`（抽 30–50 条；v0 可先小样）。

## 6 种调研专属 trap（硬 case，必须覆盖）

| tag | 含义 | 测什么 |
|-----|------|--------|
| `source_conflict` | 两份素材数据/口径/结论冲突 | 会不会指出冲突，而非悄悄混用 |
| `unsupported_extrapolation` | 素材不足以支撑某种外推 | 会不会过度外推、乱下趋势结论 |
| `single_source_overweight` | 结论只有单一信源（如仅1人访谈） | 会不会把孤证当普遍规律 |
| `missing_evidence` | 关键问题素材里根本没答案 | 会不会诚实说"素材不足"而非编造 |
| `stale_or_scope_mismatch` | 素材时间/范围与调研问题不匹配 | 会不会误用不相关素材 |
| `buzzword_bait` | 素材里有大词但无实质 | 会不会被术语带偏、复述空话 |

**已扩展的 trap 类型**（在三条真实 case 里用到，`make_bad_variants.py` 也已支持）：`metric_caveat`（选有利口径/分母不注明，如 Surge 人效用"全职"口径→可回溯性 3）、`selection_bias`（无视样本偏差，如留存腾讯问卷偏好→3）、`outlier_confound`（把一次性异常当趋势，如 DeepSeek 2 月春节 spike→红线 2）。

## Rubric 六维度（打分用，v0 已细化）

可回溯性与支撑充分 (traceability) 0.28 · 结构 (structure) 0.15 · 逻辑与故事线 (narrative) 0.12 · 提炼与洞察 (insight) 0.22 · 覆盖度 (coverage) 0.08 · 表达与受众契合 (expression) 0.15。**完整锚点见 `调研洞察汇报助手_Rubric落地文档.md`**（六维版，取代设计文档里的旧四维）。

human_labels 打分维度也用这六个字段名：`{"traceability":_,"structure":_,"narrative":_,"insight":_,"coverage":_,"expression":_}`。

## human_report 两个新增字段（v0 确认）

| 字段 | 作用 | 关联维度 |
|------|------|----------|
| `unsupportable_questions` | key_questions 里素材**根本答不了**的问题。好报告诚实留白；硬答扣 traceability，不答不扣 coverage | traceability / coverage |
| `noise_source_ids` | sources 里的**噪音/无关**片段。被大量引用充数 = 剔噪失败 | insight |

**信源规则**：一个结论**至少 2 个独立信源**才可作确定结论；单一信源必须降级为"待验证"，否则 traceability 扣分。埋 `single_source_overweight` trap 测这条。

## 现状（2026-07-13）与校准集

**harness/app 已支持 research_insight 六维路径**（判分 `judge.score_report_research`、`ResearchMockBackend`、`rubric_research.json`、app 的 research 分支与「导入报告文本/六维评分」入口均已就绪；离线自测 `harness/run_demo_research.py` 跑绿）。所以这批数据现在**能在 app 里真正跑闭环**，不再是"光有数据跑不通"。

**已搭好的校准集**：app 会话 **`research-calib`** 共 27 条 = **3 篇真实好报告**（Surge 4.57 / DeepSeek 4.63 / 留存 4.85，作上端锚点）+ **24 条坏变体骨架**（每案 8 条，overall 3.22~3.78，10 条踩可回溯性红线），六维梯度齐全。另有 `ds-timelen` 会话保留 DeepSeek + 2 条手工填好的坏变体 demo。

**把校准集变"活"的步骤**（详见 `app/README.md`「造校准集」节）：
1. `python3 app/server.py` → 打开 `research-calib`。
2. 把坏变体的 `【填写:…】` 补成具体坏报告正文（坏点保留、其余可写好）。
3. 第 3 步给全部 27 条填**你的专家六维分** → 校准一致率 = 你的分 vs judge 目标/评分。
4. 差 >1 的维度 = 锚点该对齐处，回改 `调研洞察汇报助手_Rubric落地文档.md` / `rubric_research.json` 或 case 的 traps 定义。
5. 一致率 overall ≥ 0.85 → 才谈得上开 optimizer。

⚠️ 仍待你产出：坏变体正文（骨架→具体）、每条的专家分（校准的另一半）、以及 xlsx/docx 里的精确数字核对。**human_report 与专家分是不能外包的核心**。
