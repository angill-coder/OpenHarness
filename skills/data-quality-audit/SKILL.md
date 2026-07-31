---
name: data-quality-audit
description: 对研究项目的 source、Evidence Metadata 与 groundtruth 做三类数据质检：遗漏、冲突和噪声。适用于单项目或批量 OpenHarness data.json。
---

# 数据质检

正式执行入口是 `harness/data_workflow.py`。这是数据准备、清洗和质检的唯一
公共入口；本 Skill 使用其中的 `run` workflow。工具会：

1. 只读调用 Codex CLI，把原始 source 生成 `openharness-evidence/v1` Metadata；
2. 只读调用 Codex CLI，对 Metadata、source 与 groundtruth 做语义质检；
3. 可选地根据质检发现的 `metadata_gaps` 再次回查 source，只补充原本已
   存在于 source 的事实，生成修复版 Metadata；
4. 由 Python 确定性校验分类覆盖率、计算分数并生成 Markdown。

开始前完整读取：

- `references/dimensions.md`
- `references/result-schema.md`

## OpenHarness 数据集

```bash
python harness/data_workflow.py run \
  --dataset <data.json> \
  --case-id <case-id> \
  --repair-metadata \
  --output <output-dir>
```

省略 `--case-id` 时处理全部 case。已有合法 Metadata 默认复用；使用
`--force-metadata` 重建，使用 `--force-audit` 重跑质检。

## Standalone

```bash
python harness/data_workflow.py run \
  --source <source-file-or-dir> \
  --groundtruth <groundtruth-file> \
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
    ├── evidence_metadata.json
    ├── metadata_gaps.json
    ├── metadata_repair.raw.json
    ├── evidence_metadata.repaired.json
    ├── audit.raw.json
    ├── audit.json
    ├── audit.md
    └── run_manifest.json
```

默认不修改输入目录。只有显式增加 `--publish-metadata` 时，才会将合法
Metadata 同步至 OpenHarness case 目录。

## 判定边界

- Metadata 阶段不得读取 groundtruth。
- Audit 发现遗漏后必须定向回查原始 source。
- source 已有事实但 Metadata 未充分提取时列入 `metadata_gaps`，不得误判为
  数据遗漏。
- 修复阶段不得读取 groundtruth，只能根据 `metadata_gaps` 重新回查 source；
  默认不覆盖原 Metadata。
- 同一对象、时间、样本和口径不一致才可能构成冲突。
- 1% 相对误差或 0.5 个百分点以内的差异不得判冲突。
- 证据不足以推出 GT 结论属于遗漏，不属于冲突。
- Metadata 每个 EV 必须恰好分类一次。
- 模型不计算分数；固定权重为遗漏40%、冲突40%、信噪20%。
- 不输出评级和置信度。
