---
name: research-report-memory-workbuddy
description: 为 WorkBuddy 安装和启用 Research Report Memory 专家包。当用户要求安装、配置、启用或更新 research-report 写作记忆，或希望在 WorkBuddy 中使用带长期写作偏好的调研报告助手时使用。
license: MIT
compatibility: WorkBuddy；已在 macOS 上验证自动注册和定时维护。
metadata:
  skillhub:
    slug: research-report-memory-workbuddy
    displayName: Research Report Memory for WorkBuddy
    version: 1.0.0
    summary: 为 WorkBuddy 安装带 Skill、Hook、Memory MCP 和 Memory Agent 的调研报告写作记忆插件。
---

# Research Report Memory for WorkBuddy

这是 SkillHub 分发入口。完整能力由包内的 WorkBuddy 插件提供，包括：

- `research-report` 写作 Skill；
- 写前 Recall、反馈后 Capture 的流程 Hook；
- Research Report Memory MCP；
- 独立 Memory Curator Agent。

## 安装流程

仅在用户明确要求安装或启用时执行以下操作：

1. 确认当前宿主是 WorkBuddy，且本机可找到 `codebuddy` CLI 或 WorkBuddy 应用。
2. 从本 Skill 所在目录运行：

   ```bash
   sh scripts/install-release-workbuddy.sh
   ```

3. 安装成功后，提示用户执行 `/reload-plugins`，或完全退出并重新启动 WorkBuddy。
4. 重载后由插件内的 `research-report` Skill 接管报告写作；不要用本入口替代正式写作 Skill。

若当前宿主不是 WorkBuddy，不要运行安装脚本；说明此版本的 Hook、MCP 和 Memory Agent 自动注册仅支持 WorkBuddy。

## 边界

- 不把 Memory 调度过程写入报告正文。
- 不修改或清理用户已有记忆。
- 不声称仅安装本入口 Skill 就已启用 MCP 或 Hook；以安装脚本成功和 WorkBuddy 重载为准。
