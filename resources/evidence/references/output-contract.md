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

`type` 按事实选择简短类型，如 `quantitative / qualitative / usage_case / analyst_synthesis / user_statement`；这些是示例，不是封闭枚举。`source_ref` 可合并多个真实定位，路径相对本项目来源根目录，或使用可回查的绝对路径。

用户使用案例仍使用这四个字段，不增加顶层 `cases` 或嵌套对象，不修改 v1 Schema。`content` 保存完整场景而非短摘要，可用自然段或“背景/任务/过程/结果/限制”等标签组织；标签不是必填模板。`source_ref` 覆盖用于还原该案例的全部原始位置。分析师归纳或用户直接补充的案例保留 `analyst_synthesis` / `user_statement` 类型，并在正文标明来源性质。

以下是**虚构的格式示例，不是研究证据**，展示超过 1–3 句的案例如何进入 JSON：

```json
{
  "id": "EV-002",
  "type": "usage_case",
  "source_ref": "访谈-用户A.md / Q3–Q5、Q8",
  "content": "背景与任务：用户A是一家小型电商团队的运营，每周一要把三个店铺的售后表整理成周报，以前手工归类投诉。\n使用过程：她将去掉客户姓名的三份CSV上传到产品甲，要求按退款、物流和质量问题分类，并列出对应订单号。第一次输出将换货计入退款，她补充了分类定义后重新生成，再抽查十个订单，将仍然分错的两条手工改回。随后把分类表导出到原有周报模板。\n结果与评价：她自述这次整理约花40分钟，以前通常需要半天；前后耗时均为个人回忆，材料未说明每次订单量是否相同。她说：‘它帮我先分一遍，但数字我还是要自己核。’\n限制：本次仅处理文字售后记录；带图片的质量投诉仍由她逐条查看。材料未说明是否连续多周复用。"
}
```

清洗完成不只检查字段：逐一核对素材中的案例与 JSON，确认用户、任务、操作顺序、材料给出的结果和限制未被压缩丢失；不凭示例补造缺失环节。详细要求见 [清洗规则](cleaning-rules.md)。

校验：顶层字段及 item 字段完全匹配；`schema` 与 `case_id` 正确；`items` 非空；每条字段均为非空字符串；ID 匹配 `EV-` + 至少三位数字且唯一；`unresolved` 是非空字符串组成的数组（无问题时空数组）。ID 连续只用于首次生成，更新后允许间隔。

## 保存与返回

只使用主 Agent 传入的 `outputPath` 和 `workDir`：前者是素材目录内的共享论据表，后者只放必要的解析和扫描记录，不自行改路径。

- 无变化：返回 `EVIDENCE_UNCHANGED`，不改写共享文件。
- 有变化：在内存或系统临时目录构造候选，完整校验并计算 SHA-256。确认共享文件仍与开始时一致（首次生成则仍不存在），才发布到 `outputPath`；更新后重读校验并核对指纹。若发生并发变更或写入失败，返回失败，不覆盖他人更新，也不声称发布成功。
- 不在工作区保存 `structured_data.json` 副本或 previous 备份；临时校验文件用完即清理。报告与评分记录依靠数据版本号和指纹追溯，不承诺可恢复旧数据。

用户更正原文放在 `workDir/user-update.md` 并使用绝对来源路径；原材料的相对来源仍相对于素材根目录，而非工作区。解析中间文件不是独立证据。

论据发布并验证成功后，按 [素材变化识别](source-changes.md) 更新 `数据版本说明.md`，记录数据版本、更新时间、更新摘要及当前指纹清单。格式仍保持上述四个顶层字段，不把文件指纹塞进 Evidence。无事实变化不改共享表、不增加数据版本，但确认当前素材指纹，避免只改排版的文件重复解析。确认失败或资料未读全时不得把旧版本绑定到新数据，返回 PARTIAL/FAILED 并说明；主 Agent 不据此继续写作或评测。

写入、重读与校验成功后返回：

```json
{
  "marker": "EVIDENCE_COMPLETED",
  "operation": "update",
  "path": "共享 structured_data.json 的绝对路径",
  "dataVersion": "D2",
  "dataSha256": "已验证共享论据表的 SHA-256",
  "itemCount": 12,
  "changes": {"added":["EV-012"],"updated":["EV-003"],"removed":[]},
  "unresolved": [],
  "summary": "按更正材料更新了EV-003，新增一条独立论据。"
}
```

返回的 `changes` 只描述本次增量；合并项以保留 ID 的 `updated` 和被合并 ID 的 `removed` 表达，`summary` 简述原因。不要向主会话回传整个 JSON。

数据版本由脚本写入说明文件后返回，不能由模型猜测；首次登记 D1，此后共享数据变化才递增。没有版本说明的旧项目先核验并登记当前基线，不能补造历史。纯用户反馈不改数据时沿用原数据版本。

- `EVIDENCE_UNCHANGED`：校验旧版后确认没有变更，`path` 返回旧路径，changes 全空。
- `EVIDENCE_PARTIAL`：有合法产物但部分资料无法读取或存在影响使用的缺口；返回文件路径、真实问题和已处理范围，不能报全量完成。已有冲突/口径缺口可以保留合法文件，由主 Agent 判断影响。
- `EVIDENCE_FAILED`：无可用论据、文件无法写入或格式校验失败；返回原因，不返回未经验证的文件为可用产物。之前的合法版本仍保留，但不声称它涵盖新资料。

Schema 校验只能证明格式；实际回查来源、判断是否重复或纠错仍由 Agent 完成。不添加外部模型调用、服务或固定路径运行时来做清洗。
