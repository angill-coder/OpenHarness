"""Repository-local Data package discovery and resolution."""

from __future__ import annotations

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
