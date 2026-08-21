"""Repository-local Data package discovery and resolution."""

from __future__ import annotations

import json
import re
from pathlib import Path


_DATA_NAME = re.compile(r"^v(\d+)", re.IGNORECASE)


def list_data_packages(data_root: Path) -> list[str]:
    """List valid first-level ``v<number>*`` directories naturally."""
    root = data_root.resolve()
    if not root.is_dir():
        return []
    packages = [
        path
        for path in root.iterdir()
        if path.is_dir()
        and _DATA_NAME.match(path.name)
        and (path / "data.json").is_file()
    ]
    packages.sort(
        key=lambda path: (
            int(_DATA_NAME.match(path.name).group(1)),
            path.name.lower(),
        )
    )
    return [path.name for path in packages]


def list_data_package_options(data_root: Path) -> list[dict]:
    """Return Data package names and their Case topic labels."""
    options = []
    for data_id in list_data_packages(data_root):
        document = json.loads(
            resolve_data_json(data_root, data_id).read_text(encoding="utf-8")
        )
        rows = document.get("cases", []) if isinstance(document, dict) else document
        cases = []
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            case_id = str(row.get("case_id") or "").strip()
            input_data = row.get("input") if isinstance(row.get("input"), dict) else {}
            topic = str(row.get("topic") or input_data.get("topic") or "").strip()
            if case_id:
                cases.append({"case_id": case_id, "topic": topic})
        options.append({"name": data_id, "cases": cases})
    return options

def select_case_rows(rows: list[dict], case_ids: object) -> list[dict]:
    """Select requested Cases in caller order, rejecting unknown IDs."""
    if case_ids is None:
        return rows
    if not isinstance(case_ids, list) or not case_ids:
        raise ValueError("case_ids must be a non-empty list")
    requested = list(dict.fromkeys(str(item).strip() for item in case_ids))
    if not all(requested):
        raise ValueError("case_ids cannot contain empty values")
    indexed = {str(row.get("case_id") or ""): row for row in rows}
    missing = [case_id for case_id in requested if case_id not in indexed]
    if missing:
        raise ValueError("Data package has no Case: %s" % ", ".join(missing))
    return [indexed[case_id] for case_id in requested]


def _resolve_package_source(package_root: Path, value: object) -> Path:
    source = Path(str(value)).expanduser()
    target = (
        source.resolve()
        if source.is_absolute()
        else (package_root / source).resolve()
    )
    target.relative_to(package_root)
    return target


def load_data_package_case(data_root: Path, data_id: str, case_id: str) -> dict:
    """Load one Case's structured data and original source-file manifest."""
    dataset_path = resolve_data_json(data_root, data_id)
    package_root = dataset_path.parent.resolve()
    document = json.loads(dataset_path.read_text(encoding="utf-8"))
    rows = document.get("cases", []) if isinstance(document, dict) else document
    case = next(
        (
            row for row in rows if isinstance(row, dict)
            and str(row.get("case_id") or "") == str(case_id)
        ),
        None,
    )
    if case is None:
        raise FileNotFoundError("Data package has no Case: %s" % case_id)

    structured_path = None
    files = []
    seen = set()
    for spec in case.get("input_files") or []:
        if not isinstance(spec, dict) or not spec.get("source"):
            continue
        source = _resolve_package_source(package_root, spec["source"])
        target = str(spec.get("target") or "").replace("\\", "/").lower()
        if (
            target.endswith("/00_structured_data.json")
            or source.name.lower() == "structured_data.json"
        ):
            structured_path = source
            continue
        candidates = [source]
        if source.is_dir():
            candidates = sorted(path for path in source.rglob("*") if path.is_file())
        for path in candidates:
            if not path.is_file():
                continue
            relative = path.relative_to(package_root).as_posix()
            if relative in seen:
                continue
            seen.add(relative)
            files.append({
                "name": path.name,
                "path": relative,
                "size": path.stat().st_size,
            })

    if structured_path is None or not structured_path.is_file():
        raise FileNotFoundError("Case has no readable structured_data.json")
    if not files:
        raise FileNotFoundError("Case has no readable original source files")
    input_data = case.get("input") if isinstance(case.get("input"), dict) else {}
    return {
        "data_id": data_id,
        "case_id": str(case.get("case_id")),
        "topic": str(case.get("topic") or input_data.get("topic") or "").strip(),
        "files": files,
        "structured_data": json.loads(structured_path.read_text(encoding="utf-8")),
        "structured_source": structured_path.relative_to(package_root).as_posix(),
    }


def resolve_data_json(data_root: Path, data_id: str) -> Path:
    """Resolve one package ID to its required top-level ``data.json``."""
    if (
        not isinstance(data_id, str)
        or not _DATA_NAME.match(data_id)
        or Path(data_id).name != data_id
    ):
        raise ValueError("Invalid Data ID: %s" % data_id)
    root = data_root.resolve()
    package = (root / data_id).resolve()
    package.relative_to(root)
    target = package / "data.json"
    if not package.is_dir() or not target.is_file():
        raise FileNotFoundError("Data package is missing data.json: %s" % data_id)
    return target.resolve()
