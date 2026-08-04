# 三类质检判定与评分

## 1. 证据单位

一条证据是可独立核验的论断或数据点。强关联的一组数字可合并；服务不同结论的数字必须拆开。

### 去重

`structured_data.json` 通常是 source 的结构化索引，因此不与 source 重复计数。以下视为同一证据：

- 语义等价且指标、对象、时间、样本和口径一致；
- 仅单位换算、四舍五入或措辞不同；
- 同一访谈的逐字稿、纪要和 structured_data 摘要表达同一事实。

保留最接近原始数据的引用作为主来源，其余写入 `corroborating_refs`。

## 2. Human Report 关键论据

满足任一条件：

- 出现在执行摘要、结论、建议；
- 直接支撑章节标题或主结论；
- 是影响结论方向的关键数字、比较、因果链；
- 在全文反复强调。

背景常识、装饰性案例和不能影响结论的细枝末节不计。单项目最多保留 40 条，超限按“结论/建议 > 摘要 > 章节主论点 > 关键数据”抽样并披露。

## 3. 遗漏项

关键论据在 structured_data 中无充分支撑，且定向回查 source 后仍无充分支撑。

- Human Report 有精确数字、source 只有定性方向：遗漏。
- Human Report 为综合推导，source 有构成数据且推导可复算：不遗漏。
- Human Report 只是常识性背景：不纳入关键论据，不判遗漏。
- 原文件无法解析：标记 `unverified`，不得直接判定遗漏。

如果 structured_data 中没有充分支撑，但定向回查 source 后找到了可核验证据，
不得列为数据遗漏；应列入 `structured_data_gaps`，说明这是 Structured Data 抽取遗漏或
表达不完整。

严重度：

- `critical`：影响执行摘要、核心结论或建议。
- `material`：影响重要分论点或关键数据，但不改变总方向。

## 4. 冲突项

来源论据与 Human Report 关键论据或核心论点，在相同对象、时间、样本与口径下出现：

- 数值矛盾；
- 趋势/方向相反；
- 因果或结论矛盾；
- 基础事实矛盾。

数值容差：相对误差不超过 1%，或比例绝对差不超过 0.5 个百分点，取较宽者。单位换算、合理取整不算冲突。

不同时间、样本、地域、预测/实际或统计口径可解释差异时，不算冲突；可列为口径风险，但不进入冲突数。

所有冲突都是严重干扰：

- `critical`：可能改变执行摘要、结论或建议；
- `material`：会误导重要分论点或关键数字。

每条冲突必须有 Human Report 表述、来源表述、source_ref 和冲突说明。

“现有数据不足以推出 Human Report 的因果/迁移结论”本身不是冲突，因为 source 没有给出相反
事实。此时应把可支持的来源证据标为 `used`，并把超出证据支持的 Human Report 部分记为遗漏；
不得以同一问题同时记遗漏和冲突、重复扣分。

## 5. 噪声项

来源证据未被 Human Report 使用，且不构成冲突。来源证据必须被完整分区：

```text
source_evidence_total = used + noise + conflict
```

噪声率：

```text
noise_rate = noise / source_evidence_total
```

冲突不放进噪声分子，避免一条问题被重复惩罚。报告明细可按主题聚类，只展示代表项，但 JSON 必须保留每条来源证据的分类。

## 5.1 Structured Data 完整性缺口

`structured_data_gaps` 只记录“事实已经存在于 source，但现有 Structured Data 没有充分
保留”的情况，不属于数据遗漏，也不参与数据质量评分。

- `missing`：source 中存在可独立核验的重要事实，Structured Data 完全未提取。
- `underrepresented`：Structured Data 提到了该主题，但遗漏关键数字、分群、反例、
  限定条件或其他会影响判断的内容。

每项必须给出 source 中可独立核验的事实和准确 `source_ref`。不得把只存在
于 human_report、无法从 source 复核的内容写入 `structured_data_gaps`。

## 6. 固定评分

### 子分

```text
omission_score = 100 × (1 - omission_count / human_report_core_total)
conflict_score = max(0, 100 - 20 × critical_conflicts - 10 × material_conflicts)
signal_score = 100 × (1 - noise_rate)
```

`human_report_core_total=0` 时该项目不可评分，必须修正抽取结果，不得用 100 代替。

### 综合分

```text
overall_score =
  0.40 × omission_score +
  0.40 × conflict_score +
  0.20 × signal_score
```

理由：遗漏与冲突同等影响报告可信度；噪声主要增加检索与误采成本。

报告只展示分数/100，不输出字母评级。

### 数据集总分

对项目分按 `human_report_core_total` 加权平均；同时报告中位数，防止大型报告遮蔽小项目。

## 7. 报告中的问题比例

- 遗漏项比例 = `omission_count / human_report_core_total`，同时披露关键遗漏与普通遗漏条数。
- 冲突项比例 = `conflict_count / human_report_core_total`，同时披露关键冲突与普通冲突条数。
- 噪声项比例 = `noise_count / source_evidence_total`。

报告不输出置信度。解析盲区和方法限制继续写入“口径与可核验风险”，但不单独评级。
