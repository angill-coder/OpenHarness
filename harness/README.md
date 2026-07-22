# OpenHarness · Phase 0 MVP 优化闭环

eval 驱动的 skill 自动优化平台，Phase 0 骨架。**单平台、单产品（业务汇报助手）、跑通闭环**——目标只有一个：**证明这个 loop 能在真实 rubric 上把分数稳定推高**。

本目录是架构文档里那张系统全景图的**可运行实现**，纯 Python 标准库，无需 API/网络。

## 快速开始

```bash
# 1) 生成数据集 + 校准集(已生成, 改了再跑)
python3 ../data/report_assistant/build_dataset.py

# 2) 跑完整闭环 + 打印回归看板
python3 run_demo.py
```

预期看到：judge 校准一致率 ~0.97（≥0.85 门槛，PASS）→ 优化 v0→v5，dev overall **2.58→4.75**，test 同步上升（不过拟合），红线编造失败 **3→0**，失败模式 99→21 条消退，最后 reward-hacking 杠杆被 gate 拒绝。

## 为什么是"离线确定性"而不是真跑 LLM

本环境没有 `ANTHROPIC_API_KEY`、没有 anthropic SDK、没有 claude CLI。要**证明闭环成立**，需要一个可复现、可断言的演示，而不是一次性的、不确定的真实调用。

所以 `MockBackend` 不是"随机给分"：它模拟一个执行 skill 结构（取数→洞察→写作→验证）的 agent，**输出质量由 skill 的哪些 directive 被打开精确决定**，每个 directive 对应 rubric 的一个失分点。judge 再拿输出对照数据集的 `ground_truth_findings` 打分。于是"打开正确的 directive → 修好对应缺陷 → judge 分数真的上升"是 rubric 定义的**必然结果**，而非硬编码的脚本。这正是"**结构定上限、指令让输出逼近上限**"的可运行体现。

真实 Claude 后端（`ClaudeBackend`）已写好签名与选择逻辑，**有 key + sdk 时 `python3 run_demo.py --real` 自动启用**，闭环其余部分一字不改——证明后端抽象是真的、可迁移。

## 模块 ↔ 架构文档对应

| 模块 | 架构文档盒子 | 职责 |
|------|-------------|------|
| `schemas.py` | 3 个核心数据模型 | SkillArtifact / EvalRecord |
| `artifacts/skill_v0.json` | SKILL ARTIFACT (v0) | 人工设定的结构 + 可优化的 instructions/few_shots/memory |
| `artifacts/rubric.json` | Rubric | 4 维度 + 锚点 + gate + target（镜像 rubric 文档） |
| `backend.py` | (Runner 底层) | MockBackend（默认）/ ClaudeBackend（真实） |
| `runner.py` | RUNNER + JUDGE | 批量执行 → 打分 → EvalRecord |
| `judge.py` | JUDGE | 按 rubric 锚点确定性打分 + 理由 + 红线 flag |
| `calibration.py` | meta-eval 线 | judge↔人工一致率, 门槛 0.85 |
| `clustering.py` | FAILURE CLUSTERING | 低分 case → 结构化失败模式 |
| `optimizer.py` | OPTIMIZER | 失败报告 → L1/L2/L3 提议 + 记忆 + 防 hack |
| `store.py` | SKILL ARTIFACT STORE | 版本化 + 血缘 + 分数 |
| `dashboard.py` | REGRESSION DASHBOARD | 分数曲线 / 过拟合 / 一致率 / 失败消长 |
| `loop.py` | 优化闭环的一次迭代 | 编排 + gate + 收敛判定 |
| `run_demo.py` | — | 一键入口 |

## 优化器五机制（对应历史讨论）落点

1. **输入喂诊断不喂原始 trace** → `clustering.py` 产出 failure_report，`optimizer.propose` 只吃它。
2. **改法按代价分层 L1→L2→L3** → `skill_v0.json` 的 `directives` 是 L1 动作空间；MVP 只用 L1，结构（L4+）冻结。
3. **候选必须 dev 上验证** → `loop.py` 的 gate：目标维度↑ 且 其它维度不塌 且 不引入红线，才采纳。
4. **防 reward hacking** → ①校准门槛前置（不过不许开优化）②`keyword_emphasis` 在 `optimizer.FORBIDDEN`，且简洁性维度 + gate 会拒绝它（demo Step D 反证）③test held-out 防过拟合。
5. **记忆与收敛** → `history` 记录试过什么，不重复被否决的改动；无可提议→平台期→诊断是否触发结构优化信号。

## 验收标准（MVP 是否成立就看这两条）

- ✅ judge↔人工一致率 ≥ 0.85（本演示 0.973）
- ✅ skill 分数在 dev 上随版本上升、在 test 上不塌（2.58→4.75，test 同步）

两条同时成立，才算证明了 loop 成立。

## 产物

- `artifacts/versions.json` — 每一版 skill + 血缘 + dev/test 分数（含被拒版本）
- `dashboard.md` — 回归看板 markdown

## 换一个产品怎么用

1. 按结构设计文档 + rubric 文档，写该产品的 `skill_v0.json`（结构 + directives 动作空间）和 `rubric.json`。
2. 按 `data/report_assistant/` 的格式准备 `dataset.jsonl`（含 `ground_truth_findings` + 硬 case）和 `human_labels.jsonl`。
3. 若接真实平台，实现 `ClaudeBackend.run`（或新增 adapter），把 skill 拼成提示、回收结构化输出与 trace。
