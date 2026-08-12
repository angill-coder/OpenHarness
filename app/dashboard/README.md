# Realtime Dashboard data contract

The Dashboard resolves runtime artifacts relative to the OpenHarness repository root. Browser responses use `runtime:` references and never depend on an installation-specific absolute path.

## Authoritative runtime map

| Dashboard module | Authoritative OpenHarness input |
| --- | --- |
| Experiment group and dimensions | `app/sessions/<session_id>/state.json` and `meta.json` |
| Skill versions | `generation_runs/_session_skills/<session_id>/<skill_version>/` |
| Cases for a Skill version | `generation_runs/<generation_id>/<wb_run_id>/cases/<case_id>/` |
| Evaluation and Judge Trace | Raw Checks come from `app/sessions/<session_id>/check_judgments.jsonl`; dimension scores, overall, red-line hits, and Gate status are derived by `harness/judge.py` in the Session Summary API |
| SKILL.md and instruction.md | `_session_skills/<session_id>/<version>/<artifact_hash>/<skill_name>/SKILL.md` and `references/instructions.md` |
| Case report | `<case_id>/artifacts/report.md` |
| Report-generation conversation | `<case_id>/conversation.md` |
| Three-level execution detail | `<case_id>/trace/rounds/<round>/request.json`, `result.json`, `trace/1_operations.json`, and `trace/2_events.jsonl` |
| Token use and step duration | `<case_id>/results.json`; current runner-compatible runs may expose the same authoritative `results.json` at `<wb_run_id>/results.json` |
| Raw source package | `data/<data_version>/<training-data-directory>/<case_id>/source/` |
| Complete Case Metadata / Structured Data | `data/<data_version>/<training-data-directory>/<case_id>/structured_data.json` |

The training-data directory name is discovered from the selected version's `data.json` `input_files` mapping. This supports the existing v1/v2/v3 package directory names without hard-coding a machine-specific absolute path.

## Exact-link rules

A generation job links `session_id`, `skill_version`, and `generation_id`. `generation_result.json` then links that generation to its WorkBuddy run and Case directories. The Dashboard rejects session, version, generation, or Case mismatches.

No similar document is used as a fallback:

- `state.json` text never substitutes for SKILL.md or instructions.
- `outputs.jsonl` never substitutes for `artifacts/report.md`.
- Trace JSON enriches the second- and third-level execution view but never substitutes for the User/Agent conversation, and Judge Trace is never mixed into report generation.
- `evidence_metadata.json` or a `.case.json` manifest never substitutes for `structured_data.json`.
- Data lookup never falls back from one Data version to another.

Missing exact artifacts return a missing state in the UI.

## Loading behavior

The initial Session Summary contains compact experiment descriptors, the generation-authoritative version-to-Case index, rubric definitions, and compact Check results. Raw Checks are the durable evaluation record. The Python API dynamically derives `scores`, `overall`, `redline_checks`, `hard_floor_failures`, and `case_failed_gate` through `harness/judge.py`; browser code never reimplements scoring. A recorded `rubric_sha256` mismatch produces `scoring_status=stale_rubric` and no score. Large documents load only when requested:

- Skill content when a Skill panel opens.
- `report.md` and Check reasoning when a Case report opens.
- `conversation.md` and `results.json` when the generation-chain panel opens.
- `structured_data.json` and source file listings when the data panel opens.

Runtime data (`generation_runs/`, `app/sessions/`, Data v1/v2/v3, and source packages) remains local and must not be included in the Dashboard PR.