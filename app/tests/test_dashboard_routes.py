from __future__ import annotations

import sys
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP_DIR))

import server as server_mod  # noqa: E402


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class DashboardRouteContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = "http://127.0.0.1:%d" % cls.httpd.server_port

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)

    def test_dashboard_without_trailing_slash_redirects_and_keeps_query(self):
        opener = urllib.request.build_opener(_NoRedirect)

        with self.assertRaises(urllib.error.HTTPError) as ctx:
            opener.open(self.base_url + "/dashboard?ui=2", timeout=3)

        self.assertEqual(308, ctx.exception.code)
        self.assertEqual("/dashboard/?ui=2", ctx.exception.headers["Location"])
        ctx.exception.close()

    def test_dashboard_canonical_path_and_relative_asset_are_available(self):
        for path in (
            "/dashboard/?ui=2",
            "/dashboard/local-realtime-loader.js",
        ):
            with self.subTest(path=path):
                with urllib.request.urlopen(self.base_url + path, timeout=3) as response:
                    self.assertEqual(200, response.status)


if __name__ == "__main__":
    unittest.main()
