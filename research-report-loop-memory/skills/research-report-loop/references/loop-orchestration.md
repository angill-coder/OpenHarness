# Report Loop Python orchestration contract

The App completes the three intake fields, writes V1 with the user-selected main model, then invokes the Python runner once. The App must not run Judge or Rewrite turns itself.

## Job schema

```json
{
  "schemaVersion": 2,
  "originalUserQuery": "the initial report request",
  "intakeContext": {
    "reportBackground": {"value": "confirmed background"},
    "materialHypothesis": {"value": "confirmed hypothesis"},
    "priorityMaterials": [{"path": "C:/workspace/interview.docx", "displayName": "interview.docx"}],
    "userInputEvidence": {
      "reportBackground": "exact user-authored text",
      "materialHypothesis": "exact user-authored text",
      "priorityMaterials": "exact user-authored text"
    }
  },
  "v1ArtifactPath": "C:/workspace/report-v1.md",
  "structuredDataPath": "C:/workspace/structured_data.json",
  "judgeProvider": "workbuddy",
  "hostModel": {"modelId": "the App-selected model", "effort": "optional"},
  "outputPath": "C:/workspace/report-final.md"
}
```

Run `scripts/run-python.cmd mcp/report_loop/runner.py --job <absolute-job-path>` on Windows, or the matching shell wrapper on macOS/Linux.
All three `userInputEvidence` values are mandatory exact excerpts from user-authored messages. System/App/tool-provided paths and attachment metadata are candidate materials only and cannot satisfy this gate. The runner rejects the Job before Judge if any evidence value is missing or empty.


The runner owns the loop. `judgeProvider` is WorkBuddy-only; the Codex CLI route has been removed. Every Judge round starts one isolated WorkBuddy CLI process per active Rubric dimension. It first uses locked `deepseek-v4-pro` with `medium` effort. A transport failure, empty response, or invalid Judge JSON activates a run-wide circuit breaker to the WorkBuddy App host model; a low score does not. Query and all three intake fields are included in every dimension call. Dimension processes, Judge rounds, and Rewriter never share context.

Rewrite is serial with Judge and uses one long-lived WorkBuddy CLI stream process for the entire run. It uses `hostModel.modelId` and optional effort, receives V1 context on its first turn, and thereafter retains writing, sanitized Judge feedback, and failed-attempt memory. Raw Judge output is never passed to it. Each rewrite starts from the best accepted report.

There is no version limit or Python iteration ledger. Stop when the best accepted score reaches 5, two consecutive candidates are rejected, or 60 minutes elapse. Infrastructure failures return the best judged version with `judge_unavailable` or `rewrite_unavailable`. The runner atomically copies the historical best report to `outputPath` and prints one final JSON object for the App.
