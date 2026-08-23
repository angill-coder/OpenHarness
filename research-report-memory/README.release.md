# Research Report Memory V2 MVP for WorkBuddy

安装包包含：

- `research-report` Skill；
- `research-report-memory-curator` WB Sub-agent；
- Recall/Capture Memory Guard；
- 本地 Memory MCP；
- 每日 Maintenance Scheduler。

安装：

```bash
./scripts/install-release-workbuddy.sh
```

安装身份：

- plugin：`research-report-memory-v2-mvp@research-report-memory-v2-mvp-local`
- MCP：`research-report-memory-v2-mvp`
- data：`~/.research-report-memory-v2-mvp`

安装后运行 `/reload-plugins`，或完全退出并重新启动 WorkBuddy。

手动维护：

```bash
./plugins/research-report-memory-v2-mvp/scripts/run-memory-maintenance-workbuddy.sh
```

卸载：

```bash
CODEBUDDY_CONFIG_DIR="$HOME/.workbuddy" codebuddy plugin uninstall research-report-memory-v2-mvp@research-report-memory-v2-mvp-local
CODEBUDDY_CONFIG_DIR="$HOME/.workbuddy" codebuddy plugin marketplace remove research-report-memory-v2-mvp-local
CODEBUDDY_CONFIG_DIR="$HOME/.workbuddy" codebuddy mcp remove research-report-memory-v2-mvp --scope user
```

卸载默认保留 `~/.research-report-memory-v2-mvp`。v0.2.2 与 v1 的插件、MCP 和数据不会被修改。
