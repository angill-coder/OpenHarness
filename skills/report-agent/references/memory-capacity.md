# L2 容量与使用统计

使用包内 `resources/report/memory_stats.py`，仅 Python 标准库；沿用 [篇幅统计](report-length.md) 已确认的 Python 路径和 Mac / Windows 调用方式，不安装依赖。下列路径都替换为实际绝对路径；Windows PowerShell 在带引号的解释器前加 `&`。只访问已授权的 `memoryRoot`，失败不另选目录或绕过权限。

## Memory Agent：检查与整理

双上限：生效 L2 最多 1,000 条，整个 `MEMORY.md` 最多 0.5 MiB（524,288 字节，含设置、字段与正文，按 UTF-8 文件实际字节计算）。任一超限返回 `MEMORY_CAPACITY_EXCEEDED`；条数未超也要检查大小。输出 `bytes`、`byteLimit`、`excessBytes`，草稿检查与保存后同步使用同一上限。

这是存储上限，不是上下文预算：0.5 MiB 可容纳约 17.5 万个纯中文字；扣除标题、Scope、来源等字段后，粗略按 15 万字正文估算，实际以文件字节数为准。达到 1,000 条时平均每条约 140–150 个中文字，大小限制可能先于条数限制触发。不限制单条固定字数，不允许以巨型 Rubric 或搬到其他生效文件绕过限制。

```text
"<Python>" "<脚本>" --root "<memoryRoot>" validate
"<Python>" "<脚本>" --root "<memoryRoot>" check
"<Python>" "<脚本>" --root "<memoryRoot>" check --memory "<待写入的MEMORY草稿>"
```

`validate` 按 [MEMORY 模板](memory-template.md) 检查章节、设置、ID、Scope、来源字段与正文是否齐全；同样支持 `--memory` 校验草稿。旧格式或字段问题返回 `MEMORY_FORMAT_NEEDS_REVIEW` 和具体问题（退出码 2），保留原文件，交给 Curator 整理，不当作空库。它不判断来源是否真实、内容是否长期有效，也不自动迁移。

`check` 先做同一结构检查，再统计全库生效 Rubric，不分 Scope 配额。返回条数、超出量及 `memory-stats.json` 的时间/热度，不读取 L0/L1。超过 1,000 条返回 `MEMORY_CAPACITY_EXCEEDED`（退出码 2）；权限、读取或统计损坏返回 `MEMORY_STATS_FAILED`（退出码 1），不能当作零条继续写。

新输出遵循模板；旧 L2B 仍按 L2 理解，但写入前需规范结构。旧内容的移动及语义复核按 [Reflection 指引](memory-reflection.md) 处理，不能只改标题后宣称完成。只读时报告需整理，不重建空库。

检查当前文件与拟写草稿，超过上限按成员 Prompt 的压缩原则整理。草稿通过后重新核对磁盘 revision 和用户修改，再写入并读回检查。首次空库先由 Memory Agent 初始化规范空区。已有超限也可检查，不会被脚本自动删除。

## Memory Agent：记录内容时间

L2 成功保存后，将本次实际变化写成临时 JSON，调用后不必作为用户产物交付：

```json
{"revision": 12, "newIds": ["MR-009"], "updatedIds": ["MR-002"], "merges": {"MR-003": ["MR-004"]}}
```

```text
"<Python>" "<脚本>" --root "<memoryRoot>" sync --changes "<本次变化JSON>"
```

- `newIds` 仅限确实新形成的规则，生成 `createdAt` / `updatedAt`；旧规则未有可靠时间时保持未知，不能用今天冒充创建日。
- `updatedIds` 仅限要求实质变化；标点、格式、来源补充、同义精简不填。语义无关的新标准用新 ID，不继承旧热度。
- `merges` 是保留 ID → 已退出生效区的来源 ID。脚本合并历史使用并按 Loop 去重，沿用已有时间；纯合并不算变新。删除不清空来源统计，以支持审计和在途 Loop。相同 revision 和相同变化重试不重复更新时间。
- 统计缺失的旧记录时间未知；`useCount` 仅代表 `trackingSince` 后观测到的次数，不是终身次数。统计更新不推进 MEMORY revision；统计失败说明待补，不重新写规则。

## 主 Agent：记录实际使用

Memory 开启时，在已有 Judge 结果核验流程确认维度结果有效后调用（多个有效结果可合为一次调用）：

```text
"<Python>" "<脚本>" --root "<memoryRoot>" record-use --run-id "<run-state.runId>" --plan "<冻结resolution-plan.json>" --result "<本轮已核验的单维结果.json>"
```

多个结果重复传 `--result`。脚本从已评测维度的 `memoryRubricIds` 取 ID，并检查激活决定与 Check 完整性；主 Agent 仍负责本轮 assignment、报告、数据绑定核验，不能传历史或失败结果。只冻结未评测、`ignore`、Base-only 都不增加热度。按 `runId + rubricId` 去重，同 Loop 的多维、R0/R1、重试和恢复仅计一次；新 Loop 使用新的 runId。

脚本只写统计，不调用 Capture、不生成用户偏好。失败在现有运行记录标为统计待补，可用相同输入补记，不重跑 Judge或阻断报告交付；记忆关闭时不调用。统计写入有互斥锁及原子替换，忙时稍后重试，不能删掉其他进程的锁。

整理原则参考 [Letta memory audit](https://github.com/letta-ai/letta-code/blob/main/src/skills/builtin/context-doctor/references/auditing-memory.md)：依据当前内容做有证据的小范围整理，保留重要约束，缩短本身不是目标。1,000 条及热度统计是本项目约定。
