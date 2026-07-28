# -*- coding: utf-8 -*-
"""
session.py — 会话编排层（模型 Judge 驱动的逐版推进）· 组合入口

把 harness 的自动 run_loop 拆成页面能一版一版驱动的步骤。为便于多人并行开发,
Session 已按职责物理拆成 mixin(见各文件顶注), 本文件把它们组合成对外唯一的 Session:

  session_core.py  · SessionCore  —— 状态/快照/restore/version/view       (owner: M3)
  session_eval.py  · SessionEval  —— evaluate/_apply_recorded/rubric/advance (owner: M3)
  session_label.py · SessionLabel —— 导入真实产物与模型 Judge 评分          (owner: M3)
  session_generation.py · SessionGeneration —— WB 报告批量幂等导入          (owner: M3)

对外契约不变: server.py 仍只用 session_mod.Session(...) 与 Session.restore(...)。
方法解析顺序 = Core -> Eval -> Label -> Generation(无重名方法, 互不遮蔽)。
状态全内存(单进程演示)。每个 session 一个 id。

  create()        —— 从需求描述生成 v0 skill + rubric, 建立会话       [core.__init__]
  import_data()   —— 导入数据集(dataset rows)                         [eval]
  evaluate()      —— 用当前版本 skill 跑分, 聚类失败, 组装可呈现结果   [eval]
  edit_rubric()   —— 改维度权重/阈值, 存为新的 rubric(重新评估)         [eval]
  advance()       —— optimizer 读失败 -> 提候选 -> dev gate -> 采纳    [eval]
  view()          —— 汇总当前会话状态给页面                             [core]
"""
from session_core import (   # noqa: F401  (回导出: 保持 session.DIMS 等旧引用可用)
    SessionCore,
    DIMS,
    DIM_ZH,
    _dims_from_rubric,
)
from session_eval import SessionEval    # noqa: E402
from session_label import SessionLabel  # noqa: E402
from session_generation import SessionGeneration  # noqa: E402


class Session(
    SessionCore,
    SessionEval,
    SessionLabel,
    SessionGeneration,
):
    """会话编排对外类。逻辑分布在三个 mixin, 本类只做组合, 不加新行为。"""
    pass
