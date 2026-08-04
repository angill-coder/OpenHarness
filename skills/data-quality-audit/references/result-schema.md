# 结果 Schema

## 逐项目模型输出

模型只负责语义判断，不计算分数。

```json
{
  "project": "项目目录名",
  "case_id": "case id",
  "human_report_core": [
    {
      "id": "HR-001",
      "text": "关键论据概述",
      "quote": "human_report 原文短句",
      "location": "页码或章节",
      "importance": "critical | material"
    }
  ],
  "evidence_classifications": [
    {
      "evidence_id": "EV-001",
      "classification": "used | noise | conflict",
      "human_report_ids": ["HR-001"],
      "reason": "简要依据"
    }
  ],
  "omissions": [
    {
      "human_report_id": "HR-001",
      "human_report_text": "关键论据",
      "human_report_quote": "原文短句",
      "human_report_location": "位置",
      "severity": "critical | material",
      "search_note": "回查过的 source 范围",
      "reason": "缺失了什么支撑"
    }
  ],
  "conflicts": [
    {
      "evidence_id": "EV-007",
      "human_report_id": "HR-003",
      "source_text": "来源论据",
      "source_ref": "文件/页/表",
      "human_report_text": "Human Report 论据",
      "human_report_quote": "Human Report 原文",
      "human_report_location": "位置",
      "conflict_type": "numeric | direction | conclusion | factual",
      "severity": "critical | material",
      "reason": "说明相同口径下的矛盾"
    }
  ],
  "structured_data_gaps": [
    {
      "id": "MG-001",
      "human_report_ids": ["HR-002"],
      "gap_type": "missing | underrepresented",
      "importance": "critical | material",
      "source_fact": "从 source 重新提取的可核验事实",
      "source_ref": "文件/页/表",
      "reason": "现有 Structured Data 缺少了什么"
    }
  ],
  "noise_clusters": [
    {
      "theme": "未使用信息主题",
      "evidence_ids": ["EV-002", "EV-004"],
      "representative_text": "代表性内容",
      "reason": "为何属于未采用信息"
    }
  ],
  "scope_risks": ["口径差异但不构成冲突的事项"],
  "assessment": "一段项目结论",
  "recommendations": ["按优先级给出整改建议"]
}
```

约束：

- `evidence_classifications` 必须覆盖 structured_data 的全部 item，且每个 id 恰好一次。
- `conflict` 分类必须在 `conflicts` 中有对应明细。
- `omissions[*].human_report_id` 必须存在于 `human_report_core`。
- `structured_data_gaps[*].human_report_ids` 必须存在于 `human_report_core`；其中事实必须能从 source
  独立核验，不能复制仅存在于 human_report 的内容。
- `noise_clusters` 可以聚类，但其 id 只能来自 `noise` 分类。
- 所有 quote 均为短摘录，不要复制长段原文。

## 汇总结果

`finalize_results.py` 在逐项目结果上增加：

- `metrics`: 计数、三项问题比例、三项子分和综合分；
- `dataset_summary`: 加权总分、中位数和问题总数；
- `methodology`: 评分版本与固定权重。
