# Report Memory Loop

This is an independent component branch for the report-writing memory and report-loop toolchain. It does not inherit the OpenHarness application tree from `main`.

## Components

- `research-report-memory/`: Research Report Memory V1-0820 source system.
- `report-loop/`: standalone Report Loop application, including its web UI, orchestration service, Judge/scoring logic, WorkBuddy runner, rubric, report Skill, tests, and runtime documentation.
- `research-report-loop-memory/`: integrated WorkBuddy plugin development package. It combines host-driven report writing, isolated parallel Judge loops, L0/L1 capture, and Git-backed L2B Memory Rubrics. Release archives and installed dependencies are intentionally excluded from this branch.

The standalone components remain independently testable alongside the integrated plugin.
