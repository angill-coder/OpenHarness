# AGENTS.md — OpenHarness

## 🟥 START HERE（每次进入本项目，先做这件事）

**先完整读 `HANDOFF.md`（本目录根），再动手。** 它是最新交接文档，包含项目全貌、架构、已建资产、当前进度、致命坑和下一步。不读它直接动手大概率会踩坑（尤其"改 rubric/会话后必须重启 server""mock vs recorded 区别""改 JS 先语法检查"）。

## 速记（细节以 HANDOFF.md 为准）
- 项目 = eval 驱动的 skill 自动优化平台；当前重点产品 = 调研洞察汇报助手（`research_insight`，六维 rubric）。
- 与用户**一律用简体中文**。
- 环境：无 API key、Python 3.9 仅 stdlib、Node 22；harness 离线确定性，app 是 stdlib web 平台。
- 跑：`cd app && python3 server.py`（8765，无需 key）；离线自测 `cd harness && python3 run_demo_research.py`。
- **改 rubric_research.json 或 sessions/*/state.json 后要重启 server 才生效**；**改 app/index.html 的 JS 后先 `node --check`**。
