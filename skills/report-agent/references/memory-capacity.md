# L2 容量与使用统计

生效 L2 全库最多 **1,000 条**，整个 `MEMORY.md` 最大 **0.3 MB（300,000 字节）**，任一超限就需压缩。约为 10 万个 UTF-8 常见汉字，字段和格式也占空间。

## Curator：检查容量与填写日期

`resources/report/memory_stats.py` 只读检查格式、条数和大小，不维护统计 JSON，不写记忆。仅需 Python 标准库，解释器选择沿用 [篇幅统计](report-length.md)，Windows PowerShell 在带引号的解释器前加 `&`。

```text
"<Python>" "<脚本>" --root "<memoryRoot>" validate
"<Python>" "<脚本>" --root "<memoryRoot>" check
"<Python>" "<脚本>" --root "<memoryRoot>" check --memory "<MEMORY草稿绝对路径>"
```

Capture、Manage 修改 L2 前后及每次 Reflection 检查；超限时才读取 [记忆压缩](memory-compression.md)，整理后保存并读回检查。格式错误返回 `MEMORY_FORMAT_NEEDS_REVIEW`，超限返回 `MEMORY_CAPACITY_EXCEEDED`（退出码 2）；访问失败返回 `MEMORY_STATS_FAILED`（退出码 1），不能当作空库。脚本不判断长期价值或来源真实性。

按 [MEMORY 模板](memory-template.md) 在新增或实质更新 L2 时维护 `M-YYYYMMDD-NNN-[x]` 中的日期与编号；格式整理和使用计数不刷新日期。不另写日期、次数字段，不生成变化 JSON 或调用统计写入脚本。

编号从 001 递增、至少三位，超过 999 自然扩展；查当前条目和变更历史，不复用已用编号。同日更新保留编号，跨日实质更新分配新日期下的编号。同一规则保留次数，新规则从 `[0]` 开始，不继承无关规则的热度。

改日期或编号时在 `memory-history.md` 记录旧前缀 → 新前缀及理由，保留 sourceL1Ids。历史 Plan、Judge、L0/L1 的旧引用不改写，必要时沿映射查当前条目；冻结后的本轮继续使用冻结 ID，不因计数变化判定评分失效。

## Resolution：填写使用次数

主 Agent 校验并冻结 Plan 后，让同一个 Resolution 成员更新 `MEMORY.md`：取各维度 `memoryRubricIds` 的去重集合，只将本轮激活规则 ID 末尾的 `[x]` 加 1；忽略项、Base-only、失败或仍待溯源的 Plan 不计。此值表示被激活进入评测标准的 Loop 次数，不表示 Judge 已完成次数。

沿用本轮 `run-state` 保存 `memoryUsage`（runId、冻结 ID、当前前缀、计数 before/after、pending/completed），不新增统计文件。先按日期和编号前缀定位；前缀已改则沿变更历史核验，旧格式未迁移、规则已删除或对应不明时仅标待补。多个冻结 ID 映射到同一条目只计一次。保存 pending 后，磁盘次数等于 before 才写 after，已等于 after 不重复增加；读回后标 completed。重试沿用原记录，同 Loop 多维、R0/R1 及恢复只计一次；不一致或并发状态不明时标待核对，不猜测补加。

仅修改标题 ID 的 `[x]`，不改规则、日期、编号或 revision，不生成 Episode，也不回写冻结 Plan 的 ID。主 Agent 串行调度记忆写入，避免与 Curator 同时改文件；写前重读并保留用户修改。失败不阻断报告交付，也不重跑 Judge。纯计数更新不触发压缩或 Capture，下一次 Curator 检查容量。
