# MEMORY.md 模板

文件只放设置、版本和生效 L2，不放索引或过程日志。空库使用：

```markdown
# 写作记忆

revision: 1
enabled: true
lastReflectionAt: null

## Active L2
```

每条 L2 按以下格式追加；示例 ID 和正文需替换：

```markdown
### M-20261009-001-[0] 标准名称

- scope: core
- sourceL1Ids: [L1-001]

有来源支持、以后仍适用、能根据成品评判的写作标准。
```

字段说明：

- ID：`M-YYYYMMDD-NNN-[x]`＝最近实质更新日－当日编号－使用次数；不再单列时间和次数字段。
- `scope`：core / audience / project。后两者加 `- scopeValue: 具体受众或项目`，core 不填。
- `sourceL1Ids`：非空的真实 L1 来源列表。
- 可选 `dimensionCandidate`：`{"name":"...","label":"...","reason":"..."}`，作为同名字段填写，不含权重。

正文可分点，保留适用条件，不另加章节。新库 revision 从 1 开始；已有库不重置，每次实际变更递增一次，仅计数或复盘时间变化不递增。

更新 ID 时见 [容量与统计](memory-capacity.md)；遇到旧格式时才读 [Reflection 的结构整理](memory-reflection.md#1-检查并整理结构)。
