# Data packages

Report Loop discovers first-level folders named `v<number>*` that contain a `data.json`. Source material stays local and is ignored by Git.

Minimal layout:

```text
data/
  v1/
    data.json
    cases/example/structured_data.json
    cases/example/source.docx
```

`data.json` contains a `cases` array. Each case needs `case_id`, optional `topic`, and `input_files`. One input must resolve to `structured_data.json`; at least one other readable source file is required.

```json
{
  "cases": [
    {
      "case_id": "example",
      "topic": "Example report",
      "input_files": [
        {"source": "cases/example/structured_data.json", "target": "inputs/00_structured_data.json"},
        {"source": "cases/example/source.docx", "target": "inputs/source.docx"}
      ]
    }
  ]
}
```
