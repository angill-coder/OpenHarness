"""Small filesystem helpers owned by Report Loop."""

from __future__ import annotations

import hashlib
from pathlib import Path


def directory_hash(path: Path) -> str:
    root = path.expanduser().resolve()
    digest = hashlib.sha256()
    for item in sorted(
        candidate
        for candidate in root.rglob("*")
        if candidate.is_file()
        and "__pycache__" not in candidate.parts
        and not candidate.name.startswith(".")
    ):
        digest.update(str(item.relative_to(root)).encode("utf-8"))
        digest.update(b"\0")
        digest.update(item.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()
