#!/usr/bin/env python3
"""Run all skill-owned suites, or one selected suite, using only the standard library."""
import argparse
from pathlib import Path
import subprocess
import sys
from package_info import load_package


def main():
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", help="Run only this package member's tests")
    args = parser.parse_args()
    names = load_package(root)["skills"]
    if args.skill and args.skill not in names:
        parser.error("Select a skill listed in docs/emailbutler.json")
    failed = False
    for name in ([args.skill] if args.skill else names):
        folder = root / "skills" / name / "tests"
        if not list(folder.glob("test_*.py")):
            print("Missing test suite: " + name, file=sys.stderr)
            failed = True
            continue
        print("Checking " + name, flush=True)
        result = subprocess.run([sys.executable, "-B", "-m", "unittest", "discover", "-s", str(folder), "-q"], cwd=root)
        failed = result.returncode != 0 or failed
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
