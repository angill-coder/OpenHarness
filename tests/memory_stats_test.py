import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "resources/report"))
import memory_stats as m


class MemoryCapacityTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.memory = self.root / "MEMORY.md"

    def write(self, ids):
        self.memory.write_text("# 写作记忆\nrevision: 1\nenabled: true\nlastReflectionAt: null\n\n## Active L2\n" + "".join(
            f"### {key} 标准\n- scope: core\n- sourceL1Ids: [L1-1]\n有效标准。\n" for key in ids), encoding="utf-8")

    def test_count_boundary(self):
        self.write([f"M-20261009-{i:03d}-[0]" for i in range(1,1001)])
        self.assertEqual(m.check(self.root,self.memory)["marker"],"MEMORY_CAPACITY_OK")
        self.write([f"M-20261009-{i:03d}-[0]" for i in range(1,1002)])
        self.assertEqual(m.check(self.root,self.memory)["excess"],1)

    def test_size_boundary(self):
        self.write(["M-20261009-001-[0]"])
        self.assertEqual(m.MAX_MEMORY_BYTES,300000)
        content=self.memory.read_bytes()
        self.memory.write_bytes(content+b"x"*(300000-len(content)))
        self.assertEqual(m.check(self.root,self.memory)["marker"],"MEMORY_CAPACITY_OK")
        with self.memory.open("ab") as f:
            f.write("中".encode())
        self.assertEqual(m.check(self.root,self.memory)["excessBytes"],3)

    def test_legacy_and_new_ids_and_unknown_fields(self):
        self.write(["MR-001","M-20261009-01","M-20261009-002-[3]"])
        self.assertEqual(len(m.active_ids(self.memory)),3)
        self.assertEqual(m.validate(self.memory)["marker"],"MEMORY_FORMAT_OK")
        self.write(["MR-001"])
        with self.memory.open("a") as f:
            f.write("- updatedAt: unknown\n- useCount: unknown\n")
        self.assertEqual(m.validate(self.memory)["marker"],"MEMORY_FORMAT_OK")

    def test_bad_metadata(self):
        for key in ["M-20260230-001-[0]", "M-20261009-000-[0]",
                    "M-20261009-001-[-1]", "M-20261009-01-[0]",
                    "M-20261009-001-[01]", "M-20261009-001-[x]"]:
            self.write([key])
            self.assertTrue(m.validate(self.memory)["issues"])

    def test_separate_fields_rejected_for_new_ids(self):
        for field in ["updatedAt: 2026-10-09", "useCount: 3"]:
            self.write(["M-20261009-001-[3]"])
            with self.memory.open("a") as f:
                f.write("- " + field + "\n")
            self.assertTrue(m.validate(self.memory)["issues"])

    def test_usage_change_keeps_identity_and_duplicate_counts_rejected(self):
        a,b="M-20261009-001-[0]","M-20261009-001-[1]"
        self.assertEqual(m.rubric_key(a),m.rubric_key(b))
        self.write([a,b])
        self.assertTrue(m.validate(self.memory)["issues"])
        with self.assertRaises(ValueError):
            m.active_ids(self.memory)

    def test_template_example_is_valid(self):
        import re
        text=(ROOT/"skills/report-agent/references/memory-template.md").read_text()
        blocks=re.findall(r"```markdown\n(.*?)\n```",text,re.S)
        self.memory.write_text("\n".join(blocks)+"\n",encoding="utf-8")
        self.assertEqual(m.validate(self.memory)["marker"],"MEMORY_FORMAT_OK")

    def test_check_ignores_legacy_stats_and_never_writes(self):
        self.write(["MR-001"])
        (self.root/"memory-stats.json").write_text("old invalid json")
        before={p.name:p.read_bytes() for p in self.root.iterdir()}
        self.assertEqual(m.check(self.root,self.memory)["marker"],"MEMORY_CAPACITY_OK")
        self.assertEqual(before,{p.name:p.read_bytes() for p in self.root.iterdir()})

    def test_removed_write_commands(self):
        self.write(["M-20261009-001-[0]"])
        for command in ["sync","record-use"]:
            result=subprocess.run([sys.executable,str(ROOT/"resources/report/memory_stats.py"),"--root",str(self.root),command],capture_output=True)
            self.assertEqual(result.returncode,2)
        self.assertFalse((self.root/"memory-stats.json").exists())


if __name__ == "__main__":
    unittest.main()
