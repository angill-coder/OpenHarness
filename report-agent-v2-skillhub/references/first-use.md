# 配套子代理启用

仅在当前会话缺少配套子代理时阅读。以宿主实际提供的 Agent 列表或真实调用结果为准，不把本文提到的名字当成已注册，也不为检查而额外发起一轮模型测试。

使用宿主提供且可运行的 Python 3；解释器定位方式见 [统一篇幅统计](report-length.md) 中的 Python 说明，不安装依赖。将以下路径替换为随当前 Skill 安装的 [注册脚本](../resources/workbuddy/register_agents.py) 的绝对路径后运行，不阅读或重写脚本：

```text
"<Python路径>" "<包根目录>/resources/workbuddy/register_agents.py"
```

Windows PowerShell 在带引号的解释器路径前加 `&`。不传参数只检查；脚本自动定位自身包目录和实际 WorkBuddy 配置目录，只读取对应注册信息，不扫描磁盘。

- `registration_required`：先告诉用户“首次使用或更新后，需要配置配套子代理，我会自动补齐注册。”再执行同一命令并追加 `--apply`。仅当返回 `registered` 时，告知注册完成、请新开会话继续；当前会话到此暂停，不先运行写作流程。
- `ready`：注册记录已正确，不重复写入；当前会话仍缺少子代理时提示新开会话加载。若用户已重开仍失败，报告加载故障，不重复注册或反复要求重开。
- `disabled` / `other_installation`：存在明确禁用或另一份安装，不擅自启用、替换或重复安装，向用户说明并确认使用哪一份。
- `error`：如实说明配置未完成，不伪造成功、不绕过权限或企业策略、不手改其他配置。

脚本只补 `plugins/installed_plugins.json` 与 `settings.json` 的安装、启用记录，写前备份；不搬动包、不新增市场、不改 SkillHub 更新信息，也不写额外“已注册”标记。记录已正确时不修改文件。原生插件/专家安装走宿主原有安装入口，不额外注册成 SkillHub 插件。
