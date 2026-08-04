# Realtime Dashboard data contract

The Dashboard resolves runtime artifacts relative to the OpenHarness repository root. It never stores or depends on an installation-specific absolute path.

## Authoritative generation artifacts

Skill content is loaded only from the immutable artifact produced by `compile_session_skill`:

```text
generation_runs/_session_skills/<session_id>/<skill_version>/<artifact_hash>/<skill_name>/
├── SKILL.md
└── references/
    └── instructions.md
```

A Skill artifact must match both `session_id` and `skill_version`. When a generation job record exists, its `skill_ref` selects the exact immutable directory; the path is relocated by its `generation_runs/` suffix after the repository is moved. Ambiguous or missing artifacts return 404 and the UI displays a missing state. `state.json` content is never used as a substitute.

Case trace content is loaded only from the generation identified by the matching `outputs.jsonl` row:

```text
generation_runs/<generation_id>/
├── generation_result.json
└── <wb_run_id>/cases/<case_id>/
    ├── conversation.md
    └── trace/
        ├── 1_operations.json
        └── rounds/<round>/result.json
```

The lookup requires an exact `generation_id`, `case_id`, session and Skill version association. Missing or conflicting artifacts return 404. Cached `generation_trace` fields in `outputs.jsonl`, Case questions, report text and Judge data are not used to synthesize a generation trace.

## Authoritative evaluation results

Dashboard scores, red-line counts, dimension results, rubric reasoning and Judge detail data are loaded only from `app/sessions/<session_id>/check_judgments.jsonl` (or the configured `OPENHARNESS_SESSIONS_DIR`). `judgments.jsonl` is not used as a fallback because it is not the completed Check evaluation contract.
## Repository hygiene

`generation_runs/`, `app/sessions/`, datasets and raw source packages are local runtime data and are excluded from the Dashboard PR. The repository should contain only the Dashboard code, adapters, schema documentation and synthetic tests.
## Loading and cache behavior

The initial page requests one compact Session Summary per experiment. It contains experiment metadata, compact version and Case descriptors, rubric definitions, Check scores, and invalidation markers. It deliberately excludes Skill text, report bodies, Judge reasoning, generation traces, full Metadata documents, and raw-package file listings.

Details are loaded independently on first use:

- SKILL.md and references/instructions.md when a Skill panel opens.
- Report output and the matching Check reasoning when a Case report opens.
- Generation trace only when the generation-trace panel opens.
- Complete Metadata and raw-package listings only when the data panel opens.

Session summaries are cached by file fingerprint. Polling reuses unchanged Sessions, pauses while the page is hidden, and never overlaps an existing refresh. Detail request caches include the Session revision so a changed experiment cannot reuse stale report or Metadata data.

outputs.jsonl uses an append-aware byte-offset index. The index seeks directly to the latest matching version and case_id row, safely rebuilds after truncation or replacement, and ignores an incomplete final line. All caches are derived in memory and are never committed.
## Portable runtime source map

Dashboard URLs never contain an installation directory. The server resolves three logical roots from the same runtime settings used by the evaluation platform:

- runtime:sessions: OPENHARNESS_SESSIONS_DIR, defaulting to repository-root/app/sessions.
- runtime:generation_runs: OPENHARNESS_WB_OUTPUT, defaulting to repository-root/generation_runs.
- runtime:data/v1, v2, v3: OPENHARNESS_WB_DATASET_V1, V2, V3, defaulting to repository-root/data/research-report/<version>/data.json.

Each Session Summary exposes runtime_sources with the following contract:

| Dashboard data | Authoritative platform output |
| --- | --- |
| Experiment group | runtime:sessions/<session_id>/state.json and meta.json |
| User, Data type, optimizer, Judge | runtime:sessions/<session_id>/state.json and meta.json |
| Skill versions | runtime:sessions/<session_id>/state.json |
| Cases for each Skill version | version and case identifiers from state.json, joined to Check and output rows |
| Evaluation and Judge Trace | runtime:sessions/<session_id>/check_judgments.jsonl only |
| Case SKILL.md and instruction.md | runtime:generation_runs/_session_skills/<session_id>/<version>/<artifact_hash>/<skill_name>/ |
| Case report | runtime:sessions/<session_id>/outputs.jsonl, matched by version and case_id |
| Case generation Trace | runtime:generation_runs/<generation_id>/<wb_run_id>/cases/<case_id>/trace/ |
| Raw package and Structured Data | the experiment-selected runtime:data/<data_version>/data.json and that Case's relative input_files sources |

Judge Trace has its own data endpoint and is never substituted for, or rendered inside, the report generation Trace. Data lookup never falls back from one Data version to another. Missing exact artifacts produce a local missing state rather than borrowing a similar file.

Absolute paths may exist inside a running process or a local generation-job record because the runner must open local files. They are not returned as Dashboard data references or stored in the browser snapshot. Stored generation skill references are relocated through their _session_skills suffix when an installation moves.