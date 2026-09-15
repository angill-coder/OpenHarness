import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "resources/workbuddy/register_agents.py"
spec = importlib.util.spec_from_file_location("registration", SCRIPT)
registration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(registration)
PLUGIN_ID = "report-agent-v2@skillhub"


class RegistrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="注册测试 空格-")
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "skills/report-agent"
        self.config = self.base / "企业 WorkBuddy"
        self.root.mkdir(parents=True)
        self.config.mkdir()
        self.manifest = {"name": "report-agent-v2", "version": "0.3.0", "agents": ["./agents/writer.md"]}
        self.save(self.root / ".codebuddy-plugin/plugin.json", self.manifest)
        self.save(self.root / "_skillhub_meta.json", {"source": "skillhub", "version": "1.0.1"})
        (self.root / "agents").mkdir()
        (self.root / "agents/writer.md").write_text("---\nname: writer\n---\n", encoding="utf-8")
        self.registry_path = self.config / "plugins/installed_plugins.json"
        self.settings_path = self.config / "settings.json"
        self.save(self.registry_path, {"version": 2, "plugins": {"other@market": [{"scope": "user", "installPath": "/other"}]}})
        self.save(self.settings_path, {"enabledPlugins": {"other@market": True}, "unrelated": {"text": "保留"}})

    @staticmethod
    def save(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def load(path):
        return json.loads(path.read_text(encoding="utf-8-sig"))

    def snapshot(self):
        return {str(p.relative_to(self.base)): p.read_bytes() for p in self.base.rglob("*") if p.is_file()}

    def test_check_is_read_only_apply_preserves_other_entries_and_is_idempotent(self):
        before = self.snapshot()
        self.assertEqual(registration.register(self.root, self.config)["status"], "registration_required")
        self.assertEqual(self.snapshot(), before)
        result = registration.register(self.root, self.config, True)
        self.assertEqual(result["status"], "registered")
        self.assertEqual(len(result["backups"]), 2)
        for backup in result["backups"]:
            original = self.registry_path if Path(backup).name.startswith("installed_plugins") else self.settings_path
            self.assertEqual(Path(backup).read_bytes(), before[str(original.relative_to(self.base))])
        self.assertEqual(self.load(self.settings_path)["unrelated"], {"text": "保留"})
        self.assertTrue(self.load(self.settings_path)["enabledPlugins"]["other@market"])
        record = self.load(self.registry_path)["plugins"][PLUGIN_ID][0]
        self.assertEqual(record["installPath"], str(self.root.resolve()))
        self.assertEqual(record["version"], "0.3.0")  # Not the separate SkillHub listing version.
        after = self.snapshot()
        self.assertEqual(registration.register(self.root, self.config, True)["status"], "ready")
        self.assertEqual(self.snapshot(), after)
        self.assertFalse((self.config / "plugins/known_marketplaces.json").exists())
        self.assertFalse((self.config / "plugins/cache").exists())

    def test_update_path_and_version_preserves_scope_and_metadata(self):
        self.save(self.registry_path, {"version": 2, "plugins": {PLUGIN_ID: [
            {"scope": "user", "installPath": str(self.base / "old"), "version": "0.2.0", "installedAt": "old-time", "note": "keep"},
            {"scope": "project", "installPath": str(self.base / "project"), "projectPath": "/project"}]}})
        registration.register(self.root, self.config, True)
        records = self.load(self.registry_path)["plugins"][PLUGIN_ID]
        self.assertEqual(records[0]["installedAt"], "old-time")
        self.assertEqual(records[0]["note"], "keep")
        self.assertEqual(records[1]["scope"], "project")

    def test_invalid_json_or_schema_never_overwritten(self):
        for path, content in [(self.settings_path, b'{broken'), (self.registry_path, b'{"version":1,"plugins":{}}')]:
            original = path.read_bytes()
            path.write_bytes(content)
            before = self.snapshot()
            with self.assertRaises(ValueError):
                registration.register(self.root, self.config, True)
            self.assertEqual(self.snapshot(), before)
            path.write_bytes(original)

    def test_missing_files_are_initialized_and_utf8_bom_crlf_is_accepted(self):
        self.registry_path.unlink()
        self.settings_path.write_bytes(b'\xef\xbb\xbf{\r\n"keep": "yes"\r\n}')
        self.assertEqual(registration.register(self.root, self.config, True)["status"], "registered")
        self.assertEqual(self.load(self.settings_path)["keep"], "yes")

    def test_disabled_and_other_installation_do_not_get_overridden(self):
        self.save(self.settings_path, {"enabledPlugins": {PLUGIN_ID: False}})
        before = self.snapshot()
        self.assertEqual(registration.register(self.root, self.config, True)["status"], "disabled")
        self.assertEqual(self.snapshot(), before)
        other = self.base / "native-plugin"
        other.mkdir()
        self.save(self.registry_path, {"version": 2, "plugins": {"report-agent-v2@my-experts": [{"scope": "user", "installPath": str(other)}]}})
        self.save(self.settings_path, {"enabledPlugins": {"report-agent-v2@my-experts": True}})
        before = self.snapshot()
        self.assertEqual(registration.register(self.root, self.config, True)["status"], "other_installation")
        self.assertEqual(self.snapshot(), before)

    def test_native_same_package_and_managed_records(self):
        self.save(self.registry_path, {"version": 2, "plugins": {"report-agent-v2@my-experts": [{"scope": "user", "installPath": str(self.root)}]}})
        self.save(self.settings_path, {"enabledPlugins": {"report-agent-v2@my-experts": True}})
        before = self.snapshot()
        self.assertEqual(registration.register(self.root, self.config, True)["status"], "ready")
        self.assertEqual(self.snapshot(), before)
        self.save(self.registry_path, {"version": 2, "plugins": {PLUGIN_ID: [{"scope": "managed", "installPath": str(self.root)}]}})
        with self.assertRaisesRegex(ValueError, "Managed"):
            registration.register(self.root, self.config, True)

    def test_package_must_be_complete_and_actually_skillhub_installed(self):
        agent = self.root / "agents/writer.md"
        agent.unlink()
        with self.assertRaisesRegex(ValueError, "Agent file"):
            registration.register(self.root, self.config, True)
        agent.write_text("writer")
        (self.root / "_skillhub_meta.json").unlink()
        with self.assertRaisesRegex(ValueError, "Not a SkillHub"):
            registration.register(self.root, self.config, True)

    def test_concurrent_config_change_is_not_overwritten(self):
        copy2 = shutil.copy2
        def change_settings(source, target):
            copied = copy2(source, target)
            if source == self.settings_path.resolve():
                self.save(source, {"changedByWorkBuddy": True})
            return copied
        with patch.object(registration.shutil, "copy2", side_effect=change_settings):
            with self.assertRaisesRegex(ValueError, "concurrently"):
                registration.register(self.root, self.config, True)
        self.assertEqual(self.load(self.settings_path), {"changedByWorkBuddy": True})
        self.assertFalse(list(self.config.rglob(".report-agent-*")))

    def test_cli_discovers_own_package_and_respects_custom_config(self):
        script = self.root / "resources/workbuddy/register_agents.py"
        script.parent.mkdir(parents=True)
        shutil.copy2(SCRIPT, script)
        env = {**os.environ, "WORKBUDDY_CONFIG_DIR": str(self.config), "CODEBUDDY_CONFIG_DIR": str(self.base / "wrong")}
        for args, status in [([], "registration_required"), (["--apply"], "registered"), (["--apply"], "ready")]:
            result = subprocess.run([sys.executable, "-B", str(script), *args], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], status)
        self.assertFalse((self.base / "wrong").exists())
        env.pop("WORKBUDDY_CONFIG_DIR")
        env["CODEBUDDY_CONFIG_DIR"] = str(self.config)
        result = subprocess.run([sys.executable, "-B", str(script)], env=env, capture_output=True, text=True)
        self.assertEqual(json.loads(result.stdout)["status"], "ready")


if __name__ == "__main__":
    unittest.main()
