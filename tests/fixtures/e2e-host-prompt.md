请使用 research-report-loop skill 完成一次真实的自动化 Report Loop 测试。

写作任务：根据当前目录中的 `e2e-materials.md`，撰写一份“用户时长增长驱动因素”的战略研究报告。

- 汇报背景：面向产品管理层，用于决定下一阶段应优先优化打开频次还是延长单次使用时长。
- 待验证假设：样本用户总时长增长主要由打开频次提升驱动，而不是单次使用时长变长。
- 重点素材：只以 `e2e-materials.md` 为事实依据。
- 篇幅：约 2 页 Markdown。
- 输出路径：当前目录下 `final-report.md`。

三项写作输入已经齐全，不要再向我提问，也不要停在计划或准备阶段。请完成初稿并写入 Job，由插件 Hook 自动在沙箱外启动 Report Loop（无需也无法调用 MCP 工具）；等待并读取 Runner 的最终 JSON，只交付发布出来的 `<报告主题>-vN.md`。不得伪造 Runner 结果，也不得自行执行 Judge 或 Rewrite。
