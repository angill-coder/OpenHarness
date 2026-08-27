# 报告专家V1

面向 WorkBuddy 的研究报告写作 Expert。用户提交访谈、问卷、数据或文档后，Expert 会按 `research-report-loop` Skill 完成素材解析、报告写作、自动评测和迭代改写。长期写作记忆默认关闭；用户明确要求开启后，才会从后续反馈中沉淀 Memory Rubrics。

## 内置能力

- `research-report-loop` Skill：报告写作与交付流程
- `report-expert-v1` MCP：Report Loop Launcher 与写作记忆服务
- Python Report Loop：Resolution、Judge、Rewrite 和停止条件
- Memory Curator：反馈后的实时记忆整理
- Memory Reflection：Memory 开启后自动注册的每日定时复盘；Memory 关闭时不调用模型

Expert 与完整插件使用同一套运行代码和用户数据目录；更新 Expert 不会清空已有 Memory 或报告运行记录。
