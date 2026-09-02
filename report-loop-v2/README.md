# report-loop-v2

WorkBuddy Native V2。它与现有 `research-report-loop-memory` V1 并列，不覆盖、不复用其 MCP、Hook、Python Runner 或外部 CLI；通过 Skill、原生 Sub-agent 和隐藏状态文件复现 V1 的评分、采纳、停止、恢复和 Memory 行为。

## 当前范围

- 主 Agent 解析素材、确认输入并撰写 V0；
- Memory Agent 使用 `memory: user` 管理 L0/L1/L2B；
- Resolution Judge 根据任务动态冻结 N 个评测维度；
- 参数化 Dimension Judge 按 N 个维度运行；
- 主 Agent按 V1 公式确定性计分并执行候选采纳门槛；
- Rewriter 只从历史最佳版本生成候选，主 Agent保存 Judgment 与恢复状态；
- 用户反馈后先改报告，再 Capture。

V2 不安装平台相关的每日 Automation。Memory Agent 在每日 16:30 后首次被调用时执行当日 Reflection；长期未使用时在下次调用补做，因此不引入 MCP、Hook 或系统计划任务。

## 构建与验证

```bash
npm test
npm run build
```

构建产物位于 `release/report-loop-v2-expert/`，可作为 WorkBuddy Expert 目录检查或打包。构建不会读取或修改 V1。

## 已知待验证项

1. `memory: user` 在 WorkBuddy 实际 Expert 会话中的跨 Session 行为；
2. 主 Agent 对同一个 Dimension Judge 的动态 N 次并行调用；
3. 无 Hook 时反馈 Capture 的稳定触发率；
4. Windows 与 macOS 的同包安装；
5. 原生 Sub-agent 方案和 V1 在报告质量、Token、耗时与失败率上的真实 E2E 差异。
