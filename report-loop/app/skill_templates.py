"""Discover report-generation Skill templates from the repository."""

from pathlib import Path


DEFAULT_SKILL_TEMPLATE = "research-report"
INSTRUCTION_CANDIDATES = (
    "instruction.md",
    "instructions.md",
    "references/instruction.md",
    "references/instructions.md",
)


def _instruction_path(skill_dir):
    return next(
        (skill_dir / name for name in INSTRUCTION_CANDIDATES if (skill_dir / name).is_file()),
        None,
    )


def list_skill_templates(skills_root):
    """Return usable first-level Skill folders, including their prompt documents."""
    root = Path(skills_root).resolve()
    templates = []
    if not root.is_dir():
        return templates
    for skill_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        skill_path = skill_dir / "SKILL.md"
        instruction_path = _instruction_path(skill_dir)
        if not skill_path.is_file() or instruction_path is None:
            continue
        templates.append({
            "id": skill_dir.name,
            "label": skill_dir.name,
            "skill": skill_path.read_text(encoding="utf-8"),
            "instruction": instruction_path.read_text(encoding="utf-8"),
            "skill_path": skill_path.relative_to(root.parent).as_posix(),
            "instruction_path": instruction_path.relative_to(root.parent).as_posix(),
        })
    return templates


def skill_template_document(skills_root):
    templates = list_skill_templates(skills_root)
    default_id = DEFAULT_SKILL_TEMPLATE if any(
        item["id"] == DEFAULT_SKILL_TEMPLATE for item in templates
    ) else (templates[0]["id"] if templates else "")
    return {"default": default_id, "templates": templates}
