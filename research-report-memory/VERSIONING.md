# Versioning

本目录是融合方案的独立 MVP，不覆盖既有版本。

| 版本 | 源码目录 | 插件 / MCP | 数据目录 |
|---|---|---|---|
| v0.2.2 留档 | `research-report-memory-mvp` | `research-report-memory` | `~/.research-report-memory` |
| v1 留档 | `research-report-memory-v1` | `research-report-memory-v1` | `~/.research-report-memory-v1` |
| V2 MVP `2.0.0-mvp.35` | `research-report-memory-v2-mvp` | `research-report-memory-v2-mvp` | `~/.research-report-memory-v2-mvp` |

版本升级不得覆盖其他版本的数据目录。V2 MVP 尚不提供 v1 自动迁移；后续迁移需要显式读取旧 L0/L1，重新经过 Memory Agent 晋升和 Context Repository Commit。

## 更新 research-report 写作 Skill

写作规则与 Memory 调度分开维护：

- `upstream/research-report/` 保存未经改写的上游写作 Skill。
- `skills/research-report/references/memory-orchestration.md` 是稳定的 Memory 调度契约。
- `hooks/memory-guard.mjs` 独立检查 Recall/Capture 是否完成。

收到新版 `SKILL.md` 和 `instructions.md` 后运行：

```bash
npm run sync:research-report-skill -- /absolute/path/to/SKILL.md /absolute/path/to/instructions.md
```

脚本会保存纯写作上游版本，并在可安装 Skill 中自动加入一条对 Memory 调度契约的引用。无需在每次写作 Skill 更新后手工复制 Recall/Capture 规则。
