#!/usr/bin/env python3
"""Launch an interactive Claude Code session with full katz knowledge.

Usage:
    autokatz              # interactive review session
    autokatz --status     # just print paper status and exit
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


SKILLS_DIR = Path(__file__).parent / "skills"
OVERVIEW_PATH = Path(__file__).parent / "OVERVIEW.md"


def load_overview():
    if OVERVIEW_PATH.exists():
        return OVERVIEW_PATH.read_text(encoding="utf-8")
    return ""


def load_all_skills():
    """Load all SKILL.md files into a single document."""
    skills = []
    if not SKILLS_DIR.is_dir():
        return ""
    for skill_dir in sorted(SKILLS_DIR.iterdir()):
        skill_file = skill_dir / "SKILL.md"
        if skill_file.exists():
            content = skill_file.read_text(encoding="utf-8")
            skills.append(f"## Skill: {skill_dir.name}\n\n{content}")
    return "\n\n---\n\n".join(skills)


def get_paper_status():
    """Get current katz paper status, or None if not initialized."""
    try:
        result = subprocess.run(
            ["katz", "paper", "status"],
            capture_output=True, text=True, check=True,
        )
        return json.loads(result.stdout)
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        return None


def get_katz_state():
    """Get a summary of the current katz state."""
    lines = []

    status = get_paper_status()
    if status:
        lines.append(f"Paper: {status.get('source_root', 'unknown')} @ {status['commit'][:8]}")
        lines.append(f"Sections: {status.get('sections', 0)}, Sentences: {status.get('sentences', 0)}")
        lines.append(f"Valid: {status.get('valid', False)}")

        # Count issues by state
        try:
            result = subprocess.run(
                ["katz", "issue", "list"], capture_output=True, text=True, check=True
            )
            issues = json.loads(result.stdout)
            from collections import Counter
            states = Counter(i.get("state") for i in issues)
            if issues:
                state_parts = [f"{s}: {n}" for s, n in sorted(states.items())]
                lines.append(f"Issues: {len(issues)} total ({', '.join(state_parts)})")
        except Exception:
            pass

        # Count evals
        try:
            result = subprocess.run(
                ["katz", "eval", "results"], capture_output=True, text=True, check=True
            )
            evals = json.loads(result.stdout)
            if evals:
                lines.append(f"Evaluations: {len(evals)} completed")
        except Exception:
            pass

        # Count enabled spotters
        try:
            result = subprocess.run(
                ["katz", "spotter", "list"], capture_output=True, text=True, check=True
            )
            spotters = json.loads(result.stdout)
            if spotters:
                lines.append(f"Spotters: {len(spotters)} enabled")
        except Exception:
            pass
    else:
        lines.append("No katz project initialized in this directory.")
        lines.append("Run `katz init` to get started, or use `/review-paper` for a guided walkthrough.")

    return "\n".join(lines)


def build_system_prompt():
    """Build the full system prompt for the Claude Code session."""
    overview = load_overview()
    skills = load_all_skills()
    state = get_katz_state()

    prompt = f"""# katz — Paper Review Assistant

You are assisting with an academic paper review using katz, a version-aware
ledger for paper review artifacts. You have full knowledge of katz's capabilities
and all available skills.

## Current State

{state}

## How katz Works

{overview}

## Available Skills

The following skills are available. You can invoke them with `katz guide skill <name>`
to read the full instructions, or follow them directly based on the documentation below.

{skills}

## Guidelines

- Start by understanding where the user is in the review process. Check `katz paper status`.
- If the paper isn't registered yet, guide them through `/review-paper`.
- If they're mid-review, pick up where they left off.
- Use katz commands for all structured data (issues, evals, spotters) — don't track
  review findings outside katz.
- Be candid in evaluations and investigations. The value is in honesty, not diplomacy.
- When filing issues, always include the manuscript byte range and relevant artifacts.
- When suggesting fixes, be specific and actionable — reference lines, sections, and files.
"""
    return prompt


def main():
    parser = argparse.ArgumentParser(
        description="Launch an interactive Claude Code session with full katz knowledge",
    )
    parser.add_argument("--status", action="store_true",
                        help="Print paper status and exit")
    parser.add_argument("--print-prompt", action="store_true",
                        help="Print the system prompt and exit (for debugging)")
    args = parser.parse_args()

    if args.status:
        print(get_katz_state())
        return

    prompt = build_system_prompt()

    if args.print_prompt:
        print(prompt)
        return

    # Write prompt to a temp file to avoid shell escaping issues
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".md", prefix="autokatz_prompt_", delete=False
    ) as f:
        f.write(prompt)
        prompt_path = f.name

    try:
        # Launch claude with the katz system prompt appended
        cmd = ["claude", "--append-system-prompt-file", prompt_path]
        os.execvp("claude", cmd)
    except FileNotFoundError:
        print("Error: 'claude' command not found. Install Claude Code first:", file=sys.stderr)
        print("  https://claude.ai/claude-code", file=sys.stderr)
        sys.exit(1)
    finally:
        # Clean up temp file (only reached if execvp fails)
        try:
            os.unlink(prompt_path)
        except OSError:
            pass


if __name__ == "__main__":
    main()
