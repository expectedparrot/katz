"""`katz guide` commands: lifecycle, skills, and bundled scripts."""
from __future__ import annotations

import typer
from pathlib import Path

from ..assets import PACKAGE_DIR, SKILLS_DIR
from ..errors import emit_json, fail


guide_app = typer.Typer(help="Self-documenting guide for agents.", invoke_without_command=True)


def available_skills() -> list[str]:
    if not SKILLS_DIR.is_dir():
        return []
    return [d.name for d in SKILLS_DIR.iterdir() if (d / "SKILL.md").exists()]


@guide_app.callback()
def guide_root(ctx: typer.Context) -> None:
    """Describe the complete auditable Katz lifecycle."""
    if ctx.invoked_subcommand is not None:
        return
    emit_json(
        {
            "lifecycle": [
                {
                    "stage": "register",
                    "purpose": "Preserve and map a committed canonical manuscript.",
                    "commands": ["katz init", "katz paper register", "katz paper auto-chunk"],
                },
                {
                    "stage": "configure",
                    "purpose": "Select explicit, versioned review criteria and spotters.",
                    "commands": ["katz spotter init-catalog", "katz spotter enable"],
                },
                {
                    "stage": "package",
                    "purpose": "Create and verify a native EDSL Jobs artifact.",
                    "commands": ["katz spotter jobs", "katz paper review-jobs", "katz review jobs"],
                },
                {
                    "stage": "external-execution",
                    "purpose": "Run model work explicitly outside Katz.",
                    "commands": ["ep run <jobs.ep> --output <results.ep>"],
                    "requires_user_approval": True,
                },
                {
                    "stage": "register-and-audit",
                    "purpose": "Audit Results and register them without hiding retries.",
                    "commands": ["katz results audit", "katz ingest", "katz spotter ingest"],
                },
                {
                    "stage": "investigate-and-report",
                    "purpose": "Investigate draft findings and generate an evidence-linked report.",
                    "commands": ["katz issue next", "katz validate", "katz report finalize"],
                },
            ],
            "execution_boundary": {
                "owner": "ep",
                "rule": "Katz creates native Jobs and consumes Results; it never executes model calls.",
            },
            "resume": "Run `katz next` after every material stage.",
            "documentation": {
                "overview": "katz guide overview",
                "skills": "katz guide skills",
                "cli_topics": "katz docs list",
            },
        },
        next_steps=["Run `katz next` to inspect the repository's current artifact state."],
    )


@guide_app.command("overview")
def guide_overview() -> None:
    """Show how katz works and what it can do."""
    overview = PACKAGE_DIR / "OVERVIEW.md"
    if not overview.exists():
        fail("Overview file not found", "not_found")
    emit_json({"markdown": overview.read_text(encoding="utf-8")})


@guide_app.command("skills")
def guide_skills() -> None:
    """List available skills with descriptions."""
    results = []
    if SKILLS_DIR.is_dir():
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            skill_file = skill_dir / "SKILL.md"
            if not skill_file.exists():
                continue
            content = skill_file.read_text(encoding="utf-8")
            # Extract description from YAML frontmatter
            description = None
            name = skill_dir.name
            if content.startswith("---\n"):
                end = content.find("\n---\n", 4)
                if end != -1:
                    import yaml
                    try:
                        fm = yaml.safe_load(content[4:end]) or {}
                        description = fm.get("description")
                        name = fm.get("name", name)
                    except Exception:
                        pass
            # List scripts in this skill
            scripts_dir = skill_dir / "scripts"
            scripts = [f.name for f in sorted(scripts_dir.glob("*.py"))] if scripts_dir.is_dir() else []
            results.append({"name": name, "description": description, "scripts": scripts})
    emit_json(results)


@guide_app.command("skill")
def guide_skill(name: str) -> None:
    """Show a skill's full SKILL.md instructions."""
    parts = Path(name).parts
    if len(parts) != 1 or parts[0] in {"", ".", ".."}:
        fail(f"Skill '{name}' not found", "not_found", {"name": name, "available": available_skills()})
    skill_file = SKILLS_DIR / name / "SKILL.md"
    if not skill_file.exists():
        fail(f"Skill '{name}' not found", "not_found", {"name": name, "available": available_skills()})
    emit_json({"name": name, "markdown": skill_file.read_text(encoding="utf-8")})


@guide_app.command("script")
def guide_script(path: str) -> None:
    """Show a script file from a skill's scripts/ directory.

    Path format: <skill-name>/scripts/<filename> or just <skill-name>/<filename>
    """
    # Normalize either <skill>/scripts/<file> or <skill>/<file>.
    skills_root = SKILLS_DIR.resolve()

    def safe_skill_file(candidate: Path) -> Path | None:
        try:
            resolved = candidate.resolve()
            relative = resolved.relative_to(skills_root)
        except (OSError, ValueError):
            return None
        if len(relative.parts) < 3 or relative.parts[1] != "scripts":
            return None
        return resolved

    parts = Path(path).parts
    full_path = None
    if len(parts) >= 3 and parts[1] == "scripts":
        full_path = safe_skill_file(SKILLS_DIR / path)
    if full_path is None or not full_path.exists():
        # Try inserting scripts/
        if len(parts) >= 2 and parts[1] != "scripts":
            full_path = safe_skill_file(SKILLS_DIR / parts[0] / "scripts" / Path(*parts[1:]))
    if full_path is None or not full_path.exists() or not full_path.is_file():
        fail(f"Script not found: {path}", "not_found", {"path": path})
    emit_json({"path": path, "source": full_path.read_text(encoding="utf-8")})
