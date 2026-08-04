import json
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from backfill_generation_traces import backfill_sessions, build_trace_index


class GenerationTraceBackfillTests(unittest.TestCase):
    def test_backfills_by_generation_and_case(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            run = root / "runs" / "gen-1"
            trace = run / "case-hash-a01" / "cases" / "case-a" / "trace"
            (trace / "rounds" / "00_task").mkdir(parents=True)
            (trace.parent / "conversation.md").write_text("conversation", encoding="utf-8")
            (trace / "1_operations.json").write_text(json.dumps([{
                "name": "Read", "status": "success", "round_index": 0,
                "duration_ms": 5, "input": {"path": "a"}, "result": "ok",
            }]), encoding="utf-8")
            (trace / "rounds" / "00_task" / "result.json").write_text(json.dumps({
                "status": "success", "duration_ms": 8, "final_output": "done",
            }), encoding="utf-8")
            (run / "generation_result.json").write_text(json.dumps({
                "generation_id": "gen-1",
                "cases": [{
                    "openharness_case_id": "case-a",
                    "attempts": [{
                        "attempt": 1, "status": "success", "wb_status": "success",
                        "wb_run_id": "case-hash-a01", "observed_models": ["model-a"],
                    }],
                }],
            }), encoding="utf-8")
            session = root / "sessions" / "session-a"
            session.mkdir(parents=True)
            outputs = session / "outputs.jsonl"
            outputs.write_text(json.dumps({
                "version": "v0", "case_id": "case-a", "report_text": "report",
                "generation_id": "gen-1",
            }) + "\n", encoding="utf-8")

            index = build_trace_index(root / "runs")
            stats = backfill_sessions(root / "sessions", index)
            row = json.loads(outputs.read_text(encoding="utf-8"))

            self.assertEqual(stats["matched"], 1)
            self.assertEqual(row["generation_trace"]["model"], "model-a")
            self.assertEqual(row["generation_trace"]["operations"][0]["name"], "Read")
            self.assertEqual(row["generation_trace"]["rounds"][0]["output"], "done")
            self.assertTrue(row["generation_trace"]["conversationAvailable"])


if __name__ == "__main__":
    unittest.main()
