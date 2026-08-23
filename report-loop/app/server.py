"""Standalone HTTP server for the Report Loop application."""

from __future__ import annotations

import argparse
import json
import mimetypes
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
HARNESS = ROOT / "harness"
for path in (HERE, HARNESS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import llm_client
from data_packages import list_data_package_options
from model_config import (
    DEFAULT_CODEX_REASONING_EFFORT,
    DEFAULT_EVALUATION_API_MODEL,
    DEFAULT_EVALUATION_CODEX_MODEL,
    DEFAULT_EVALUATION_WB_MODEL,
    SUPPORTED_API_MODELS,
    SUPPORTED_CODEX_MODELS,
    SUPPORTED_CODEX_REASONING_EFFORTS,
    SUPPORTED_WB_MODELS,
)
from report_loop_api import handle_get, handle_post, safe_call
from report_loop_service import ReportLoopService
from report_loop_settings import ReportLoopSettings
from skill_templates import skill_template_document
from workbuddy_batch.adapter import discover_command

DATA_ROOT = ROOT / "data"
SKILLS_ROOT = ROOT / "skills"
RUNS_ROOT = ROOT / "report_runs"
RUBRIC_PATH = ROOT / "harness" / "artifacts" / "v2_rubric_research.json"
MAX_JSON_BODY = 2 * 1024 * 1024
STATIC_FILES = {
    "/report-loop/": HERE / "report-loop.html",
    "/report-loop-app.js": HERE / "report-loop-app.js",
    "/report-pages.css": HERE / "report-pages.css",
}


def generation_configuration(settings: ReportLoopSettings) -> dict:
    try:
        discover_command(settings.command[0] if settings.command else None)
        wb_ready, wb_error = True, None
    except (FileNotFoundError, OSError, ValueError) as exc:
        wb_ready, wb_error = False, str(exc)
    codex = llm_client.codex_configuration()
    return {
        "model": settings.model,
        "models": list(settings.models),
        "wb_cli_ready": wb_ready,
        "wb_cli_error": wb_error,
        "judge_wb_model": DEFAULT_EVALUATION_WB_MODEL,
        "evaluation_models": list(SUPPORTED_WB_MODELS),
        "api_models": list(SUPPORTED_API_MODELS),
        "api_model_default": DEFAULT_EVALUATION_API_MODEL,
        "codex_models": list(SUPPORTED_CODEX_MODELS),
        "codex_model_default": DEFAULT_EVALUATION_CODEX_MODEL,
        "codex_reasoning_efforts": list(SUPPORTED_CODEX_REASONING_EFFORTS),
        "codex_reasoning_effort_default": DEFAULT_CODEX_REASONING_EFFORT,
        "codex_cli_ready": codex["ready"],
        "codex_cli_error": codex["error"],
    }


def build_service(settings: ReportLoopSettings | None = None) -> ReportLoopService:
    selected = settings or ReportLoopSettings.from_env()
    selected.validate()
    return ReportLoopService(
        RUNS_ROOT,
        DATA_ROOT,
        SKILLS_ROOT,
        RUBRIC_PATH,
        settings=selected,
    )


def make_handler(service: ReportLoopService):
    configuration = generation_configuration(service.settings)

    class Handler(BaseHTTPRequestHandler):
        server_version = "OpenHarnessReportLoop/1.0"

        def _json(self, status: int, payload: dict) -> None:
            content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def _static(self, path: Path) -> None:
            content = path.read_bytes()
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", content_type + ("; charset=utf-8" if content_type.startswith("text/") or content_type == "application/javascript" else ""))
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)

        def do_GET(self) -> None:
            request = urlparse(self.path)
            if request.path in ("/", "/report-loop"):
                self.send_response(302)
                self.send_header("Location", "/report-loop/")
                self.end_headers()
                return
            if request.path in STATIC_FILES:
                self._static(STATIC_FILES[request.path])
                return
            if request.path == "/api/data/options":
                self._json(200, {"datasets": list_data_package_options(DATA_ROOT)})
                return
            if request.path == "/api/skill/templates":
                self._json(200, skill_template_document(SKILLS_ROOT))
                return
            if request.path == "/api/generation/config":
                self._json(200, configuration)
                return
            result = safe_call(lambda: handle_get(service, request.path, request.query))
            if result is not None:
                self._json(*result)
                return
            self._json(404, {"error": "Not found"})

        def do_POST(self) -> None:
            request = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length") or "0")
            except ValueError:
                self._json(400, {"error": "Invalid Content-Length"})
                return
            if length > MAX_JSON_BODY:
                self._json(413, {"error": "JSON body is too large"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError("JSON body must be an object")
            except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
                self._json(400, {"error": str(exc)})
                return
            result = safe_call(lambda: handle_post(service, request.path, body, "local"))
            if result is not None:
                self._json(*result)
                return
            self._json(404, {"error": "Not found"})

        def log_message(self, fmt: str, *args) -> None:
            print("[report-loop] " + (fmt % args))

    return Handler


def main() -> None:
    parser = argparse.ArgumentParser(description="Run standalone OpenHarness Report Loop")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8098)
    args = parser.parse_args()
    service = build_service()
    server = ThreadingHTTPServer((args.host, args.port), make_handler(service))
    print("Report Loop: http://%s:%d/report-loop/" % (args.host, args.port))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
