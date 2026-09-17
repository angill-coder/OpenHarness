# 统一篇幅统计

主 Agent 在每个报告版本落盘后运行一次 [计数脚本](../../../resources/report/report_stats.py)，将返回的 JSON 作为该版本的 `reportStats` 保存到已有运行状态/评测记录中。这是允许使用的只读辅助脚本，不是 Report Loop Runner；不安装依赖、不阅读或重写脚本。

优先复用 Evidence 阶段已成功使用的 Python 3 路径；否则使用宿主提供的 Python（如 `WORKBUDDY_EXTRA_PATHS` 中的解释器）。需要查找内置版本时，在实际 WorkBuddy 配置目录（优先 `WORKBUDDY_CONFIG_DIR` / `CODEBUDDY_CONFIG_DIR`，未提供才用用户目录下 `.workbuddy`）读取 `binaries/python/versions/current`：Mac 使用对应版本的 `bin/python3`，Windows 使用 `python.exe`。不要写死用户名或版本；不存在时才尝试系统 `python3` / `python`，用 `--version` 确认能运行 Python 3，不能只检查命令名是否存在。

替换为实际绝对路径后调用（Mac shell）：

```text
"<Python路径>" "<插件根目录>/resources/report/report_stats.py" "<报告绝对路径>" --max-chars 3000
```

Windows PowerShell 使用相同参数，但在带引号的解释器路径前加 `&`。路径作为独立参数保留，不手动替换中文或分隔符。宿主拒绝执行或读取时如实报错，不提权、不绕过沙箱。

以本轮确认的字数上限替换 3000；只给页数时按每页 1000 字折算，未指定默认 3000。沿用 V1 的 Markdown 可见字符口径：表格文字、标题、字母、数字、标点计入，常见 Markdown 标记和空白不计；不是 token 数，也不是排版后的真实页数。

Judge 接收当前版本的 `reportStats`；Writer 修改时接收正确基线的统计值、上限和超出量。报告一经修改就重新统计，不复用旧结果（结果附路径和 SHA-256）。各 Agent 不再人工逐字计数或估算 token，不新增“数完继续压缩”的内部循环，超限随正常改写处理。

脚本不可运行或结果缺失时明确标注“篇幅未核验”，不声称达标、不让子代理尝试不存在的命令工具。反馈直改仍不启动 Judge，但交付时如实说明未核验或仍超限。
