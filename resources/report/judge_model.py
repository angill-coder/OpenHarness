"""Judge 模型的唯一数据源。

为什么需要它
------------
两类 Judge 的模型被刻意钉死，不继承宿主会话模型：报告分数必须跨轮、跨报告
可比，因为写作记忆靠稳定评分校准——记忆管理员判断某条反馈值不值得沉淀为
L2B，前提是分数可比。Judge 跟着用户当时选的模型漂，评分就成了移动靶。

但"钉死"不等于"抄五遍"。改造前模型字面量散落在 5 处：两个 Judge 的
frontmatter、契约文档两处、README 一处。平台下线或升级模型时漏改任何一处，
两个 Judge 就会用不同模型打分，而这种偏差不会报错，只会让分数悄悄不可比。

所以这里是唯一定义处，`scripts/check_judge_model.py` 校验其余位置与它一致，
`build.py` 在打包前调用该校验。平台升级只改这个文件。

注意：V4 是纯原生编排，Agent 读不到 Python 模块，模型仍必须写在各自的
frontmatter 里。因此这里采用"单一定义 + 构建期一致性校验"，而不是运行期注入。
"""

from __future__ import annotations

# 平台升级时只改这两行
JUDGE_MODEL = "gpt-5.6-sol"
JUDGE_EFFORT = "medium"

# 两类 Judge 都使用上面的模型；其余成员继承宿主模型。
JUDGE_AGENTS = (
    "report-resolution-judge",
    "report-dimension-judge",
)

# 派发与文档中必须与上面一致的位置，供 check_judge_model.py 校验。
CONSISTENCY_TARGETS = (
    "agents/report-resolution-judge.md",
    "agents/report-dimension-judge.md",
    "skills/research-report-loop/references/native-agent-contracts.md",
    "README.md",
)

__all__ = [
    "JUDGE_MODEL",
    "JUDGE_EFFORT",
    "JUDGE_AGENTS",
    "CONSISTENCY_TARGETS",
]
