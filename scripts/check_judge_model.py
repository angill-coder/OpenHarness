#!/usr/bin/env python3
"""校验 Judge 模型在所有位置与唯一数据源一致。

V4 是纯原生编排，Agent 读不到 Python，模型必须写在各自 frontmatter 里。
本脚本在打包前比对所有出现位置，任何一处与 `judge_model.py` 不符即失败——
防的是"升级模型时漏改一处，两个 Judge 用不同模型打分"这种静默偏差。

用法：python3 scripts/check_judge_model.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "resources" / "report"))

from judge_model import (  # noqa: E402
    CONSISTENCY_TARGETS,
    JUDGE_AGENTS,
    JUDGE_EFFORT,
    JUDGE_MODEL,
)

_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---", re.DOTALL)
# 任何形如 gpt-… / claude-… 的模型 ID；用于发现未登记的字面量
_MODEL_LIKE = re.compile(r"\b(?:gpt|claude|deepseek|gemini|qwen)[-a-z0-9.]*\b", re.I)


def check() -> list[str]:
    errors: list[str] = []

    # 1. 两个 Judge 的 frontmatter 必须精确声明模型与 effort
    for agent in JUDGE_AGENTS:
        path = ROOT / "agents" / f"{agent}.md"
        if not path.is_file():
            errors.append(f"缺少 Judge agent: {path.relative_to(ROOT)}")
            continue
        match = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
        if not match:
            errors.append(f"{agent}: 无 frontmatter")
            continue
        frontmatter = match.group(1)
        model = re.search(r"^model:\s*(\S+)\s*$", frontmatter, re.M)
        effort = re.search(r"^effort:\s*(\S+)\s*$", frontmatter, re.M)
        if not model:
            errors.append(f"{agent}: frontmatter 缺 model")
        elif model.group(1) != JUDGE_MODEL:
            errors.append(
                f"{agent}: model={model.group(1)}，应为 {JUDGE_MODEL}"
            )
        if not effort:
            errors.append(f"{agent}: frontmatter 缺 effort")
        elif effort.group(1) != JUDGE_EFFORT:
            errors.append(
                f"{agent}: effort={effort.group(1)}，应为 {JUDGE_EFFORT}"
            )

    # 2. 非 Judge 成员不得钉死模型——它们应继承宿主模型
    for path in sorted((ROOT / "agents").glob("*.md")):
        if path.stem in JUDGE_AGENTS:
            continue
        match = _FRONTMATTER.match(path.read_text(encoding="utf-8"))
        if match and re.search(r"^model:", match.group(1), re.M):
            errors.append(f"{path.stem}: 非 Judge 成员不应声明 model")

    # 3. 文档与契约里出现的模型 ID 必须与数据源一致
    for relative in CONSISTENCY_TARGETS:
        path = ROOT / relative
        if not path.is_file():
            errors.append(f"缺少一致性校验目标: {relative}")
            continue
        for found in set(_MODEL_LIKE.findall(path.read_text(encoding="utf-8"))):
            if found.lower() != JUDGE_MODEL.lower():
                errors.append(
                    f"{relative}: 出现未登记的模型 ID {found}，应为 {JUDGE_MODEL}"
                )

    # 4. 未登记的文件里不得出现模型字面量（漏改的主要来源）
    registered = set(CONSISTENCY_TARGETS)
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or path.suffix not in {".md", ".mjs", ".py", ".json"}:
            continue
        relative = path.relative_to(ROOT)
        parts = set(relative.parts)
        # scripts/ 自身含匹配用的正则，tests/ 断言的就是这些值，都不算漏改点
        if parts & {".git", "dist", "node_modules", "__pycache__", "tests", "scripts"}:
            continue
        if str(relative) in registered or relative.name == "judge_model.py":
            continue
        if _MODEL_LIKE.search(path.read_text(encoding="utf-8", errors="replace")):
            errors.append(
                f"{relative}: 出现模型字面量但未登记到 CONSISTENCY_TARGETS"
            )
    return errors


def main() -> int:
    errors = check()
    if errors:
        print("Judge 模型一致性校验失败：")
        for error in errors:
            print(f"  - {error}")
        return 1
    print(f"Judge 模型一致性校验通过：{JUDGE_MODEL} / {JUDGE_EFFORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
