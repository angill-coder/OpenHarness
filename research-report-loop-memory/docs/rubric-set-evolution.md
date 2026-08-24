# Rubric Set 演进与 Scope 解析

## 目标

L2B 不在每轮 Judge 前作为独立 Check 列表临时追加。Memory Curator 在 L2B 证据通过 Gate 后更新 Git-backed Rubric Set，形成新版本；Report Loop 只读取当前版本并做确定性 Scope 解析。

## Rubric Set

一个版本包含四层定义：

```text
Base Rubric
└── Core Overlay
    └── 当前 Audience Overlay
        └── 当前 Project Overlay
```

仓库只保存一个 Base、一个 Core、每个 Audience 一个 Overlay、每个 Project 一个 Overlay，文件数量按 `Audience + Project` 线性增长，不保存 `Audience × Project` 的完整组合。

## Criterion Slot

每个 Check 使用稳定 `criterionKey` 表示语义槽。Scope 优先级只在相同 Criterion 上生效；不同 Criterion 即使同属一个 Dimension 也同时保留。

Overlay 操作：

- `add`：新增独立 Criterion。
- `extend`：方向一致，在现有 Criterion 上增加结构化 `requirements`。
- `override`：方向冲突，用当前 Scope 的完整标准替换低优先级定义。
- `disable`：当前 Scope 明确不适用；Base 红线不可停用。

解析顺序固定为 `Base → core → audience → project`。运行时不调用模型，不做自然语言语义判断。

## Personal Dimension

只有可观察、可评判且无法归入基础六维的长期要求才进入 `personal`。当前场景没有适用 Personal Check 时不生成该维度，基础六维权重不变；存在时 `personal=0.10`，基础六维按原比例缩放到合计 `0.90`。

## 版本与冻结

每次有效 Overlay 修改在同一个 Git Commit 中：

1. 更新相关 Scope 文档；
2. 递增 `manifest.json` 的 `vN`；
3. 更新来源记录；
4. 生成只读 `views/rubric-set.md`。

Report Loop 启动时记录 Rubric Set Git HEAD、版本、Scope 和 Resolver Hash，并把解析结果写入本轮 `compiled_rubric.json`。同一 Loop 后续版本始终使用该冻结文件。

## 存储约定

```text
l2b-rubrics/personal/default/
├── manifest.json
├── system/rubrics.json
├── audiences/<canonical-id>/rubrics.json
├── projects/<canonical-id>/rubrics.json
├── views/rubric-set.md
├── .memory/provenance.jsonl
└── .git/
```

JSON 是执行真相；Markdown 是自动生成的人类可读视图，不能反向覆盖 JSON。
