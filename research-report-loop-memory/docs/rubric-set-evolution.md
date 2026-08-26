# Memory Rubrics 与本轮 Resolution

## 目标

Memory Agent 保持开放判断，只维护独立 Memory Rubrics；Base Rubrics 始终由产品版本管理。Report Loop 启动时，由一个 Judge Model 结合当前任务解释 Base + Memory，一次冻结后再交给六维 Judge，避免六个 Judge 各自理解导致不一致。

```text
Memory Agent：维护独立 Memory Rubrics
                 ↓
report_loop_start：读取 core + audience + project 候选
                 ↓
Resolution Judge：additional / interpret / ignore
                 ↓
冻结 Resolution Plan + compiled rubric
                 ↓
六维 Judge 并行评测，不再重新解释 Memory
```

## Memory Rubric

Memory Rubric 不绑定 Base Dimension 或 Check：

```json
{
  "id": "MR-SUMMARY-CONCISE",
  "statement": "报告摘要控制在 2–3 行，只呈现核心观点及关键推导逻辑。",
  "status": "active",
  "sourceL1Ids": ["atom-1", "atom-2"]
}
```

Scope 保存在文档层：`core / audience / project`。仓库只保存一份 core、每个 audience 一份、每个 project 一份，文件数量线性增长，不生成 Audience×Project 组合文件。每次变更形成 Git commit，可审查和回滚。

Memory Agent 只遵守三条原则：

1. 保存未来可从报告直接判断、且会影响质量结论的稳定用户标准。
2. 优先整合、纠正和精简现有 Memory，保留 Scope 与 L1 来源。
3. 只维护 Memory Rubrics，不修改、删除或预先映射 Base Rubrics。

## Resolution Plan

只要仓库存在 Memory Rubric 候选，就调用一次 Resolution Judge；真正空仓库不调用模型。Provider 不按 audience/project 字符串预筛选，scope/scopeValue 作为语义线索随候选交给模型。每条候选 Memory 必须得到一个决定：

- `additional`：在一个现有六维中新增本轮检查，不新建维度、不改变权重。
- `interpret`：细化或替代一条非红线 Base Check 在本场景的适用方式；编译结果明确“本轮以场景解释为准”，但不改写 Base 文件。
- `ignore`：重复、无关或无法可靠判断，本轮不使用。

硬约束由 Runtime 执行：Base 不可变；红线不可 interpret；每条 Memory 只进入一个六维；计划必须覆盖全部选中 Memory。Resolution 失败时按 Base-only 继续，并保存失败原因，六维 Judge 不临时猜测。

## 每轮冻结文件

每个 run 保存：

- `base_rubric.json`：本轮 Base 快照；
- `memory_rubrics.json`：全部 Scope 候选的 Memory 快照；
- `rubric_resolution_plan.json`：一次模型解释的冻结结果；
- `compiled_rubric.json`：六维 Judge 实际使用的标准。

后续 report rewrite 与再次 Judge 始终使用同一 `compiled_rubric.json`，直到新建下一轮 Report Loop。
