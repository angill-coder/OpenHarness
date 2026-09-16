# Report Agent V2 · SkillHub

根目录 `SKILL.md` 就是完整的 `research-report-agent-v2` 主入口；随包包含 6 个 Agent、执行卡、Base Rubrics 和标准库 Python 辅助脚本，与专家版、插件版来自同一份源码。

通过 SkillHub 安装后正常发起报告任务即可。如果当前会话缺少子代理，主 Agent 会先检查注册，告知后补齐 WorkBuddy 的安装和启用记录，再提示新开会话继续。文件仍保存在 SkillHub 安装的 `skills/` 目录，不复制到缓存、不创建额外市场、不修改 SkillHub 更新信息。

注册记录本身就是启用存档，不额外保存成功标记。路径或包内插件版本变化时可重新核对；已经正确则不写入。新开会话仍无法调用子代理时，应排查加载故障，而不是反复安装。明确禁用、另一份安装或权限限制会停止自动配置。

注册只针对 WorkBuddy。macOS / Windows 使用同一 Python 脚本，无第三方依赖；当前版本的两处注册方式已在本机验证，仍需在对应 WorkBuddy 版本验证实际子代理调用。SkillHub 删除本包后，插件注册记录可能残留，可通过 WorkBuddy 插件管理解除；不要手动删除其他插件记录。

本目录是直接提交、可用于 SkillHub 分发的完整包体，不包含构建脚本。开发以 `report-agent-v2-expert/` 为准，修改后同步四份包体；本包只调整根 Skill 入口和相对路径，不单独修改工作流。版本与一致性检查见仓库根目录 README。
