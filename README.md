# Report Memory Loop

This is an independent component branch for the report-writing memory and report-loop toolchain. It does not inherit the OpenHarness application tree from `main`.

## Components

- `research-report-memory/`: Research Report Memory V1-0820 source system.
- `report-loop/`: standalone Report Loop application, including its web UI, orchestration service, Judge/scoring logic, WorkBuddy runner, rubric, report Skill, tests, and runtime documentation.

The two components will remain independently testable before they are packaged as one user-facing plugin.
