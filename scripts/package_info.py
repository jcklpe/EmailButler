#!/usr/bin/env python3
"""Locate and validate the complete EmailButler package through a registered skill."""
import argparse
import json
from pathlib import Path
import re
import sys

REQUIRED_FILES = ("AGENTS.md", "README.md", "docs/interface.md", "docs/state.md", "assets/default-profile.json", "scripts/butler.py", "scripts/capabilities.py", "scripts/journal.py", "scripts/review.py", "scripts/install_skill.py", "scripts/onboarding.py", "scripts/bulk.py")


def load_package(source):
    source = Path(source).resolve(strict=True)
    value = json.loads((source / "emailbutler.json").read_text(encoding="utf-8"))
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
        result = locate(args.skill_dir) if args.skill_dir else {"root": str(Path(__file__).resolve().parents[1]), **load_package(Path(__file__).resolve().parents[1])}
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
