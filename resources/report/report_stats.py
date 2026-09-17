"""Deterministic report length, compatible with V1 judge_batch._report_stats.

Python standard library only. Reads UTF-8 Markdown; never modifies the report.
"""
import argparse
import hashlib
import json
import re
from pathlib import Path


def report_stats(text):
    visible = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    visible = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", visible)
    visible = re.sub(r"(?m)^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*$", "", visible)
    visible = re.sub(r"(?m)^\s{0,3}(?:#{1,6}|>|[-+*]|\d+[.)])\s+", "", visible)
    visible = re.sub(r"```[^\n]*|```", "", visible)
    visible = re.sub(r"[*_`~|]", "", visible)
    count = len(re.sub(r"\s+", "", visible))
    return {"visible_chars": count, "estimated_pages_at_1000_chars": round(count / 1000, 3),
            "counting_rule": "markdown-visible-v1: non-whitespace visible characters, including table text, letters, digits and punctuation; not tokens"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--max-chars", type=int, default=3000)
    args = parser.parse_args()
    if args.max_chars <= 0:
        parser.error("--max-chars must be positive")
    try:
        raw = args.report.read_bytes()
        result = report_stats(raw.decode("utf-8-sig"))
    except (OSError, UnicodeError) as exc:
        parser.exit(2, "REPORT_STATS_FAILED: " + str(exc) + "\n")
    result.update(report_path=str(args.report.resolve()), sha256=hashlib.sha256(raw).hexdigest(),
                  max_chars=args.max_chars, over_by=max(0, result["visible_chars"] - args.max_chars),
                  within_limit=result["visible_chars"] <= args.max_chars)
    print(json.dumps(result, ensure_ascii=True))


if __name__ == "__main__":
    main()
