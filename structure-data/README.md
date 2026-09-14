# Structure Data：原始资料清洗与质检工具

将原始文件或目录整理为带来源的 `structured_data.json`；可选地对照参考报告做遗漏、冲突和噪声质检，再回查来源补充抽取遗漏。

这是原 Python 命令行工具，不是 WorkBuddy 插件、专家包或 V2 原生子代理。可单独复制整个 `structure-data/` 目录使用，不依赖相邻的 Report Loop、Memory 或 OpenHarness Web 服务。

## 依赖

- Python 3.9+，Python 代码只使用标准库。
- 已安装并完成认证的 Codex CLI；真实运行会调用模型并产生用量。默认模型 `gpt-5.6-sol`、effort `medium`，可用参数覆盖。
- CLI 必须支持原 Runner 使用的 `exec --ephemeral --ignore-user-config --sandbox read-only --output-schema --output-last-message` 等参数；可先检查 `codex exec --help`。本次迁移保留原调用协议，没有新增 CLI Provider 或安装器。
- Office、PDF、图片等材料的实际可读性取决于 Codex 执行环境中的解析能力；本工具不附带文档解析运行时。

## 使用

以下命令在 `structure-data/` 下执行。Windows 可将 `python3` 改为本机的 `python`；含空格、中文的路径用引号包裹。

### 只生成结构化论据

```bash
python3 harness/data_workflow.py run --source "原始资料目录" --case-id example --structured-data-only --output "output"
```

`--source` 可重复传入，支持文件或目录。结果位于 `output/example/structured_data.json`。生成阶段不得读取参考报告，不以报告结论反向补造论据。

### 清洗、质检与补充修复

```bash
python3 harness/data_workflow.py run --source "原始资料目录" --human-report "参考报告.md" --case-id example --repair-structured-data --output "output-audit"
```

`run` 和 `audit` 共用同一工作流，默认执行清洗与质检。单项目质检必须提供 `--human-report`。加 `--repair-structured-data` 后，针对质检发现的抽取遗漏回查原始资料，另存修复版；不是让模型根据参考报告编写新事实，也不是任意改写或删除旧论据。

### 复用论据 / 处理已有数据集

```bash
python3 harness/data_workflow.py run --source "原始资料目录" --structured-data "已有论据.json" --human-report "参考报告.md" --case-id example --output "output-audit"
python3 harness/data_workflow.py run --dataset "data.json" --structured-data-only --output "output-batch"
```

数据集模式接受原 OpenHarness `data.json` 格式，可用重复的 `--case-id` 筛选；本工具不负责生成或合并该数据集。

常用参数：

| 参数 | 用途 |
|---|---|
| `--background` | 研究背景 |
| `--model` / `--effort` | 模型与推理强度 |
| `--codex-cli` | Codex 可执行文件名称或路径 |
| `--parallel` | 项目并发数，默认 1 |
| `--timeout` / `--retries` | 单次模型调用超时秒数与重试次数 |
| `--force-structured-data` / `--force-audit` / `--force-repair` | 显式重跑对应阶段 |

## 产物与写入边界

```text
output/
├── summary.json
└── example/
    ├── structured_data.json
    ├── audit.raw.json / audit.json / audit.md
    ├── structured_data_gaps.json
    ├── structured_data_repair.raw.json
    ├── structured_data.repaired.json
    └── run_manifest.json
```

仅生成已执行阶段的产物。Structured Data 只含 `schema / case_id / items / unresolved`；每条 Evidence 为 `id / type / source_ref / content`。模型输出接受 JSON Schema 约束，Python 再校验 ID、字段和分类覆盖等业务约束，质检分数由 Python 计算。

默认只向 `--output` 写入；已有合法阶段产物可复用，来源更新后应明确重跑受影响阶段或使用新输出目录，不能把缓存视为已自动更新。只有显式添加 `--publish-structured-data` 才会向数据集指定的 Structured Data 目标写入，可能覆盖该目标文件，请谨慎使用。该参数不用于发布到 GitHub。

这里的修复沿用原工具的“回查来源后补充”方式，不包含 V2 子代理的增量纠错、撤回和版本分配机制。

## 文件与来源

```text
harness/data_workflow.py          命令行与 Python API
harness/_data_audit.py            原清洗、质检、修复及 Codex Runner
harness/data_quality_assets/      原清洗 Prompt、三份 JSON Schema
harness/tests/                   原测试及独立运行检查
skills/data-quality-audit/        原质检说明、维度与输出规则
```

来源：`OpenHarness-rubrics-loop-main-20260812` 的 `harness/` 与 `skills/data-quality-audit/`，源 Git 提交 `8269d1830bceb0d2bfa63cefd35921f826033af9`。清洗引擎、模型 Prompt、Schema、质检规则和原有测试原样保留。

独立化改动仅为移除 `data_workflow.py` 的 `prepare` 子命令和 `_data_prepare` 导入，避免加载无关的 `workbuddy_batch` 数据集构造依赖。原规则文档中的 `finalize_results.py` 为历史名称，当前确定性汇总由 `_data_audit.py` 完成。没有加入真实素材、报告、记忆、凭据或实验结果。

## 验证

```bash
python3 harness/data_workflow.py --help
python3 -m unittest discover -s harness/tests -v
```

离线测试使用模拟模型结果或临时假 CLI，不调用真实模型，不能替代本机 Codex 认证、模型可用性与多格式材料解析的端到端验证。
