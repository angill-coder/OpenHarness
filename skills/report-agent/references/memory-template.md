# MEMORY.md 模板与边界

初始化、Capture、Manage 和 Reflection 使用同一结构。下方空库模板可直接使用，不把本参考文档的说明复制进用户记忆：

```markdown
# 写作记忆

revision: 1
enabled: true
lastReflectionAt: null

## Active L2
```

在生效区按以下形式追加真实规则，不复制示例 ID 或占位正文：

```markdown
### MR-001 标准名称

- scope: core
- sourceL1Ids: [L1-001]

有来源支持、以后仍适用、能根据成品评判的写作标准。
```

`scope` 只取 core / audience / project；后两者增加 `- scopeValue: 具体受众或项目`，core 不填。`sourceL1Ids` 非空，指向真实 L1；需要提出新维度时可增加 `- dimensionCandidate: {"name":"...","label":"...","reason":"..."}`，不填写权重。规则正文可分点，保留必要的适用条件与例外，不另加小标题或把无关要求拼成一条。

## 文件各自放什么

- `MEMORY.md` 只放顶部三项元数据和生效 L2；不放目录导航、累计条数、完整索引、复盘结论、每日任务记录或未晋升的要求。
- 用户原话、任务经过与修改对照在 L0；单次要求、待确认偏好和细化证据在 L1；实际修改经过在 `memory-history.md`；时间与使用统计在 `memory-stats.json`。
- 具体不等于错误：有依据的特定受众标准可进入 L2；“本次选了 R2”只是经历。不能把一次性要求换成抽象措辞就当作长期标准。

新库从 revision 1 开始；已有版本继续递增，不重置或整体加一。旧 revision 0 兼容读取，下次实际修改后进入 1。缺少版本时先核验原内容，再登记基线 1，不虚构以前的版本。同一次整理只递增一次；无变化、仅更新复盘时间或使用统计不递增。

旧 L2B 与 L2 同义。旧格式先报告需要整理，保留原内容；实际写入或 Reflection 时按 [Reflection 指引](memory-reflection.md) 迁移。模板不是覆盖文件的授权，不重建空库、不丢弃用户手改内容。
