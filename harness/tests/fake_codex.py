#!/usr/bin/env python3
"""契约测试用的最小 Codex ``exec --json`` 替身。"""

from __future__ import annotations

import json
from pathlib import Path


def main() -> int:
    report = Path("deliverables/report.md")
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(
        "# Fake Codex Research Report\n\n"
        + ("这是用于验证 Codex Runner 工作区、报告验收和导入契约的正文。" * 30),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "type": "thread.started",
                "thread_id": "fake-codex-thread",
            }
        ),
        flush=True,
    )
    print(
        json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "type": "agent_message",
                    "text": "fake codex completed",
                },
            }
        ),
        flush=True,
    )
    print(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {"input_tokens": 100, "output_tokens": 20},
            }
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
