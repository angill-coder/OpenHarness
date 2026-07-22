# OpenHarness · Skill 标注与迭代平台（Web）

给下属用的页面化工作台。把「需求 → 生成 v0 → 导入数据 → 人工标注 → 迭代出下一版」这个闭环搬到浏览器里。纯 Python 标准库 + 单页原生 JS，**无需安装任何依赖，无需 API key**。

## 启动

```bash
cd app
<<<<<<< HEAD
python3 server.py                 # 默认 http://127.0.0.1:8765
# 可选: --port 8000  --host 0.0.0.0
```

打开浏览器访问 `http://127.0.0.1:8765`。**无需 API key** —— skill 在平台（Claude Code）里跑，平台就是运行时；真实报告经页面粘贴导入（见下）。
=======
python3 server.py                 # 默认 http://127.0.0.1:8080
# 可选: --port 8000  --host 0.0.0.0
```

打开浏览器访问 `http://127.0.0.1:8080`。**无需 API key** —— skill 在平台（Claude Code）里跑，平台就是运行时；真实报告经页面粘贴导入（见下）。
>>>>>>> origin/main

> 内置样例：算数字型读 `data/report_assistant/dataset.jsonl`（没有先跑 `python3 ../data/report_assistant/build_dataset.py`）；调研洞察型读 `data/research_assistant/dataset.sample.jsonl`。「用内置样例」按钮按会话产品自动选。

## 两类产品

平台按需求描述（或 `product_id`）自动识别两类产品，各用各的 rubric 与维度：

| 产品 | 触发 | 维度 |
|------|------|------|
| **算数字型 report-assistant** | 经营月报 / 周报 / 简报等 | 数据准确性 / 完整性 / 洞察 / 简洁性（4 维）|
| **调研洞察 research_insight** | 需求含"调研/洞察/素材/访谈/高管报告"等，或 `product_id=research_insight` | 可回溯性 / 结构 / 逻辑与故事线 / 提炼与洞察 / 覆盖度 / 表达（6 维）|

## 页面输入（左列自上而下）

1. **需求描述 → 生成 V0**：填一段对产品的描述，点「生成 V0」。平台识别产品/受众、产出**第一版 v0 skill（结构+指令+memory）+ 一版 rubric**（directive 全关，作为优化起跑线）。
2. **导入数据**：点「用内置样例数据集」，或粘贴 JSONL/JSON 数组。每条 case 需含 `case_id` / `input` / 答案键（算数字型 `ground_truth_findings`；调研洞察 `ground_truth`）。
3. **人工标注**：中列标注表，给当前版本每个 case 分维度打 1–5 分（灰色是 judge 分，填入即覆盖）。点「提交标注」重算 judge↔人工校准一致率。
4. **导入报告文本 + LLM-judge 评分**（调研洞察真实数据用）：见下节。

## 真实报告与 LLM-judge 评分（RecordedJudge · 调研洞察真实数据）

调研洞察产品的 skill **在平台（Claude Code）里跑真实素材、产出真实报告**，harness 不再自己调 API。真实报告的六维分也由**平台上的 LLM-as-judge** 按 rubric 打，再粘回页面。离线自测（mock）与真实数据两条路是叠加的：**没粘真实产物的 case 用 mock 占位分，粘了的 case 用真实分**（标注表 judge 列有 `真/mock` 徽标区分）。

操作顺序（页面第 4 步「导入报告文本」卡）：

1. **在平台跑 skill**：拿当前版本 skill 的 instructions，在 Claude Code 里对某个 case 的素材跑一遍 → 得到报告 markdown。
2. **粘报告**：第 4 步卡里选 case → 粘报告正文 → 点「导入该 case 的报告」。报告存下来（供人工按 rubric 逐维对照阅读），标注表该行出现 `📄已导入`。
3. **在平台用 LLM-judge 打分**：在 Claude Code 里让一个独立的 judge（按 `harness/artifacts/rubric_research.json` 的六维锚点）给这份报告打 1–5 分。
4. **粘评分**：把六维分填进第 4 步卡的评分框 → 点「导入六维评分」。该 case 即由 mock 转**真实分**，参与分数曲线、校准（= 你的人工分 vs 平台 judge 分）、以及 traceability 红线一票否决。
5. **人工标注照旧**（第 3 步）：填你的专家分 → 校准一致率据此更新。

> **边界**：候选新版本（advance 出的 vN+1）**在真实数据上无法自动 gate**——因为你还没在平台跑过 vN+1、没有它的报告和评分。真实数据下的逐版推进本质是人在环的（跑 → 粘报告 → 粘评分 → 再推进）。离线 mock 路（`harness/run_demo_research.py`）不受此限，用于验证闭环逻辑本身。

## 造校准集（真实数据工作流）

judge 校准需要**既有"好报告打高"、也有"坏报告在不同维度打低"**的样本，覆盖高/中/低质量。一件真实资料 → 一条完整 case 的步骤：

1. **定尺子（ground_truth）**：把素材切片配 `S-xxx`，写 `supported_claims / key_claim_ids / expected_insights / unsupportable_questions / noise_source_ids / traps`，写进 `data/research_assistant/dataset.jsonl`。**这是不能外包的核心**（什么算 noise、哪条是单一信源、什么该留白、什么算越界外推——都在这里定死，judge 靠它扣分）。
2. **好报告**：真实产出的报告 → 第4步「导入报告文本」贴正文 → 平台 LLM-judge 按 `harness/artifacts/rubric_research.json` 锚点打分 → 「导入六维评分」。
3. **坏样本（半自动）**：
   ```bash
   cd data/research_assistant
   python3 make_bad_variants.py --case <case_id>                    # 生成坏报告骨架
   python3 make_bad_variants.py --case <case_id> --into-session <sid>  # 顺带一键装入会话
   ```
   它读该 case 的 `ground_truth`，**只在有把柄时**派生对应坏变体（硬答留白问题/越界外推/孤证当定论/混用冲突/引噪音/罗列/摘要铺陈/"不是,而是"注水），每个只犯一种错、隔离到一个维度，产出带 `【填写:…】` 的报告骨架 + 建议六维分 + reasoning。你补正文（坏点保留、其余写好）、核对分即可。坏变体**不写进 dataset.jsonl**（尺子保持干净）。
4. **填人工分**：app 第3步给每条（好+坏）打**你的专家六维分** → 校准一致率 = 你的分 vs judge 分。某维度差 >1，说明该维锚点要再对齐（meta-eval 要抓的正是这个）。
5. **攒量**：凑够 30–50 条覆盖高/中/低，校准 overall ≥ 0.85，才谈得上开 optimizer。

> 参考实例：`data/DeepSeek用户时长分析/`（真实素材 + vF 报告）已按此建成 `dataset.jsonl` 的 `rr-ds-timelen` + 会话 `ds-timelen`（1 好 + 2 坏）。

## 核心交互节奏（标注后手动推进）

- 每导入/推进一次，中列展示**当前版本的 skill**（打开了哪些 directive、来自什么优化提议、结构冻结部分），右列刷新**看板**（校准一致率、分数曲线、失败模式、当前 rubric）。
- 你标注满意后，点中列的 **「▶ 生成下一版 skill」**：optimizer 读当前失败模式 → 提一个最便宜的改动（L1 打开某 directive）→ dev gate 验证（目标维度↑ 且 其它不塌 且 不引红线）→ 通过则成为新版本，否则记为**被拒版本**（版本条上带删除线）。
- **每一版 skill 和 rubric 都呈现在页面上**：版本条可点，看板分数曲线逐版累积。
- 优化器无更多可提议 → 提示**收敛/平台期**，并诊断是否需要回去改结构。

## Rubric 可编辑（成败杠杆）

左列「编辑 Rubric」可改维度权重和 overall 目标，保存为**新的 rubric 版本**（r1/r2…）并立即重评。改权重会看到 overall 与达标情况随之变化——这就是"rubric 定义什么是好"的直接体现。

## 与 harness 的关系

本 app 是 `harness/` 的**人在环编排 + 页面外壳**，不重复实现算法：

| app 文件 | 职责 |
|---------|------|
| `generator.py` | 需求描述 → v0 skill + rubric（离线启发式 / Claude 路径）|
| `session.py` | 会话状态；把 harness 的自动 `run_loop` 拆成页面可逐版驱动的步骤 |
| `server.py` | stdlib `http.server`，JSON API + 托管页面 |
| `index.html` | 单页 UI（三输入 + 版本演进 + 标注 + 看板）|

算法本体（runner/judge/calibration/clustering/optimizer）全部复用 `harness/`。后端选择、"结构定上限"、防 reward hacking、校准门槛等语义与 harness 一致。

## API（供调试）

| 方法/路径 | 作用 |
|-----------|------|
| `POST /api/session` `{requirement, product_id?}` | 建会话，生成 v0 |
| `GET /api/session?id=` | 当前会话完整状态 |
| `POST /api/data` `{id, rows? / use_sample?, labels?}` | 导入数据 |
| `POST /api/labels` `{id, version, labels:{case_id:{dim:score}}}` | 提交人工标注 |
| `POST /api/rubric` `{id, weights?, target?}` | 编辑 rubric（存新版本）|
| `POST /api/advance` `{id}` | 生成下一版 skill |
| `POST /api/import_output` `{id, case_id, report_text, version?}` | 存平台跑出的真实报告文本 |
| `POST /api/import_judgment` `{id, case_id, scores:{dim:score}, reasoning?, version?}` | 存平台 LLM-judge 六维分（覆盖 mock）|
| `GET /api/sample_data` | 返回内置样例数据集（按会话产品）|
| `GET /api/sessions` | 列出所有已恢复会话 |

## 说明与边界（v0 演示范围）

- **落盘持久化**：需求描述、每一版 skill/rubric、每次人工标注**都落盘**，重启自动恢复（见下）。
- **离线确定性**：mock 后端的输出质量由 skill 的 directive 决定，judge 按 rubric 锚点对照 ground truth 打分，所以"打开正确 directive → 分数上升"是 rubric 的必然而非脚本。算数字型走 `MockBackend`，调研洞察型走 `ResearchMockBackend`（吐报告文本+signals）。
- **无需 API key**：skill 在平台上跑，真实报告/评分经粘贴导入（RecordedJudge），后端选择不依赖 key。（`generator.py` 保留一条 dormant 的"Claude 生成 v0"分支，仅在有 key + `--real` 时启用，与执行/评分无关。）
- **v0 生成用固定词汇**：需求描述影响产品/受众/权重/初始 directive，但结构与维度沿用 harness 已知词汇（保证生成物能被 loop 真正跑起来）。
- **优化目前只做 L1**（打开 directive）；L2（few-shot）/L3（memory）与结构级优化是后续。

## 落盘与恢复

每个会话落在 `app/sessions/<sid>/`：

| 文件 | 内容 |
|------|------|
| `meta.json` | 不可变元信息：sid / **原始需求描述** / product_id / 创建时间 |
| `events.jsonl` | **追加式完整历史**，一行一事件（带时间戳）：`created` / `import_data` / `submit_labels` / `edit_rubric` / `version_adopted` / `version_rejected` / `converged` / `import_output` / `import_judgment`。可回溯"谁在什么时候改了什么"。|
| `state.json` | 最新完整快照（durable 输入：需求/rubric/所有版本 skill/current/opt_history/数据/所有标注）。派生量（分数/校准/失败聚类）恢复后重算。|
| `outputs.jsonl` | 平台跑出的**真实报告文本**（按 版本×case 追加）。恢复时重建 `report_outputs`。|
| `judgments.jsonl` | 平台 LLM-judge 对真实报告的**六维评分**（按 版本×case 追加）。恢复时重建 `report_judgments`。|

- **每次变更即写盘**（create/import/label/rubric/advance 之后立即），崩溃/重启不丢。
- **重启自动恢复**：`server.py` 启动时扫描 `sessions/` 全部载入，页面可继续之前的迭代（版本曲线、标注、rubric 全部还原，并能接着点"生成下一版"）。
- 右列「操作历史（已落盘）」面板实时展示 events 时间线。
- `GET /api/sessions` 列出所有已恢复会话。
- 想清空：删掉 `app/sessions/` 目录即可。
