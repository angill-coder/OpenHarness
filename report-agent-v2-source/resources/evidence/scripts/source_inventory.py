"""Track source bytes, not facts. Standard library only; no model or network calls."""

import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


MANIFEST = "数据版本说明.md"
SCHEMA = "report-agent-source-inventory/v1"
SKIP_DIRS = {"报告", ".git", ".workbuddy", ".report-agent", "Agent运行记录", "__pycache__", "__MACOSX"}


def digest(filename):
    before = filename.stat()
    result = hashlib.sha256()
    with filename.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    after = filename.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise ValueError("File changed during scan: " + str(filename))
    return result.hexdigest()


def optional_digest(filename):
    return digest(filename) if filename.exists() else None


def load(filename):
    with filename.open(encoding="utf-8-sig") as stream:
        text = stream.read()
    if filename.name == MANIFEST:
        blocks = re.findall(r"```json\n(.*?)\n```", text, re.S)
        if len(blocks) != 1 or text.count("## 当前素材指纹清单") != 1:
            raise ValueError("Invalid data version log: expected one current source inventory")
        text = blocks[0]
    return json.loads(text)


def inventory(root, exclusions):
    files = {}
    excluded = {Path(p) for p in exclusions}

    def visit(directory):
        for entry in sorted(directory.iterdir()):
            if entry in excluded or any(p in entry.parents for p in excluded):
                continue
            if entry.name == ".DS_Store" or entry.name.startswith(("._", "~$")):
                continue
            if directory == root and entry.name in {MANIFEST, "structured_data.json", "素材清单.json"}:
                continue
            if entry.name in SKIP_DIRS and entry.is_dir():
                continue
            # Do not silently omit inaccessible/external sources and call them unchanged.
            if entry.is_symlink():
                raise ValueError("Resolve source symlink explicitly: " + str(entry))
            if entry.is_dir():
                visit(entry)
            elif entry.is_file():
                files[entry.relative_to(root).as_posix()] = digest(entry)
            else:
                raise ValueError("Unsupported source entry: " + str(entry))

    visit(root)
    return files


def baseline_status(previous, root, exclusions, evidence_hash):
    if previous is None:
        return "missing"
    if not isinstance(previous, dict):
        raise ValueError("Invalid source manifest; expected an object")
    if (previous.get("schema") != SCHEMA or previous.get("root") != str(root)
            or previous.get("excluded") != exclusions):
        return "scope_changed"
    if evidence_hash is None or previous.get("structuredDataSha256") != evidence_hash:
        return "evidence_changed"
    files = previous.get("files")
    if not isinstance(files, dict) or not all(
        isinstance(k, str) and isinstance(v, str) and len(v) == 64
        and all(c in "0123456789abcdef" for c in v) for k, v in files.items()
    ):
        raise ValueError("Invalid source manifest; do not overwrite it automatically")
    return "valid"


def scan(root, output, exclude):
    root, output = root.resolve(strict=True), output.resolve()
    if not root.is_dir():
        raise ValueError("Source root must be a directory")
    exclusions = sorted({str(Path(p).resolve()) for p in exclude})
    if any(Path(p) == root or Path(p) in root.parents for p in exclusions):
        raise ValueError("Cannot exclude the source root")
    # Scans belong in workDir, outside the source inventory.
    if output.is_relative_to(root) and not (
        any(part in SKIP_DIRS for part in output.relative_to(root).parts[:-1])
        or any(Path(p) in output.parents for p in exclusions)
    ):
        raise ValueError("Put scan output in the report workDir and exclude any custom output directory")
    manifest = root / MANIFEST
    manifest_hash = optional_digest(manifest)
    previous = load(manifest) if manifest_hash else None
    evidence_hash = optional_digest(root / "structured_data.json")
    current = inventory(root, exclusions)
    status = baseline_status(previous, root, exclusions, evidence_hash)
    old = previous["files"] if status == "valid" else {}
    delta = {
        "added": sorted(current.keys() - old.keys()),
        "modified": sorted(k for k in current.keys() & old.keys() if current[k] != old[k]),
        "deleted": sorted(old.keys() - current.keys()),
    }
    result = {
        "schema": SCHEMA, "root": str(root), "excluded": exclusions,
        "baselineStatus": status, "baselineSha256": manifest_hash,
        "structuredDataSha256": evidence_hash, "files": current, "changes": delta,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return {"scanPath": str(output), "baselineStatus": status,
            "fullReviewRequired": status != "valid", "changes": delta,
            "unchangedCount": len(current.keys() & old.keys()) - len(delta["modified"])}


def confirm(scan_path, evidence_hash, summary):
    candidate = load(scan_path)
    if not isinstance(candidate, dict) or candidate.get("schema") != SCHEMA:
        raise ValueError("Invalid scan schema")
    root = Path(candidate["root"]).resolve(strict=True)
    manifest = root / MANIFEST
    if optional_digest(manifest) != candidate["baselineSha256"]:
        raise ValueError("Source manifest changed concurrently; scan again")
    if inventory(root, candidate["excluded"]) != candidate["files"]:
        raise ValueError("Sources changed after scan; do not mark them processed")
    shared = root / "structured_data.json"
    if digest(shared) != evidence_hash:
        raise ValueError("Shared evidence differs from the reviewed hash")
    # Semantic review/schema validation is the Evidence Agent's responsibility.
    evidence = load(shared)
    if not isinstance(evidence, dict) or evidence.get("schema") != "openharness-structured-data/v1" or not evidence.get("items"):
        raise ValueError("No valid structured evidence to associate with inventory")
    published = {k: candidate[k] for k in ("schema", "root", "excluded", "files")}
    published["structuredDataSha256"] = evidence_hash
    old_text = manifest.read_text(encoding="utf-8-sig") if manifest.exists() else ""
    old = load(manifest) if old_text else None
    if old and not re.fullmatch(r"D[1-9][0-9]*", old.get("dataVersion", "")):
        raise ValueError("Invalid data version; do not reset version history")
    changed = old is None or old.get("structuredDataSha256") != evidence_hash
    version = (int(old["dataVersion"][1:]) if old else 0) + int(changed)
    published["dataVersion"] = "D" + str(version)
    published["updatedAt"] = datetime.now().astimezone().isoformat(timespec="seconds") if changed else old["updatedAt"]
    history = old_text.split("## 当前素材指纹清单", 1)[0] if old else (
        "# 数据版本说明\n\n版本只记录变更，不保存旧数据副本；不能据此恢复旧数据。\n\n"
        "| 数据版本 | 更新时间 | 更新内容 | 数据 SHA-256 |\n|---|---|---|---|\n"
    )
    if changed:
        description = " ".join(summary.split()).replace("|", "／").replace("`", "＇").replace("#", "＃")
        if not description:
            raise ValueError("A data change summary is required")
        history = history.rstrip() + f"\n| D{version} | {published['updatedAt']} | {description} | {evidence_hash} |\n"
    text = history.rstrip() + "\n\n## 当前素材指纹清单\n\n用于增量核验，不是数据快照；只保留当前清单。\n\n```json\n"
    text += json.dumps(published, ensure_ascii=False, indent=2) + "\n```\n"
    # One atomic version log + current inventory; no source or evidence backup.
    fd, temp = tempfile.mkstemp(prefix=".source-inventory-", suffix=".tmp", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temp, manifest)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return {"manifestPath": str(manifest), "status": "confirmed", "dataVersion": published["dataVersion"],
            "dataSha256": evidence_hash, "versionChanged": changed}


def check(root, version=None, expected_hash=None):
    root = root.resolve(strict=True)
    metadata = load(root / MANIFEST)
    actual = digest(root / "structured_data.json")
    if (not isinstance(metadata, dict) or metadata.get("schema") != SCHEMA
            or metadata.get("root") != str(root)
            or not re.fullmatch(r"D[1-9][0-9]*", metadata.get("dataVersion", ""))
            or metadata.get("structuredDataSha256") != actual
            or (version is not None and metadata.get("dataVersion") != version)
            or (expected_hash is not None and actual != expected_hash)):
        raise ValueError("DATA_VERSION_CHANGED: do not use stale evidence or scores")
    if inventory(root, metadata["excluded"]) != metadata["files"]:
        raise ValueError("DATA_VERSION_CHANGED: sources differ from the registered evidence")
    return {"dataVersion": metadata["dataVersion"], "dataSha256": actual, "status": "ok"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    scanner = commands.add_parser("scan")
    scanner.add_argument("--root", type=Path, required=True)
    scanner.add_argument("--output", type=Path, required=True)
    scanner.add_argument("--exclude", action="append", default=[])
    confirmer = commands.add_parser("confirm")
    confirmer.add_argument("--scan", type=Path, required=True)
    confirmer.add_argument("--evidence-sha256", required=True)
    confirmer.add_argument("--summary", required=True)
    checker = commands.add_parser("check")
    checker.add_argument("--root", type=Path, required=True)
    checker.add_argument("--version")
    checker.add_argument("--sha256")
    args = parser.parse_args()
    try:
        if args.command == "scan":
            result = scan(args.root, args.output, args.exclude)
        elif args.command == "confirm":
            result = confirm(args.scan, args.evidence_sha256, args.summary)
        else:
            result = check(args.root, args.version, args.sha256)
        print(json.dumps(result, ensure_ascii=True))
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=True), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
