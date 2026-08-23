# Report Loop runtime closure

This repository is a deliberate extraction from OpenHarness. Files are kept only when they participate in a Report Loop session or document its setup.

## Request path

```text
app/report-loop.html + app/report-loop-app.js
  -> app/server.py
  -> app/report_loop_api.py
  -> app/report_loop_service.py
  -> Judge: app/judge_batch.py + app/judge_prompt.py + app/report_scoring.py
  -> Runner: harness/workbuddy_runner.py + harness/workbuddy_batch/
  -> persistence: app/report_loop_store.py + report_runs/ (runtime only)
```

## Included groups

- `app/report_loop_*`: state machine, store, API, settings, gate and helpers.
- `app/data_packages.py`, `app/skill_templates.py`: local input discovery.
- `app/llm_client.py`, `app/model_config.py`: WorkBuddy/API/Codex model calls.
- `harness/external_run_models.py`, `report_artifact.py`, `report_source.py`: runner contracts and report validation.
- `harness/workbuddy_batch/`: isolated WorkBuddy process adapter.
- `skills/research-report/`: the report-generation Skill frozen per run.
- `harness/artifacts/v2_rubric_research.json`: Judge rubric.

## Explicitly excluded

- Skill Loop sessions, optimizer, compiler and directive registry.
- Legacy `harness/loop.py`, `clustering.py`, `judge.py`, `runner.py`.
- Dashboard pages and the mixed legacy `app/server.py`.
- Existing `report_runs`, generation outputs, logs and `.runtime` state.
- Real data packages, credentials, WorkBuddy home and model configuration.

The few reusable functions formerly located in legacy modules were moved into `report_failure.py`, `report_scoring.py`, `report_loop_gate.py`, and `report_loop_utils.py`; no compatibility import remains.
