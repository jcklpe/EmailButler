#!/usr/bin/env python3
"""Locate and validate the complete EmailButler package through a registered skill."""
import argparse
import json
from pathlib import Path
import re
import sys

REQUIRED_FILES = (
    'AGENTS.md',
    'README.md',
    'skills/customize-emailbutler/assets/default-profile.json',
    'skills/customize-emailbutler/scripts/butler.py',
    'skills/customize-emailbutler/scripts/profiles.py',
    'skills/setup-emailbutler/scripts/private_state.py',
    'skills/setup-emailbutler/scripts/capabilities.py',
    'skills/review-inbox-emailbutler/scripts/journal.py',
    'skills/review-inbox-emailbutler/scripts/review.py',
    'skills/setup-emailbutler/scripts/install_skill.py',
    'skills/setup-emailbutler/scripts/package_info.py',
    'skills/setup-emailbutler/scripts/onboarding.py',
    'skills/bulk-triage-emailbutler/scripts/bulk.py',
    'skills/bulk-triage-emailbutler/scripts/execute_bulk.py',
    'skills/bulk-triage-emailbutler/scripts/plan_bulk.py',
    'skills/bulk-triage-emailbutler/scripts/freeze_bulk.py',
    'skills/bulk-triage-emailbutler/scripts/connector_adapters.py',
    'skills/bulk-triage-emailbutler/scripts/connector_loop.js',
    'skills/bulk-triage-emailbutler/references/execution.md',
    'skills/bulk-triage-emailbutler/references/planning.md',
    'skills/setup-emailbutler/scripts/run_tests.py',
)


def load_package(source):
    source = Path(source).resolve(strict=True)
    value = json.loads((source / "docs" / "emailbutler.json").read_text(encoding="utf-8"))
    if set(value) != {"schema_version", "name", "version", "repository", "skills"} or value["schema_version"] != 1 or value["name"] != "EmailButler":
        raise ValueError("Unsupported EmailButler package manifest")
    if not isinstance(value["version"], str) or not re.fullmatch(r"\d+\.\d+\.\d+", value["version"]):
        raise ValueError("Invalid package version")
    names = value["skills"]
    if not isinstance(names, list) or not names or any(not isinstance(n, str) for n in names) or len(set(names)) != len(names):
        raise ValueError("Expected distinct package skills")
    for name in names:
        if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) or len(name) > 64:
            raise ValueError("Invalid package skill name")
        folder = source / "skills" / name
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
        header = text.split("---", 2)
        if len(header) != 3 or header[0].strip() or not re.search(r"^name:\s*" + re.escape(name) + r"\s*$", header[1], re.M):
            raise ValueError("Skill name and directory differ: " + name)
        if not (folder / "agents/openai.yaml").is_file():
            raise ValueError("Missing human-facing skill metadata: " + name)
    missing = [name for name in REQUIRED_FILES if not (source / name).is_file()]
    if missing:
        raise ValueError("Missing package resources: " + ", ".join(missing))
    return value


def locate(skill_directory):
    skill = Path(skill_directory).resolve(strict=True)
    root = skill.parent.parent
    package = load_package(root)
    if skill.parent.name != "skills" or skill.name not in package["skills"]:
        raise ValueError("This directory is not a member of the EmailButler package")
    return {"root": str(root), **package, "mailbox_changed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-dir", type=Path)
    args = parser.parse_args()
    try:
        result = locate(args.skill_dir) if args.skill_dir else {"root": str(Path(__file__).resolve().parents[3]), **load_package(Path(__file__).resolve().parents[3])}
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
