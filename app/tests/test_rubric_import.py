# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import json
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


APP = Path(__file__).resolve().parents[1]
HARNESS = APP.parent / "harness"
for path in (str(APP), str(HARNESS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import generator  # noqa: E402
import persistence as persist  # noqa: E402
import server as server_mod  # noqa: E402
from session import Session  # noqa: E402


class RubricImportTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_base = persist._BASE
        persist._BASE = str(Path(self.tmp.name) / "sessions")
        self.session = Session(
            "rubric-import-test",
            "生成调研洞察报告",
            "research_insight",
        )

    def tearDown(self):
        persist._BASE = self.old_base
        self.tmp.cleanup()

    def imported(self, version="v2.2-local"):
        rubric = copy.deepcopy(generator._build_rubric_research())
        rubric["version"] = version
        return rubric

    def test_import_is_session_local_and_survives_restore(self):
        default_path = HARNESS / "artifacts" / "rubric_research.json"
        before = default_path.read_bytes()

        state = self.session.import_rubric(
            self.imported(),
            filename="../rubric_v2.2.json",
        )

        self.assertEqual(state["rubric"]["version"], "v2.2-local")
        self.assertEqual(state["rubric_source"], {
            "kind": "imported",
            "filename": "rubric_v2.2.json",
            "version": "v2.2-local",
        })
        self.assertEqual(default_path.read_bytes(), before)

        other = Session(
            "rubric-default-stays-current",
            "生成另一份调研洞察报告",
            "research_insight",
        )
        self.assertEqual(other.rubric_source["kind"], "default")
        self.assertEqual(
            other.rubric["version"],
            generator._build_rubric_research()["version"],
        )

        restored = Session.restore(persist.load_snapshot(self.session.id))
        restored_state = restored.view()
        self.assertEqual(restored_state["rubric"]["version"], "v2.2-local")
        self.assertEqual(
            restored_state["rubric_source"],
            state["rubric_source"],
        )

    def test_invalid_import_is_atomic(self):
        original = copy.deepcopy(self.session.rubric)
        invalid = self.imported()
        invalid["dimensions"][1]["checks"][0]["id"] = (
            invalid["dimensions"][0]["checks"][0]["id"]
        )

        with self.assertRaisesRegex(ValueError, "check id 重复"):
            self.session.import_rubric(invalid, "invalid.json")

        self.assertEqual(self.session.rubric, original)
        self.assertEqual(self.session.rubric_source["kind"], "default")

    def test_product_mismatch_is_allowed_and_switches_backend(self):
        imported = copy.deepcopy(generator._build_rubric("report-assistant", {
            "data_accuracy": 0.4,
            "completeness": 0.25,
            "insight": 0.2,
            "conciseness": 0.15,
        }))
        imported["version"] = "report-rubric-local"

        state = self.session.import_rubric(imported, "rubric-report.json")

        self.assertEqual(self.session.product_id, "research_insight")
        self.assertEqual(state["rubric"]["product"], "report-assistant")
        self.assertEqual(state["backend"], "mock")
        self.assertEqual(
            state["dims"],
            [d["name"] for d in imported["dimensions"]],
        )

    def test_custom_skill_session_accepts_its_research_rubric_type(self):
        custom = Session(
            "custom-skill-rubric-import",
            "根据访谈素材生成调研洞察报告",
            "custom-skill",
        )
        self.assertEqual(custom.product_id, "custom-skill")
        self.assertEqual(custom.rubric["product"], "research_insight")

        state = custom.import_rubric(
            self.imported("v2.3-custom"),
            "rubric-v2.3.json",
        )

        self.assertEqual(state["rubric"]["version"], "v2.3-custom")
        self.assertEqual(state["rubric"]["product"], "research_insight")

    def test_legacy_checks_without_optimizer_metadata_can_import(self):
        legacy = self.imported("v1-legacy")
        for dimension in legacy["dimensions"]:
            for check in dimension.get("checks", []):
                check.pop("optimizer", None)
        legacy["dimensions"][0]["checks"].append({
            "id": "T-LEGACY",
            "label": "历史检查点",
        })

        state = self.session.import_rubric(legacy, "rubric-v1.json")

        imported_check = state["rubric"]["dimensions"][0]["checks"][-1]
        self.assertIsNone(imported_check["optimizer"])

    def test_import_invalidates_current_judge_checks(self):
        version = self.session._current()["version"]
        self.session.judge_checks[version] = {
            "case-a": {"checks": {"T1": 1.0}}
        }

        self.session.import_rubric(self.imported(), "new.json")

        self.assertEqual(self.session.judge_checks[version], {})


class RubricImportHTTPTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old_base = persist._BASE
        persist._BASE = str(Path(self.tmp.name) / "sessions")
        self.original_sessions = dict(server_mod.SESSIONS)
        self.original_service = server_mod.GENERATION_SERVICE
        server_mod.SESSIONS.clear()
        server_mod.GENERATION_SERVICE = None
        self.session = Session(
            "rubric-import-http",
            "生成调研洞察报告",
            "research_insight",
        )
        server_mod.SESSIONS[self.session.id] = self.session
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever,
            daemon=True,
        )
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=3)
        server_mod.SESSIONS.clear()
        server_mod.SESSIONS.update(self.original_sessions)
        server_mod.GENERATION_SERVICE = self.original_service
        persist._BASE = self.old_base
        self.tmp.cleanup()

    def post(self, body):
        request = urllib.request.Request(
            "http://127.0.0.1:%d/api/rubric/import"
            % self.httpd.server_port,
            data=json.dumps(body).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))

    def test_endpoint_imports_full_rubric(self):
        rubric = copy.deepcopy(generator._build_rubric_research())
        rubric["version"] = "v2.1-http"

        status, payload = self.post({
            "id": self.session.id,
            "filename": "rubric-v2.1.json",
            "rubric": rubric,
        })

        self.assertEqual(status, 200)
        self.assertEqual(payload["rubric"]["version"], "v2.1-http")
        self.assertEqual(
            payload["rubric_source"]["filename"],
            "rubric-v2.1.json",
        )

    def test_endpoint_returns_400_for_invalid_rubric(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            self.post({
                "id": self.session.id,
                "filename": "broken.json",
                "rubric": {"version": "broken"},
            })
        self.assertEqual(ctx.exception.code, 400)
