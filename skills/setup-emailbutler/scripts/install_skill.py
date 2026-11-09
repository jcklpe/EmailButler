#!/usr/bin/env python3
"""Register the EmailButler skill collection without overwriting other skills."""
import argparse
import json
import os
from pathlib import Path
import sys
from package_info import load_package


class InstallError(ValueError):
    pass


CLIENT_DIRECTORIES = {"codex": Path(".agents") / "skills", "claude-code": Path(".claude") / "skills"}


def link_target(path):
    return Path(os.path.abspath(path.parent / path.readlink()))


def install(source, client, skills_dir=None, execute=False, previous_source=None, migrate_registrations=False):
    if client not in CLIENT_DIRECTORIES:
        raise InstallError("Unsupported client; inspect its supported installation mechanism")
    if previous_source is not None and not migrate_registrations:
        raise InstallError("Relocated registrations require Update EmailButler and --migrate-registrations; existing skills were left unchanged")
    try:
        source = Path(source).resolve(strict=True)
        package = load_package(source)
    except (OSError, ValueError, TypeError) as error:
        raise InstallError("The checkout is missing a valid EmailButler package: " + str(error)) from None
    base = (Path(skills_dir) if skills_dir is not None else Path.home() / CLIENT_DIRECTORIES[client]).absolute()
    previous = Path(os.path.abspath(previous_source)) if previous_source is not None else None
    replacements, registrations = [], []
    for name in package["skills"]:
        target, wanted = base / name, source / "skills" / name
        registrations.append({"name": name, "source": str(wanted), "destination": str(target)})
        if target.is_symlink() and target.resolve() == wanted:
            continue
        if target.exists() or target.is_symlink():
            if not (previous is not None and target.is_symlink() and link_target(target) == previous / "skills" / name):
                raise InstallError("An existing skill occupies " + name + "; existing skills were left unchanged")
            replacements.append((target, wanted, target.readlink()))
        else:
            replacements.append((target, wanted, None))
    legacy = base / "email-butler"
    retire_legacy = legacy.exists() or legacy.is_symlink()
    if retire_legacy and not (legacy.is_symlink() and (legacy.resolve() == source or (previous is not None and link_target(legacy) == previous))):
        raise InstallError("An unrelated or copied legacy skill was left unchanged; inspect it before migrating")
    result = {"client": client, "source": str(source), "destination": str(base), "registrations": registrations, "retire_legacy": retire_legacy, "migration_requested": migrate_registrations, "discovery_verified": False, "connectors_changed": False}
    if retire_legacy and not migrate_registrations:
        return {"status": "update_required", **result, "retire_legacy": False, "next_skill": "update-emailbutler"}
    if not execute:
        return {"status": "preview", **result}
    completed = []
    legacy_value = legacy.readlink() if retire_legacy else None
    legacy_removed = False
    try:
        base.mkdir(parents=True, exist_ok=True)
        for target, wanted, old in replacements:
            if old is not None:
                if not target.is_symlink() or target.readlink() != old:
                    raise InstallError("A registration changed during migration")
                target.unlink()
            try:
                target.symlink_to(wanted, target_is_directory=True)
            except OSError:
                if old is not None and not target.exists() and not target.is_symlink():
                    target.symlink_to(old, target_is_directory=True)
                raise
            completed.append((target, wanted, old))
        for item in registrations:
            target = Path(item["destination"])
            if not target.is_symlink() or target.resolve() != Path(item["source"]) or not (target / "SKILL.md").is_file():
                raise InstallError("Skill registration could not be verified")
        if retire_legacy:
            if not legacy.is_symlink() or legacy.readlink() != legacy_value:
                raise InstallError("The legacy registration changed during migration")
            legacy.unlink()
            legacy_removed = True
    except (OSError, InstallError) as error:
        for target, wanted, old in reversed(completed):
            if target.is_symlink() and link_target(target) == wanted:
                target.unlink()
                if old is not None:
                    target.symlink_to(old, target_is_directory=True)
        if legacy_removed and not legacy.exists() and not legacy.is_symlink():
            legacy.symlink_to(legacy_value, target_is_directory=True)
        raise InstallError("Registration failed; existing registrations were preserved: " + str(error)) from None
    return {"status": "registered" if replacements or retire_legacy else "already_registered", **result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", required=True, choices=sorted(CLIENT_DIRECTORIES))
    parser.add_argument("--skills-dir", type=Path)
    parser.add_argument("--previous-source", type=Path)
    parser.add_argument("--migrate-registrations", action="store_true", help="Apply the registration migration prepared by Update EmailButler; ordinary setup never replaces existing registrations")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(install(Path(__file__).resolve().parents[3], args.client, args.skills_dir, args.execute, args.previous_source, args.migrate_registrations), indent=2))
        return 0
    except (InstallError, OSError) as error:
        print(json.dumps({"error": str(error), "connectors_changed": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
