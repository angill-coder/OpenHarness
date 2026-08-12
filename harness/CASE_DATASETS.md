# 数据准备 Workflow：原子 Case 生成与数据集合并

统一入口 `data_workflow.py prepare` 用来把原始素材转换为可独立运行的
WorkBuddy case，并在需要时将不同来源的 case 组合成批量 dataset。具体实现
位于私有引擎中，调用方无需再协调多个脚本。

## 1. 为素材生成原子 case

建议让 `.case.json` 与素材相邻。这样每个 case 可以直接传给
`harness/run_external.py --dataset`，不需要额外配置素材根目录。

```bash
cd OpenHarness

python3 harness/data_workflow.py prepare generate \
  --materials-dir ../all_data/104合成数据汇总 \
  --source-collection synthetic-104 \
  --id-prefix case-synth104 \
  --openharness-id-prefix rr-synth104
```

输入：

```text
104合成数据汇总/
├── 1_AI coding工具对中国互联网公司的生产力影响.md
└── 2_国内外AI chatbot应用研究.md
```

输出：

```text
104合成数据汇总/
├── 1_AI coding工具对中国互联网公司的生产力影响.md
├── 1_AI coding工具对中国互联网公司的生产力影响.case.json
├── 2_国内外AI chatbot应用研究.md
└── 2_国内外AI chatbot应用研究.case.json
```

默认识别 `{编号}_{topic}`。不符合该格式的文件也可以生成：topic 使用完整
文件 stem，ID 使用稳定的 ASCII slug 或文件名哈希。其他命名规范可以通过
`--filename-regex` 指定；正则必须提供 `(?P<topic>...)`，还可以提供
`(?P<index>...)` 和 `(?P<id>...)`。

默认只处理当前目录的 `*.md`。处理其他格式或子目录：

```bash
python3 harness/data_workflow.py prepare generate \
  --materials-dir ../all_data/another_collection \
  --material-glob '*.md' \
  --material-glob '*.pdf' \
  --recursive
```

生成器会忽略已有的 `*.case.json`，避免宽泛 glob 把输出再次当作素材。

如果每条数据本身是一个包含多份材料的目录，使用“一目录一 case”模式：

```bash
python3 harness/data_workflow.py prepare generate \
  --materials-dir ../all_data \
  --source-kind directory \
  --material-glob '*_原始资料'
```

目录会整体复制到 case workspace 的 `materials/`。默认的 file 模式则把单个
文件复制为 `materials/<原文件名>`。

### ID 规则

未显式指定前缀时，工具会根据 `source_collection` 生成 collection token，
避免不同数据源都产生 `case-001`。正式数据建议显式指定稳定、易读的前缀：

```bash
--source-collection synthetic-104
--id-prefix case-synth104
--openharness-id-prefix rr-synth104
```

### Intake 模式

- `neutral`：默认。缺失内容使用中性背景和“暂无预设假设”。
- `placeholder`：缺失内容写成 `TODO`，便于后续人工补齐。
- `strict`：背景或 hypo 缺失时拒绝生成，适合冻结正式评测数据。

通过 JSON overrides 补充逐 case 内容：

```json
{
  "1": {
    "research_background": "具体研究背景。",
    "hypo": "具体研究假设。",
    "material_focus": "市场规模和竞争格局是重点。",
    "report_pages": 3,
    "report_max_chars": 3000,
    "topic": "可选的展示 topic 修正",
    "split": "dev",
    "metadata": {
      "industry": "互联网"
    }
  }
}
```

override key 可以使用 source index、素材相对路径、素材文件名、素材 stem 或
生成后的 case ID。
`report_pages` 未提供时默认 3 页；页数按每页不超过约 1000 个中文可见字符
折算。通常只需设置 `report_pages`，只有用户明确给出字数时才覆盖
`report_max_chars`。
同一素材命中多个内容不同的 override 时会报错，避免静默选错。

```bash
python3 harness/data_workflow.py prepare generate \
  --materials-dir ../all_data/104合成数据汇总 \
  --intake-overrides ../all_data/104合成数据汇总/intake_answers.json \
  --intake-mode strict \
  --force
```

先检查而不写文件：

```bash
python3 harness/data_workflow.py prepare generate \
  --materials-dir ../all_data/104合成数据汇总 \
  --dry-run
```

默认不会覆盖已有输出；确认需要重建时使用 `--force`。

### 使用 human_report 和 Codex 一步生成完整 Round 1

如果素材目录和 human_report 目录具有相同的相对子目录及文件 stem，可以在一次
`generate` 中自动完成 PDF 配对、Codex intake 推断和原子 case 生成：

```bash
python3 harness/data_workflow.py prepare generate \
  --materials-dir '../all_data/120条真实数据汇总/Markdown_data' \
  --human-report-dir '../all_data/120条真实数据汇总/raw_ppt' \
  --recursive \
  --source-collection real-120 \
  --id-prefix case-real120 \
  --openharness-id-prefix rr-real120 \
  --filename-regex '^(?P<index>\d{6,8})[_ ]?(?P<topic>.+)$' \
  --codex-parallel 2 \
  --force
```

例如：

```text
Markdown_data/2022/20220426_金融云分析_v60.md
raw_ppt/2022/20220426_金融云分析_v60.pdf
```

会按相对路径 `2022/20220426_金融云分析_v60` 精确配对。human_report 中没有
对应 Markdown 的额外 PDF 不会被处理；任何 Markdown 缺少配对 human_report
都会在调用 Codex 前报错。

流程默认：

1. 用 `pdftotext -layout` 提取 PDF，并添加 `PAGE N` 页码标记；
2. 在独立临时目录中以 read-only sandbox 运行 `codex exec`；
3. 使用 JSON Schema 约束研究背景、hypo、证据页、置信度和泄漏风险；
4. 将成功结果写入 `human_report-dir` 父目录的
   `intake_answers.codex.json`；
5. 所有 case 成功后，以 strict intake 生成最终 `.case.json`。

缓存记录 human_report SHA-256、prompt 版本、Codex CLI 版本和模型。再次执行时
会跳过未变化的成功结果；使用 `--refresh-intake` 强制重新推断。Codex 失败时
已成功结果仍会保存，重新执行即可断点续跑。默认不会以 neutral 内容覆盖失败
case。

常用控制参数：

- `--codex-cli`：指定 Codex CLI。
- `--codex-model`：固定推断模型；默认使用 CLI 配置。
- `--codex-parallel`：并发数，默认 2。
- `--codex-timeout`：单次 Codex 超时，默认 900 秒。
- `--codex-retries`：失败后的额外重试次数，默认 1。
- `--intake-cache`：自定义推断缓存位置。
- `--minimum-extracted-characters`：扫描件/空 PDF 检测阈值。
- `--pdftotext-cli`：指定 Poppler `pdftotext`。

Codex 生成的 round 1 在 metadata 中标记为 `codex_inferred`，并记录
`hypo_type`、`intake_confidence` 和 `intake_leakage_risk`。人工 overrides
优先级高于 Codex 缓存，可用 `--intake-overrides` 覆盖个别结果。

### 项目目录模式

对于“每个 case 一个项目目录，目录内包含一个素材子目录和一个 human_report”
的真实项目数据，使用 `generate-projects`：

```text
real_project/
└── 20251230_SurgeAI人效调研/
    ├── source/
    │   └── 原始资料.docx
    └── 20251230_Surge AI高人效和AI赋能调研_vF.pdf
```

```bash
python3 harness/data_workflow.py prepare generate-projects \
  --projects-dir ../all_data/real_project \
  --output-dir data/20260724_test_data/10_real_project \
  --id-prefix case-realproject \
  --openharness-id-prefix rr-realproject \
  --codex-parallel 3 \
  --force
```

每个项目必须有且仅有一个非隐藏素材目录，以及一个 PDF 或 PPTX human_report。
素材目录会统一复制成 `<项目>/source/`；human_report 只用于生成 intake，不会
进入报告模型的 `input_files`。复制时会过滤 `.DS_Store`、隐藏工具目录和
Office `~$` 临时文件。PDF 通过 Poppler 提取，PPTX 通过 slide XML 提取，并
统一保留页/幻灯片编号。

默认 ID 使用 `case-realproject-{项目编号}`，便于与
`case-real120-*` 等其他来源区分。

## 2. 合并为批量 dataset

合并整个 collection：

```bash
python3 harness/data_workflow.py prepare merge \
  --input ../all_data/104合成数据汇总 \
  --output ../datasets/synthetic-104.json
```

按 `metadata.source_index` 选择：

```bash
python3 harness/data_workflow.py prepare merge \
  --input ../all_data/104合成数据汇总 \
  --include 1,2,5,20-30 \
  --output ../datasets/selected.json
```

按字段筛选：

```bash
python3 harness/data_workflow.py prepare merge \
  --input ../all_data/104合成数据汇总 \
  --filter metadata.split=dev \
  --filter metadata.intake_status=reviewed \
  --output ../datasets/reviewed-dev.json
```

混合多个来源：

```bash
python3 harness/data_workflow.py prepare merge \
  --input ../all_data/104合成数据汇总 \
  --input ../all_data/人工案例 \
  --output ../datasets/mixed.json
```

也可以用 manifest 精确选择。相对路径以 manifest 所在目录为基准，空行和
以 `#` 开头的注释会被忽略：

```text
# dev cases
../all_data/104合成数据汇总/1_xxx.case.json
../all_data/人工案例/manual-03.case.json
```

```bash
python3 harness/data_workflow.py prepare merge \
  --manifest ../datasets/dev-cases.txt \
  --output ../datasets/dev.json
```

合并器会：

1. 展开每个输入 dataset 的 `defaults`，保持单 case 的实际配置；
2. 以原 JSON 为基准解析素材路径；
3. 以新 dataset 为基准重新生成相对路径；
4. 拒绝重复的 case ID 和 OpenHarness case ID；
5. 使用现有 `workbuddy_batch.dataset.load_cases()` 验证最终文件；
6. 检查所有输入素材仍然存在。

因此不同目录的原子 case 可以安全混合，不会因为移动 dataset 而丢失素材路径。

## 3. 模板定制

生成器支持：

- `--task-template`
- `--background-template`
- `--hypo-default`
- `--material-focus-default`
- `--skill`
- `--split`

任务和背景模板可以引用 `{topic}`。任务模板还可以引用 `{case_id}`、
`{source_file}` 和 `{source_collection}`。引用未知变量会直接报错。

查看完整参数：

```bash
python3 harness/data_workflow.py prepare generate --help
python3 harness/data_workflow.py prepare merge --help
```
