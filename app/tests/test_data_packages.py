from pathlib import Path
import sys
import tempfile
import unittest

APP = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from data_packages import list_data_packages, resolve_data_json


class DataPackagesTest(unittest.TestCase):
    def test_lists_only_valid_first_level_directories_in_natural_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ("v10", "v2_real", "v1"):
                package = root / name
                package.mkdir()
                (package / "data.json").write_text("{}", encoding="utf-8")
            (root / "notes").mkdir()
            (root / "v3_missing_json").mkdir()
            nested = root / "group" / "v4"
            nested.mkdir(parents=True)
            (nested / "data.json").write_text("{}", encoding="utf-8")

            self.assertEqual(
                ["v1", "v2_real", "v10"],
                list_data_packages(root),
            )

    def test_resolves_required_top_level_data_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "v2_real"
            package.mkdir()
            expected = package / "data.json"
            expected.write_text("{}", encoding="utf-8")

            self.assertEqual(
                expected.resolve(),
                resolve_data_json(root, "v2_real"),
            )

    def test_rejects_invalid_or_missing_package(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(ValueError):
                resolve_data_json(root, "../v1")
            with self.assertRaises(ValueError):
                resolve_data_json(root, "research_data")
            with self.assertRaises(FileNotFoundError):
                resolve_data_json(root, "v99")


if __name__ == "__main__":
    unittest.main()
