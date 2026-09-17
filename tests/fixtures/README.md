# 手工端到端测试素材

这些文件不被自动化测试引用，用于**真实环境**的人工验证——自动化测试全部使用
StubRuntime，不会真的调用 Judge 或 Rewriter。

用法：把 `e2e-materials.md` 放进一个空目录，然后把 `e2e-host-prompt.md` 的内容
发给专家团，观察：

- 产物是否落在 `<素材目录>/报告/` 与 `.report-agent/<报告标识>/loop-001-*/`
- 交付版本是否为 `<报告主题>-v1.md`，`版本说明.md` 是否登记
- 给一条写作反馈后，`~/ReportAgentMemory/` 是否按契约长出 MEMORY.md / L0 / L1

`e2e-report.md` 是一份参考产出，仅用于比对结构，不是期望答案。
