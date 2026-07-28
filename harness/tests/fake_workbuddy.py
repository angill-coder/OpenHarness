#!/usr/bin/env python3
"""契约测试用的最小 WorkBuddy stream-json 替身。"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _state_path() -> Path:
    value = os.environ.get("FAKE_WB_STATE_FILE")
    if not value:
        raise RuntimeError("缺少 FAKE_WB_STATE_FILE")
    return Path(value)


def _load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {"attempt": 0}
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(state: dict) -> None:
    _state_path().write_text(
        json.dumps(state),
        encoding="utf-8",
    )


def main() -> int:
    state = _load_state()
    resumed = "--resume" in sys.argv
    if not resumed:
        state["attempt"] = int(state.get("attempt", 0)) + 1
        _save_state(state)
    attempt = int(state.get("attempt", 1))
    succeed_on = int(os.environ.get("FAKE_WB_SUCCEED_ON_ATTEMPT", "1"))

    if os.environ.get("FAKE_WB_CLI_ERROR") == "1":
        print("fake cli error", file=sys.stderr)
        return 3

    if resumed and succeed_on > 0 and attempt >= succeed_on:
        report = Path("deliverables/report.md")
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            "# Fake Research Report\n\n"
            + ("这是用于验证 OpenHarness 报告产物、重试与追踪链路的正文。" * 20),
            encoding="utf-8",
        )

    if os.environ.get("FAKE_WB_STREAM_EVENT") == "1":
        print(
            json.dumps(
                {
                    "type": "stream_event",
                    "event": {
                        "type": "content_block_delta",
                        "delta": {
                            "type": "text_delta",
                            "text": "逐 token 内容",
                        },
                    },
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": f"fake attempt={attempt} resumed={resumed}",
                "model": "fake-model",
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 5,
                },
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
