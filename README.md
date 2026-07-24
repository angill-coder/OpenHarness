# OpenHarness

**eval 驱动的 skill 自动优化平台**。当前重点产品 = 调研洞察汇报助手（`research_insight`，六维 rubric）。

> 📌 接手先读 **[`HANDOFF.md`](HANDOFF.md)**（完整交接：架构、资产、进度、致命坑）与 **[`MODULES.md`](MODULES.md)**（多人协作的模块划分 + 接口契约）。本 README 只给最短上手路径。

## 是什么

闭环：**Runner → 批量模型 Judge（LLM-as-judge）→ 失败聚类 → Optimizer（反思式改写）→ 版本化 Store → 回归看板**。

铁律：结构定质量上限（flow/subagent schema 由人设对，优化器不动结构）；rubric 与数据是杠杆、不能外包；真实调研会话必须全部 case 完成模型 Judge 才允许开优化器。

## 两大件

- **`harness/`** — 离线引擎（纯 stdlib、确定性）：`schemas` · `store` · `runner` · `judge` · `calibration` · `clustering` · `optimizer` · `loop` · `dashboard` · `backend`(Mock/ResearchMock/Recorded 三后端) · `artifacts/rubric*.json`(评测尺子)。
- **`app/`** — Web 平台（stdlib http + 单页 JS，批量生成与模型评测运行时）：
  - `server.py`(路由/鉴权入口) · `session.py`(组合入口) + `session_core.py`/`session_eval.py`/`session_label.py`/`session_generation.py`(会话编排) · `generation_jobs.py`(WB 后台任务) · `judge_batch.py`(批量模型 Judge) · `persistence.py`(落盘) · `generator.py`(需求→v0) · `auth.py`(iOA 鉴权)
  - `index.html`(单页 UI 结构+样式) + `app.js`(前端逻辑)

## 怎么跑

```bash
# 平台(默认 8080)。判分 LLM key 走 start_real.sh(gitignored, 含密钥, 勿提交)
cd app && source ./start_real.sh && python3 server.py --host 0.0.0.0 --port 8080
# 浏览器打开 → 导入 case → “真实运行 · WB CLI”批量生成并导入报告
# 然后点击“批量 Judge 全部 case”，全部完成后再生成下一版 Skill

# 离线自测闭环(无需 key)
cd harness && python3 run_demo_research.py   # 六维: dev overall 2.17→4.56, 采纳 15 版
cd harness && python3 run_demo.py            # 旧算数字产品, 防回归: 2.58→4.75
```

## 优化分层（L1/L2）

- **L1（翻 directive）**：优化器按失败聚类逐个打开 directive，dev gate 验证后采纳。research-run 演示这条。
- **L2（注入 few-shot 范例）**：L1 空间探尽后自动升级；对带 `needs_style_exemplar` 的 case，表达维由 few-shot 独占驱动（无范例=3 / 有=5）。见 `optimizer.propose` 的 L2 遍历与 `SkillArtifact.clone_with_fewshot`。
- L3（memory）尚未实现，见 `记忆与rubric分期设计.md`。

## 致命坑（详见 HANDOFF §5）

1. 改 `rubric_research.json` / `sessions/*/state.json` 后**必须重启 server** 才生效。
2. 改 `app/app.js` 后先 `node --check app/app.js`。
3. mock vs recorded 泾渭分明：Web UI 在模型 Judge 未覆盖全部 case 前不展示 mock 占位曲线，也不允许推进真实调研版本。
4. 当前一键 WB 运行使用固定 `skills/research-report`，已冻结 Session 版本/hash，但尚未把每版 `SkillArtifact` 渲染为可执行 Skill；因此只用于生成导入链路，不可作为真实版本 Gate。

## 注意

- ⚠️ 当前 `server.py` 的 **iOA 鉴权为本地测试临时关闭**（统一 `local` 账号）；对外/生产前须按 `_account()` 注释还原并打开 `import auth`。
- `app/start_real.sh` 已 gitignore（含真实密钥），切勿提交。
