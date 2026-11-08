#!/usr/bin/env python3
"""Record first-run cleanup outcomes without granting permission or operating mail."""
import argparse
import json
from pathlib import Path
import sys
import butler as b

STATES = {"active", "completed", "deferred", "blocked"}


def validate_outcome(item):
    b.fields(item, {"account", "provider", "status", "reason", "evidence"})
    b.account_provider(item)
    b.require(item["status"] in STATES, "Unknown first-run outcome")
    b.text_value(item["reason"], "reason", 1000)
    b.text_value(item["evidence"], "evidence or direct-user decision", 2000)


def state_at(root):
    path = root / "onboarding.json"
    value = b.load_json(path) if path.exists() else {"revision": 0, "accounts": {}}
    b.fields(value, {"revision", "accounts"})
    b.integer(value["revision"], 0, 1_000_000, "onboarding revision")
    b.require(type(value["accounts"]) is dict, "Expected onboarding accounts")
    for key, item in value["accounts"].items():
        validate_outcome(item)
        b.require(key == item["provider"] + ":" + item["account"].casefold(), "Account record changed")
    return value


def check(root, accounts):
    b.require(type(accounts) is list and 1 <= len(accounts) <= 10, "Expected selected accounts")
    root = b.private_directory(root)
    with b.locked(root):
        value = state_at(root)
        results, seen = [], set()
        for account in accounts:
            b.fields(account, {"account", "provider"})
            b.account_provider(account)
            key = account["provider"] + ":" + account["account"].casefold()
            b.require(key not in seen, "Duplicate selected account")
            seen.add(key)
            results.append(value["accounts"].get(key, {**account, "status": "required"}))
        return {"revision": value["revision"], "accounts": results, "cleanup_addressed": all(r["status"] in {"completed", "deferred", "blocked"} for r in results), "cleanup_completed": all(r["status"] == "completed" for r in results), "mailbox_changed": False}


def record(root, request):
    b.fields(request, {"base_revision", "outcome"})
    b.integer(request["base_revision"], 0, 1_000_000, "base revision")
    validate_outcome(request["outcome"])
    root = b.private_directory(root)
    with b.locked(root):
        value = state_at(root)
        b.require(value["revision"] == request["base_revision"], "Onboarding changed; inspect before recording")
        item = request["outcome"]
        value["accounts"][item["provider"] + ":" + item["account"].casefold()] = item
        value["revision"] += 1
        b.atomic_json(root / "onboarding.json", value)
        b.require(state_at(root) == value, "Onboarding readback failed")
        return {"revision": value["revision"], "outcome": item, "mailbox_changed": False, "permission_granted": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "record"])
    parser.add_argument("--state-dir", type=Path, default=b.default_state_directory())
    parser.add_argument("--input", type=Path, required=True)
    args = parser.parse_args()
    try:
        payload = b.load_json(args.input)
        print(json.dumps(check(args.state_dir, payload) if args.command == "check" else record(args.state_dir, payload), indent=2))
        return 0
    except (b.ButlerError, OSError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error), "mailbox_changed": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
