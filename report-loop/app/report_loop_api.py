"""HTTP-neutral route adapter for the independent Report Loop service."""
from __future__ import annotations

from urllib.parse import parse_qs

from report_loop_service import ReportLoopError


def handle_get(service, path: str, query: str):
    if path == "/api/report-loop/runs":
        return 200, {"runs": service.list_runs()}
    if path == "/api/report-loop/run":
        run_id = (parse_qs(query).get("id") or [""])[0]
        if not run_id:
            return 400, {"error": "缺少 Report Run id"}
        return 200, {"run": service.view(run_id)}
    if path == "/api/report-loop/job":
        job_id = (parse_qs(query).get("id") or [""])[0]
        if not job_id:
            return 400, {"error": "缺少 Report Loop job id"}
        return 200, {"job": service.job(job_id)}
    if path == "/api/report-loop/generation-chain":
        run_id = (parse_qs(query).get("id") or [""])[0]
        version = (parse_qs(query).get("version") or [""])[0]
        if not run_id or not version:
            return 400, {"error": "缺少 Report Run id 或 version"}
        return 200, service.generation_chain(run_id, version)
    return None


def handle_post(service, path: str, body: dict, account: str):
    if path == "/api/report-loop/runs":
        stop = body.get("stop_policy") or {}
        run = service.create_run(
            data_id=str(body.get("data_id") or "").strip(),
            case_id=str(body.get("case_id") or "").strip(),
            skill_template_id=str(body.get("skill_template_id") or "").strip(),
            requirement=str(body.get("requirement") or ""),
            creator=account,
            overall_target=stop.get("overall_target", 5.0),
            max_no_improvement=stop.get("max_no_improvement", 2),
            max_elapsed_seconds=stop.get("max_elapsed_seconds", 3600),
            stop_on_unrepairable_failure=stop.get(
                "stop_on_unrepairable_failure", False
            ),
        )
        return 201, {"run": run}
    if path == "/api/report-loop/generate":
        job = service.start_generate(
            str(body.get("id") or ""),
            body.get("model"),
            body.get("generation_backend"),
            body.get("api_model"),
            body.get("reasoning_effort"),
        )
        return 202, {"job": job}
    if path == "/api/report-loop/optimize":
        job = service.start_optimize(
            str(body.get("id") or ""), body.get("model")
        )
        return 202, {"job": job}
    if path == "/api/report-loop/judge":
        job = service.start_judge(
            str(body.get("id") or ""),
            body.get("llm_backend"),
            body.get("llm_model"),
            body.get("llm_reasoning_effort"),
        )
        return 202, {"job": job}
    return None


def safe_call(callback):
    try:
        return callback()
    except (ReportLoopError, FileNotFoundError, ValueError) as exc:
        return 400, {"error": str(exc)}
    except OSError as exc:
        return 500, {"error": str(exc)}



