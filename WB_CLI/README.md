# WB_CLI

批量运行 WorkBuddy 案例，支持指定模型和 Skill、并发执行、重复采样、多轮对话，并自动保存对话、工具操作和文件产物。

## 快速开始

```bash
python -m WB_CLI run \
  --dataset WB_CLI/sample/multi_cases.json \
  --skill research-report \
  --model deepseek-v4-pro \
  --parallel 3 \
  --repetition 1 \
  --output workbuddy_batch_runs
```

脚本会为每个 case 使用独立 workspace，并以 `bypassPermissions` 运行，确保 Bash、Python 等报告流程可以非交互执行。独立 workspace 用于分隔输入和产物，不是安全沙箱；请只在可信环境和可信数据上运行。

这条命令会：

- 读取 JSON 中的多个 case，最多同时运行3个；
- 每个 case 运行1次，并在同一 session 中依次发送第0轮到第N轮 prompt；
- 将结果写入 `workbuddy_batch_runs/<run-id>/`。

`parallel` 是同时运行的独立任务数；`repetition` 是每个 case 的独立运行次数。比如10个 case、`repetition=2`，共运行20次。每个 case 默认最多运行15分钟，包含它的全部对话轮次。

## 数据集

推荐使用 JSON。每个 case 包含参考资料和自己的多轮用户输入：

```json
{
  "cases": [
    {
      "id": "case-001",
      "input_files": [
        {
          "source": "materials/case-001",
          "target": "materials"
        }
      ],
      "turns": [
        {
          "round": 0,
          "label": "task",
          "prompt": "请基于提供的参考资料撰写分析报告。"
        },
        {
          "round": 1,
          "label": "brief_confirmation",
          "prompt": "Brief 确认，请继续。"
        },
        {
          "round": 2,
          "label": "analysis_confirmation",
          "prompt": "采用方案二，请继续。"
        }
      ]
    }
  ]
}
```

- `cases`：独立输入列表，每个 case 可以有不同资料和不同轮数。
- `input_files.source`：参考文件或文件夹；相对路径以 JSON 所在目录为基准。
- `input_files.target`：复制到 case 隔离 workspace 后的位置；省略时使用 source 名称。
- `turns`：从 `round: 0` 开始连续编号，每项都是一轮模拟用户输入。

同一 case 的 turns 串行执行并通过 `--resume` 保持上下文；不同 case 和 repetition 使用独立 session、workspace 和输出目录。

也支持 JSONL 和 CSV。旧的 `prompt + interactions` 格式仍兼容，新数据建议使用 `turns`。

## 查看结果

运行结束后，终端会打印结果目录。优先查看以下文件：

| 文件 | 内容 |
|---|---|
| `results.md` | 整批状态、耗时、最终输出摘要及各 case 链接 |
| `cases/<case-id>/conversation.md` | 按 `user / workbuddy / tool` 排列的完整记录 |
| `cases/<case-id>/case.json` | 该 case 的输入与实际生效配置 |
| `cases/<case-id>/artifacts/` | 最终生成或修改的报告文件 |
| `cases/<case-id>/trace/` | 结构化结果、工具操作、原始事件和运行现场 |

当 `repetition>1` 时，case ID 会变成 `case-001__rep_001`、`case-001__rep_002` 等。

## 常用参数

| 参数 | 默认值 | 说明 |
|---|---:|---|
| `--dataset PATH` | 必填 | JSON、JSONL 或 CSV case 文件 |
| `--skill NAME` | 必填 | 已安装 Skill 名称，可重复指定 |
| `--skill-path PATH` | 无 | 本地 Skill 目录，可替代 `--skill` |
| `--model NAME` | WorkBuddy 默认 | 批次模型，case 可单独覆盖 |
| `--parallel N` | `2` | 同时运行的独立任务数 |
| `--repetition N` | `1` | 每个 case 的独立运行次数 |
| `--timeout SECONDS` | `900` | 单个 case 的总超时，包含全部对话轮次 |
| `--stall-timeout SECONDS` | `180` | WorkBuddy 连续无任何输出时终止该 case，避免拖住整批 |
| `--no-native-session` | 不使用 | 不复制默认保留的 WorkBuddy 原生 session |
| `--output PATH` | `workbuddy_batch_runs` | 输出根目录 |
| `--run-id ID` | 自动生成 | 固定本次运行目录名 |

CLI 参数优先于配置文件。查看全部参数：

```bash
python -m WB_CLI run --help
```

## 补充说明

- 检查 WorkBuddy CLI 和版本，不调用模型：`python -m WB_CLI doctor`。
- macOS 会自动发现 WorkBuddy App 内置 CLI，并复用 `~/.workbuddy` 登录状态。
- 本地 Skill 使用 `--skill-path /absolute/path/to/research-report`。
- 每个 case 固定使用 `bypassPermissions`，无需额外权限参数。
- 终端会输出批次、case、轮次的启动和完成状态。
- case 达到 `--timeout` 时标记为 `timeout`；连续达到 `--stall-timeout` 无输出时标记为 `stalled`。两种情况都会终止对应进程组并保留已有 trace，不影响其他 case。
- 每个 case 完成后会立即写入自己的 `conversation.md`、`artifacts/` 和 `trace/`；整批根目录的 `results.md` 会在所有 case 结束后生成。
- 如果系统没有 `python` 命令，请改用 `python3`。

## 产物说明

产物按“人工总览 → 工具链路 → 原始事件”的顺序组织：

```text
<run-id>/
├── results.md
└── cases/
    └── <case-id>/
        ├── conversation.md
        ├── case.json
        ├── artifacts/
        │   ├── manifest.json
        │   └── <最终报告及其他产物>
        └── trace/
            ├── 1_operations.json
            ├── 2_events.jsonl
            ├── rounds/
            ├── native_session/
            └── workspace/
```

| 顺序 | 路径 | 代表什么 | 什么时候看 |
|---:|---|---|---|
| 1 | `results.md` | 整批运行总览，汇总所有 case 的状态、耗时、配置模型、实际观测模型、逐轮 token、最终输出和对话入口 | 批次全部完成后首先查看 |
| 2 | `cases/<case-id>/conversation.md` | 完整且易读的用户输入、WorkBuddy 回复、工具操作，以及整案/逐轮的实际模型和 token | 查看单个 case 的完整过程 |
| 3 | `cases/<case-id>/case.json` | 原始输入、每轮 prompt、模型、Skill、session 和实际生效配置 | 复现实验或核对输入 |
| 4 | `cases/<case-id>/artifacts/` | 最终报告等人工交付物，以及记录原始路径和哈希的 `manifest.json` | 直接打开或交付最终产物 |
| 5 | `cases/<case-id>/trace/1_operations.json` | 按时间整理的工具名称、输入、输出、状态和耗时 | 排查 Bash、Read、Write 等工具行为 |
| 6 | `cases/<case-id>/trace/2_events.jsonl` | 带轮次和耗时的完整 stdout/stderr 事件流 | 需要最完整链路时调试 |
| 7 | `cases/<case-id>/trace/rounds/` | 每轮的请求、结果、stdout、stderr、耗时和该轮产物 | 定位具体某一轮的问题 |
| 8 | `cases/<case-id>/trace/native_session/` | 默认保留的 WorkBuddy 原生 session JSONL | 排查 `--resume` 或事件缺失时对照原生记录 |
| 9 | `cases/<case-id>/trace/workspace/` | 该 case 的独立运行目录，包含输入资料、Skill 和 Agent 生成文件 | 深度复现或检查未被采集的文件 |

模型与 token 的查看位置：

- 首先看根目录 `results.md`：主表展示配置模型和实际观测模型，`Token 使用量`表展示每个 case、每轮的 input、output、cache creation 和 cache read。
- 查看单个 case 时打开 `conversation.md`：顶部展示整案配置模型、实际观测模型和最新一轮上报的四类 token，每轮末尾保留该轮明细。
- 原始模型字段位于 `trace/2_events.jsonl` 的 `event.model` 或 `event.message.model`。
- 原始 token 字段位于每轮最终 `result` 事件的 `event.usage`，也保留在 `trace/rounds/<round>/result.json` 的 `usage`。
- 多轮通过 `--resume` 继续时，CLI 返回的 usage 可能是会话累计值，不应直接将各轮相加。
