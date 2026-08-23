# OpenHarness Report Loop

Standalone Report Loop system extracted from OpenHarness. It repeatedly runs a report-generation Skill, judges the report against a rubric, rewrites the next revision plan, and adopts only eligible improvements.

Default stopping policy:

- overall score reaches `5.0`;
- two consecutive judged revisions are not adopted;
- elapsed time reaches one hour.

## Run locally

Requirements: Python 3.10+ and an authenticated WorkBuddy CLI. Optional PDF, Word and Excel material parsing uses the packages in `requirements.txt`.

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python app/server.py --host 127.0.0.1 --port 8098
```

Open <http://127.0.0.1:8098/report-loop/>.

Copy `.env.example` values into your process environment when WorkBuddy cannot be auto-discovered. The server does not automatically load `.env` and never persists credentials.

## Add input data

Create a local `data/v1/data.json` package as described in [`data/README.md`](data/README.md). Real data is ignored by Git.

## Repository boundary

This repository intentionally contains no Skill Loop runtime or historical session/output data. See [`docs/RUNTIME_FILES.md`](docs/RUNTIME_FILES.md).

## Verify

```powershell
python -m unittest discover -s tests -v
node --check app/report-loop-app.js
python -m compileall -q app harness
```
