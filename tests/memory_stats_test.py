import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "resources/report"))
import memory_stats as m


def plan(ids=("MR-1",), mode="additional"):
    return {"dimensions": [{"id": "d", "checks": [{"id": "C"}], "memoryRubricIds": list(ids)}],
            "memoryDecisions": [{"memoryId": i, "mode": mode} for i in ids]}


RESULT = {"dimensionId": "d", "checks": [{"id": "C", "status": "met"}]}


class MemoryStatsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "中文 空格记忆"
        self.root.mkdir()
        self.memory = self.root / "MEMORY.md"
        self.write_memory(["MR-1"])

    def write_memory(self, ids, revision=1, title="Active L2"):
        self.memory.write_text(f"revision: {revision}\n## {title}\n" + "".join(
            f"### {key} 要求\nscope: core\nsourceL1Ids: [L1-1]\n规则正文\n" for key in ids), encoding="utf-8")

    def stats(self):
        return m.load_stats(self.root)

    def test_limit_and_readonly(self):
        self.write_memory([f"MR-{i}" for i in range(1000)])
        self.assertEqual(m.check(self.root, self.memory)["marker"], "MEMORY_CAPACITY_OK")
        self.write_memory([f"MR-{i}" for i in range(1001)])
        self.assertEqual(m.check(self.root, self.memory)["excess"], 1)
        self.assertFalse((self.root / "memory-stats.json").exists())
        with self.assertRaises(ValueError):
            m.sync(self.root, {"revision": 1})

    def test_size_boundary_and_single_giant_rubric(self):
        self.assertEqual(m.MAX_MEMORY_BYTES, 524288)
        base = self.memory.read_bytes()
        self.memory.write_bytes(base + b"x" * (m.MAX_MEMORY_BYTES - len(base)))
        self.assertEqual(m.check(self.root, self.memory)["marker"], "MEMORY_CAPACITY_OK")
        with self.memory.open("ab") as stream:
            stream.write("中".encode("utf-8"))
        result = m.check(self.root, self.memory)
        self.assertEqual(result["marker"], "MEMORY_CAPACITY_EXCEEDED")
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["excess"], 0)
        self.assertEqual(result["excessBytes"], 3)
        with self.assertRaisesRegex(ValueError, "0.5 MiB"):
            m.sync(self.root, {"revision": 1})
        self.assertFalse((self.root / "memory-stats.json").exists())

    def test_size_preview_and_whole_file_metadata(self):
        proposal = self.root / "proposal.md"
        proposal.write_text("x" * m.MAX_MEMORY_BYTES + "\n" + self.memory.read_text(encoding="utf-8"), encoding="utf-8")
        result = m.check(self.root, proposal)
        self.assertEqual(result["marker"], "MEMORY_CAPACITY_EXCEEDED")
        self.assertEqual(result["bytes"], proposal.stat().st_size)
        self.assertEqual(m.check(self.root, self.memory)["marker"], "MEMORY_CAPACITY_OK")

    def test_size_cli_exit_code(self):
        with self.memory.open("ab") as stream:
            stream.write(b"x" * m.MAX_MEMORY_BYTES)
        result = subprocess.run([sys.executable, str(ROOT / "resources/report/memory_stats.py"),
                                 "--root", str(self.root), "check"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertGreater(json.loads(result.stdout)["excessBytes"], 0)

    def test_legacy_and_unknown_statistics(self):
        self.write_memory(["MR-1"], title="Active L2B Index")
        row = m.check(self.root, self.memory)["rubrics"][0]
        self.assertIsNone(row["updatedAt"])
        self.assertIsNone(row["useCount"])
        m.sync(self.root, {"revision": 1})
        self.assertIsNone(self.stats()["rubrics"]["MR-1"]["createdAt"])

    def test_missing_inventory_and_duplicate_ids_fail(self):
        for text in ("revision: 1", "## Active L2\n| MR-1 | 规则 |", "## Active L2\n### MR-1\n### MR-1"):
            self.memory.write_text(text, encoding="utf-8")
            with self.assertRaises(ValueError):
                m.active_ids(self.memory)

    def test_empty_inventory_and_reference_mentions(self):
        self.write_memory([])
        self.assertEqual(m.active_ids(self.memory), [])
        self.write_memory(["MR-1"])
        with self.memory.open("a", encoding="utf-8") as f:
            f.write("与 MR-2 有关\n## History\n### MR-3 已失效\n")
        self.assertEqual(m.active_ids(self.memory), ["MR-1"])

    def test_new_update_and_retry(self):
        m.sync(self.root, {"revision": 1, "newIds": ["MR-1"]})
        before = self.stats()["rubrics"]["MR-1"].copy()
        self.assertTrue(m.sync(self.root, {"revision": 1, "newIds": ["MR-1"]})["idempotent"])
        self.write_memory(["MR-1"], revision=2)
        m.sync(self.root, {"revision": 2})  # wording only
        self.assertEqual(before, self.stats()["rubrics"]["MR-1"])
        with self.assertRaises(ValueError):
            m.sync(self.root, {"revision": 2, "updatedIds": ["MR-1"]})

    def test_stale_revision_and_reused_id(self):
        with self.assertRaises(ValueError):
            m.sync(self.root, {"revision": 2})
        m.sync(self.root, {"revision": 1, "newIds": ["MR-1"]})
        self.write_memory(["MR-1"], revision=2)
        with self.assertRaises(ValueError):
            m.sync(self.root, {"revision": 2, "newIds": ["MR-1"]})

    def test_loop_dedup_and_memory_untouched(self):
        before = self.memory.read_bytes()
        self.assertEqual(m.record_use(self.root, "loop1", plan(), [RESULT])["added"], 1)
        self.assertEqual(m.record_use(self.root, "loop1", plan(), [RESULT, RESULT])["added"], 0)
        m.record_use(self.root, "loop2", plan(), [RESULT])
        self.assertEqual(self.stats()["rubrics"]["MR-1"]["useCount"], 2)
        self.assertEqual(self.memory.read_bytes(), before)
        self.assertFalse((self.root / "memory-history.md").exists())

    def test_ignored_and_incomplete_judge_rejected(self):
        with self.assertRaises(ValueError):
            m.record_use(self.root, "loop1", plan(mode="ignore"), [RESULT])
        for checks in ([], [{"id": "X", "status": "met"}], [{"id": "C", "status": "unknown"}]):
            with self.assertRaises(ValueError):
                m.record_use(self.root, "loop1", plan(), [{"dimensionId": "d", "checks": checks}])
        self.assertFalse((self.root / "memory-stats.json").exists())

    def test_base_only_does_not_write(self):
        self.assertEqual(m.record_use(self.root, "loop1", plan(ids=()), [RESULT])["added"], 0)
        self.assertFalse((self.root / "memory-stats.json").exists())

    def test_merge_counts_union_and_does_not_refresh_time(self):
        self.write_memory(["MR-1", "MR-2"])
        m.sync(self.root, {"revision": 1, "newIds": ["MR-1", "MR-2"]})
        stamp = self.stats()["rubrics"]["MR-1"]["updatedAt"]
        m.record_use(self.root, "loop1", plan(ids=("MR-1", "MR-2")), [RESULT])
        m.record_use(self.root, "loop2", plan(ids=("MR-2",)), [RESULT])
        self.write_memory(["MR-1"], revision=2)
        m.sync(self.root, {"revision": 2, "merges": {"MR-1": ["MR-2"]}})
        self.assertEqual(self.stats()["rubrics"]["MR-1"]["useCount"], 2)
        self.assertEqual(self.stats()["rubrics"]["MR-1"]["updatedAt"], stamp)
        m.record_use(self.root, "late-loop", plan(ids=("MR-2",)), [RESULT])
        self.assertEqual(self.stats()["rubrics"]["MR-1"]["useCount"], 3)
        m.record_use(self.root, "late-loop", plan(ids=("MR-1",)), [RESULT])
        self.assertEqual(self.stats()["rubrics"]["MR-1"]["useCount"], 3)

    def test_different_dimensions_in_same_loop_count_once(self):
        p = plan()
        p["dimensions"].append({"id": "second", "checks": [{"id": "S"}], "memoryRubricIds": ["MR-1"]})
        m.record_use(self.root, "loop1", p, [RESULT])
        m.record_use(self.root, "loop1", p, [{"dimensionId": "second", "checks": [{"id": "S", "status": "partial"}]}])
        self.assertEqual(self.stats()["rubrics"]["MR-1"]["useCount"], 1)

    def test_preview_checks_proposal_not_live_file(self):
        proposal = self.root / "proposal.md"
        proposal.write_text("## Active L2\n" + "".join(f"### MR-{i}\n规则\n" for i in range(1001)), encoding="utf-8")
        self.assertEqual(m.check(self.root, proposal)["excess"], 1)
        self.assertEqual(m.active_ids(self.memory), ["MR-1"])

    def test_substantive_update_refreshes_only_updated_timestamp(self):
        from unittest.mock import patch
        with patch.object(m, "now", return_value="2026-01-01T00:00:00+00:00"):
            m.sync(self.root, {"revision": 1, "newIds": ["MR-1"]})
        self.write_memory(["MR-1"], revision=2)
        with patch.object(m, "now", return_value="2026-02-01T00:00:00+00:00"):
            m.sync(self.root, {"revision": 2, "updatedIds": ["MR-1"]})
        entry = self.stats()["rubrics"]["MR-1"]
        self.assertTrue(entry["createdAt"].startswith("2026-01"))
        self.assertTrue(entry["updatedAt"].startswith("2026-02"))

    def test_bad_stats_not_overwritten_and_lock_not_removed(self):
        path = self.root / "memory-stats.json"
        path.write_text("broken", encoding="utf-8")
        with self.assertRaises(ValueError):
            m.record_use(self.root, "loop1", plan(), [RESULT])
        self.assertEqual(path.read_text(), "broken")
        lock = self.root / ".memory-stats.lock"
        lock.touch()
        with self.assertRaises(FileExistsError):
            m.record_use(self.root, "loop1", plan(), [RESULT])
        self.assertTrue(lock.exists())

    def test_concurrent_usage_no_lost_updates(self):
        def work(n):
            for _ in range(100):
                try:
                    return m.record_use(self.root, str(n), plan(), [RESULT])
                except FileExistsError:
                    import time
                    time.sleep(.005)
            raise AssertionError("lock not released")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(work, range(12)))
        self.assertEqual(self.stats()["rubrics"]["MR-1"]["useCount"], 12)

    def test_cli_unicode_path_and_exit_codes(self):
        script = ROOT / "resources/report/memory_stats.py"
        self.write_memory([f"MR-{i}" for i in range(1001)])
        result = subprocess.run([sys.executable, str(script), "--root", str(self.root), "check"], capture_output=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["count"], 1001)


if __name__ == "__main__":
    unittest.main()
