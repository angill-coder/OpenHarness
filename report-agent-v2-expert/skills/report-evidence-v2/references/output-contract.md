# 输出与更新格式

`structured_data.json` 严格使用同目录 [structured_data.schema.json](structured_data.schema.json)。该 Schema 原样复用 OpenHarness；只包含以下四个顶层字段，每条论据也只有四个字段。

```json
{
  "schema": "openharness-structured-data/v1",
  "case_id": "example-project",
  "items": [
    {"id":"EV-001","type":"quantitative","source_ref":"usage.csv / 第2行 / 2026-06","content":"2026年6月，样本100名用户，月均使用时长为12小时；材料未说明抽样方法。"}
  ],
  "unresolved": ["usage.csv 未说明抽样方法。"]
}
```

`type` 按事实选择简短类型，如 `quantitative / qualitative / analyst_synthesis / user_statement`；这些是示例，不是封闭枚举。`source_ref` 可合并多个真实定位，路径相对本项目来源根目录，或使用可回查的绝对路径。

校验：顶层字段及 item 字段完全匹配；`schema` 与 `case_id` 正确；`items` 非空；每条字段均为非空字符串；ID 匹配 `EV-` + 至少三位数字且唯一；`unresolved` 是非空字符串组成的数组（无问题时空数组）。ID 连续只用于首次生成，更新后允许间隔。

## 保存与返回

只使用主 Agent 传入的 `outputPath` 和 `workDir`：前者是素材目录内的共享论据表，后者用于本轮快照与解析文件，不自行改路径。

- 无变化：返回 `EVIDENCE_UNCHANGED`，不改写共享文件。
- 有变化：先把读入的原共享文件保存为 `workDir/previous-structured_data.json`，将完整候选写入 `workDir/structured_data.json` 并重读校验。确认共享文件仍与开始时一致（首次生成则仍不存在），才用已验证的候选更新 `outputPath`；更新后核对内容一致。若发生并发变更或写入失败，返回失败，不覆盖他人更新，也不声称发布成功。
- 本轮快照与已有报告快照不再覆盖；无变化时由主 Agent 将采用的共享内容保存为本轮快照。只用 `workDir/structured_data.json` 这一份，不另建 snapshot 文件；历史快照不随共享文件更新而改变。

用户更正原文放在 `workDir/user-update.md` 并使用绝对来源路径；原材料的相对来源仍相对于素材根目录，而非工作区。解析中间文件不是独立证据。

写入、重读与校验成功后返回：

```json
{
  "marker": "EVIDENCE_COMPLETED",
  "operation": "update",
  "path": "共享 structured_data.json 的绝对路径",
  "previousPath": "更新前副本的绝对路径；首次为 null，无变化时为原路径",
  "itemCount": 12,
  "changes": {"added":["EV-012"],"updated":["EV-003"],"removed":[]},
  "unresolved": [],
  "summary": "按更正材料更新了EV-003，新增一条独立论据。"
}
```

返回的 `changes` 只描述本次增量；合并项以保留 ID 的 `updated` 和被合并 ID 的 `removed` 表达，`summary` 简述原因。不要向主会话回传整个 JSON。

- `EVIDENCE_UNCHANGED`：校验旧版后确认没有变更，`path` 返回旧路径，changes 全空。
- `EVIDENCE_PARTIAL`：有合法产物但部分资料无法读取或存在影响使用的缺口；返回文件路径、真实问题和已处理范围，不能报全量完成。已有冲突/口径缺口可以保留合法文件，由主 Agent 判断影响。
- `EVIDENCE_FAILED`：无可用论据、文件无法写入或格式校验失败；返回原因，不返回未经验证的文件为可用产物。之前的合法版本仍保留，但不声称它涵盖新资料。

Schema 校验只能证明格式；实际回查来源、判断是否重复或纠错仍由 Agent 完成。不添加外部模型调用、服务或固定路径运行时来做清洗。
