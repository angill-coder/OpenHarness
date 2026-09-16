"""Register an existing SkillHub package in WorkBuddy; Python stdlib only."""
import argparse
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import sys
import tempfile


def read_object(path, default):
    raw = path.read_bytes() if path.exists() else None
    value = json.loads(raw.decode("utf-8-sig")) if raw is not None else copy.deepcopy(default)
    if not isinstance(value, dict):
        raise ValueError("Expected JSON object: " + str(path))
    return value, raw


def config_directory():
    configured = os.environ.get("WORKBUDDY_CONFIG_DIR") or os.environ.get("CODEBUDDY_CONFIG_DIR")
    directory = Path(configured).expanduser() if configured else Path.home() / ".workbuddy"
    if not directory.is_absolute() or not directory.is_dir():
        raise ValueError("WorkBuddy config directory not found; check WORKBUDDY_CONFIG_DIR: " + str(directory))
    return directory.resolve()


def register(root, config, apply=False):
    root, config = root.resolve(), config.resolve()
    manifest, _ = read_object(root / ".codebuddy-plugin/plugin.json", {})
    name, version = manifest.get("name", ""), manifest.get("version", "")
    if not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) or not isinstance(version, str) or not version:
        raise ValueError("Plugin manifest needs name and version")
    agents = manifest.get("agents")
    if not isinstance(agents, list) or not agents:
        raise ValueError("Plugin manifest needs agents")
    for entry in agents:
        if not isinstance(entry, str):
            raise ValueError("Invalid agent path")
        candidate = (root / entry).resolve()
        if root not in candidate.parents or not candidate.is_file():
            raise ValueError("Agent file missing or outside package: " + entry)

    registry_path, settings_path = config / "plugins/installed_plugins.json", config / "settings.json"
    registry, registry_raw = read_object(registry_path, {"version": 2, "plugins": {}})
    settings, settings_raw = read_object(settings_path, {})
    if registry.get("version") != 2 or not isinstance(registry.get("plugins"), dict):
        raise ValueError("Unsupported installed_plugins.json schema; left unchanged")
    if "enabledPlugins" in settings and not isinstance(settings["enabledPlugins"], dict):
        raise ValueError("Invalid enabledPlugins; left unchanged")
    enabled = settings.get("enabledPlugins", {})
    plugin_id = name + "@skillhub"
    result = {"pluginId": plugin_id, "installPath": str(root), "version": version}
    if plugin_id in enabled and not isinstance(enabled[plugin_id], bool):
        raise ValueError("Invalid plugin enabled state; left unchanged")

    # Do not register a second copy over a native Expert/plugin installation.
    for key, records in registry["plugins"].items():
        if not key.startswith(name + "@"):
            continue
        if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
            raise ValueError("Invalid installed record: " + key)
        for record in records:
            if record.get("scope") not in ("user", "managed"):
                continue
            location = record.get("installPath")
            if not isinstance(location, str):
                raise ValueError("Invalid installPath: " + key)
            same = Path(location).resolve() == root
            if key != plugin_id and same:
                return {**result, "pluginId": key, "status": "ready" if enabled.get(key) is True else "disabled"}
            if key != plugin_id and Path(location).is_dir() and enabled.get(key) is True:
                return {**result, "status": "other_installation", "existingPluginId": key, "existingPath": location}

    records = registry["plugins"].get(plugin_id, [])
    if any(r.get("scope") == "managed" for r in records):
        raise ValueError("Managed plugin registration cannot be changed")
    users = [r for r in records if r.get("scope") == "user"]
    if len(users) > 1:
        raise ValueError("Multiple user records; inspect registration instead of overwriting")
    current = users[0] if users else None
    if enabled.get(plugin_id) is False:
        return {**result, "status": "disabled"}
    if current and Path(current["installPath"]).resolve() == root and current.get("version") == version and enabled.get(plugin_id) is True:
        return {**result, "status": "ready"}

    # Only bootstrap packages actually installed by SkillHub, never development folders.
    metadata, _ = read_object(root / "_skillhub_meta.json", {})
    if metadata.get("source") != "skillhub":
        raise ValueError("Not a SkillHub installation; use WorkBuddy's normal plugin/expert installer")
    if not apply:
        return {**result, "status": "registration_required"}

    now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    record = {**(current or {}), "scope": "user", "installPath": str(root), "version": version,
              "installedAt": current.get("installedAt", now) if current else now, "lastUpdated": now}
    registry["plugins"][plugin_id] = [record if r is current else r for r in records] if current else [*records, record]
    settings.setdefault("enabledPlugins", {})[plugin_id] = True
    changes = [(p, raw, value) for p, raw, value in [(registry_path, registry_raw, registry), (settings_path, settings_raw, settings)]
               if raw is None or json.loads(raw.decode("utf-8-sig")) != value]
    backups, staged = [], []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    try:
        for filename, original, value in changes:
            filename.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=filename.parent, prefix=".report-agent-", delete=False) as handle:
                temporary = Path(handle.name)
                staged.append((filename, original, temporary))
                handle.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            if original is not None:
                shutil.copymode(filename, temporary)
                backup = filename.with_name(filename.name + ".before-report-agent-" + stamp + ".bak")
                shutil.copy2(filename, backup)
                backups.append(str(backup))
        for filename, original, temporary in staged:
            if (filename.read_bytes() if filename.exists() else None) != original:
                raise ValueError("Configuration changed concurrently; stop and recheck: " + str(filename))
            os.replace(temporary, filename)
    finally:
        for _, _, temporary in staged:
            if temporary.exists():
                temporary.unlink()
    return {**result, "status": "registered", "backups": backups, "newSessionRequired": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Apply missing/stale registration; otherwise check only")
    args = parser.parse_args()
    try:
        result = register(Path(__file__).resolve().parents[2], config_directory(), args.apply)
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(json.dumps({"status": "error", "reason": str(error)}, ensure_ascii=True))
        return 2
    print(json.dumps(result, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
