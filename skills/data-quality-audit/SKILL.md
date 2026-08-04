---
name: data-quality-audit
description: 对研究项目的 source、Structured Data 与 human_report 做三类数据质检：遗漏、冲突和噪声。适用于单项目或批量 OpenHarness data.json。
---

# 数据质检

正式执行入口是 `harness/data_workflow.py`。这是数据准备、清洗和质检的唯一
公共入口；本 Skill 使用其中的 `run` workflow。工具会：

1. 只读调用 Codex CLI，把原始 source 生成 `openharness-structured-data/v1` Structured Data；
2. 只读调用 Codex CLI，对 Structured Data、source 与 human_report 做语义质检；
3. 可选地根据质检发现的 `structured_data_gaps` 再次回查 source，只补充原本已
   存在于 source 的事实，生成修复版 Structured Data；
4. 由 Python 确定性校验分类覆盖率、计算分数并生成 Markdown。

开始前完整读取：

- `references/dimensions.md`
- `references/result-schema.md`

## OpenHarness 数据集

```bash
python harness/data_workflow.py run \
  --dataset <data.json> \
  --case-id <case-id> \
  --repair-structured-data \
  --output <output-dir>
```

省略 `--case-id` 时处理全部 case。已有合法 Structured Data 默认复用；使用
`--force-structured-data` 重建，使用 `--force-audit` 重跑质检。

## Standalone

```bash
python harness/data_workflow.py run \
  --source <source-file-or-dir> \
  --human-report <human_report-file> \
  --case-id <case-id> \
  --background <研究背景> \
  --output <output-dir>
```

多个 source 可重复传入 `--source`。

## 产物

```text
<output-dir>/
├── summary.json
└── <case-id>/
    ├── structured_data.json
    ├── structured_data_gaps.json
    ├── structured_data_repair.raw.json
    ├── structured_data.repaired.json
    ├── audit.raw.json
    ├── audit.json
    ├── audit.md
    └── run_manifest.json
```

默认不修改输入目录。只有显式增加 `--publish-structured-data` 时，才会将合法
Structured Data 同步至 OpenHarness case 目录。

## 判定边界

- Structured Data 阶段不得读取 human_report。
- Audit 发现遗漏后必须定向回查原始 source。
- source 已有事实但 Structured Data 未充分提取时列入 `structured_data_gaps`，不得误判为
  数据遗漏。
- 修复阶段不得读取 human_report，只能根据 `structured_data_gaps` 重新回查 source；
  默认不覆盖原 Structured Data。
- 同一对象、时间、样本和口径不一致才可能构成冲突。
- 1% 相对误差或 0.5 个百分点以内的差异不得判冲突。
- 证据不足以推出 Human Report 结论属于遗漏，不属于冲突。
- Structured Data 每个 EV 必须恰好分类一次。
- 模型不计算分数；固定权重为遗漏40%、冲突40%、信噪20%。
- 不输出评级和置信度。
