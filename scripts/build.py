"""Package only expert runtime assets; no installation or registry changes."""
import json
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (".codebuddy-plugin", "settings.json", "README.md", "agents",
           "skills", "resources", "rubrics", "avatars")


def build():
    # Judge 模型分散在 frontmatter 与文档里；打包前确认它们仍与唯一数据源
    # 一致，避免升级时漏改一处导致两个 Judge 用不同模型打分。
    check = subprocess.run(
        [sys.executable, str(ROOT / "scripts/check_judge_model.py")],
        capture_output=True, text=True,
    )
    if check.returncode != 0:
        raise ValueError(check.stdout.strip() or "Judge model consistency check failed")
    manifest = json.loads((ROOT / ".codebuddy-plugin/plugin.json").read_text(encoding="utf-8"))
    if manifest["expertType"] != "team":
        raise ValueError("Expected a team expert")
    members = [manifest["teamInfo"]["leadAgent"], *manifest["teamInfo"]["memberAgents"]]
    if len(members) != len(set(members)):
        raise ValueError("Duplicate member ID")
    for member in members:
        if not (ROOT / "agents" / (member + ".md")).is_file():
            raise ValueError("Missing Agent: " + member)
    for item in [manifest, *manifest["members"]]:
        if not (ROOT / item["avatar"]).is_file():
            raise ValueError("Missing avatar: " + item["avatar"])
    output = ROOT / "dist"
    output.mkdir(exist_ok=True)
    target = output / (manifest["name"] + "-" + manifest["version"] + ".zip")
    files = []
    for item in RUNTIME:
        path = ROOT / item
        if not path.exists():
            raise ValueError("Missing runtime asset: " + item)
        files.extend([path] if path.is_file() else sorted(path.rglob("*")))
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            rel = path.relative_to(ROOT)
            if not path.is_file() or "__pycache__" in rel.parts or path.name == ".DS_Store":
                continue
            if path.is_symlink():
                raise ValueError("Runtime symlink not allowed: " + str(rel))
            archive.write(path, str(Path(manifest["name"]) / rel))
    print(target)


if __name__ == "__main__":
    build()
