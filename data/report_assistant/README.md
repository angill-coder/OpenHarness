# 业务汇报助手 · 数据集 + 校准集

对应 `业务汇报助手_Rubric落地文档.md` §8「数据集要求」。这是 rubric 的另一半——rubric 定义"什么是好",数据集决定"在什么样本上比好坏"。二者都是专家资产,缺一不可。

## 文件

| 文件 | 内容 |
|------|------|
| `build_dataset.py` | 可复现生成器(种子固定 `SEED=20260705`)。改了要重跑。 |
| `dataset.jsonl` | 28 条 case,一行一个 JSON。train/dev/test 三分。 |
| `human_labels.jsonl` | 校准集:每条 case 的**人工分维度打分**(§6 meta-eval 用)。此处为模拟专家分,真实项目由业务专家手工填。 |

> 生成:`python3 build_dataset.py`

## 分布(满足 §8「覆盖真实分布」)

- **4 种报告类型** × 2 种受众:`monthly_biz_review`(exec)、`weekly_update`(team)、`ops_brief`(exec)、`project_progress`(team)。
- **切分**:train 14 / dev 7 / test 7(≈50/25/25)。分层打散,**test 里也含硬 case**。
- **test(held-out)在优化全程不可见**,只用于防过拟合验证(见 harness 的 dashboard 过拟合标记)。

## 硬 case(满足 §8「必须含硬 case」)

16/28 条带硬特征——**这些"硬骨头"才是区分 skill 好坏的地方**。四类,各 5 条(含复合):

| tag | 含义 | 考察 skill 什么 |
|-----|------|----------------|
| `anomaly` | 注入区域环比异常(如西南 -21%) | 能否**识别并写进**异常/风险段(抓漏报) |
| `missing_data` | 某数据源缺失(如 customers_new=null) | 是否**标记数据缺口**而**不对缺失项编数字** |
| `unit_confusion` | 只有环比数据,埋同比诱饵 | 是否**不把环比说成同比**(口径正确) |
| `contradiction` | 两来源给出冲突的 arr | 是否**标记矛盾**而非随便挑一个当真 |

复合 case(两个 tag 叠加)最难,是天花板测试。

## 关键字段:为什么 judge 靠它打分

每条 case 的核心是 `human_report_findings` —— **正确计算出的事实,每条带 `id` / `value` / `source_ref`**。

- judge 靠它核对**可回溯性**:报告里的数字能不能对上某条 finding。
- judge 靠它抓**编造**:报告里出现了 `input.raw` 和 findings 里都没有的数字 = 编造 → 数据准确性红线(<3)。
- `key_finding_ids`:必须被写进报告的关键 finding(如异常、现金跑道)。漏了 = 完整性漏报。

```
case = {
  case_id, report_type, audience, required_sections,
  hard_case_tags: [...],
  input: { raw: {...含缺口/异常/矛盾...}, notes: [...] },
  human_report_findings: [ {id, metric, value, unit, source_ref, [is_gap|is_conflict]} ],
  key_finding_ids: [...],   # 必须覆盖,否则算漏报
  split: train|dev|test
}
```

## 校准集(§6)

`human_labels.jsonl` 每条给出**分维度人工分**:

```
{ case_id, split, human_scores: {data_accuracy, completeness, insight, conciseness}, labeler }
```

harness 的 `calibration.py` 用它算 judge↔人工一致率,**门槛 ≥0.85 才许开优化**(否则是在优化一个坏裁判)。

> 注意:本仓库的 human_scores 是**确定性模拟专家分**(基于硬特征数 + case_id 哈希抖动),目的是让 meta-eval 一致率有真实但可收敛的差距,便于演示。**真实项目里这一栏必须由业务专家手工按 rubric 打分。**
