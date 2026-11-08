#!/usr/bin/env python3
"""Checkpoint bounded filing jobs; connector calls and current-session approval stay with the agent."""
import argparse
import json
from pathlib import Path
import re
import sys
import butler as b

JOB_ID = re.compile(r"B-[a-f0-9]{24}\Z")
STATES = {"pending", "verified", "reported", "uncertain", "failed"}


def validate_selection(value):
    b.fields(value, {"scope", "ids", "batch_size"})
    scope = value["scope"]
    b.fields(scope, {"account", "provider", "action", "source", "destination", "selection"})
    b.account_provider(scope)
    b.require(scope["action"] in {"move", "archive", "label"}, "Bulk jobs only support filing")
    for field in ("source", "destination", "selection"):
        b.text_value(scope[field], field, 1000)
    b.require(scope["destination"].casefold() not in {"trash", "deleted items", "junk", "junk email", "spam"}, "A filing job cannot use a deletion/junk destination")
    b.require(type(value["ids"]) is list and 1 <= len(value["ids"]) <= 1000, "Freeze 1–1000 identities per local job; larger approved selections can span several jobs")
    for identity in value["ids"]:
        b.text_value(identity, "message identity", 1000)
    b.require(len(set(value["ids"])) == len(value["ids"]), "Duplicate message identity")
    b.integer(value["batch_size"], 1, 100, "connector batch size")
    b.require(len(b.canonical(value)) <= 400_000, "Split this selection into smaller local jobs")
    return value


def load(root, job_id):
    b.require(type(job_id) is str and JOB_ID.fullmatch(job_id), "Invalid bulk job identity")
    value = b.load_json(root / "bulk" / (job_id + ".json"))
    b.fields(value, {"id", "selection", "revision", "results"})
    validate_selection(value["selection"])
    b.require(value["id"] == job_id == "B-" + b.digest(value["selection"])[:24], "Bulk scope changed")
    b.integer(value["revision"], 0, 1_000_000, "revision")
    b.require(type(value["results"]) is dict, "Expected item results")
    for index, result in value["results"].items():
        b.require(index.isdigit() and str(int(index)) == index and 0 <= int(index) < len(value["selection"]["ids"]), "Invalid result identity")
        validate_result(result)
    return value


def validate_result(result):
    b.fields(result, {"status", "evidence"}, {"result_id"})
    b.require(result["status"] in STATES, "Unknown operation result")
    b.text_value(result["evidence"], "result evidence", 1000)
    if "result_id" in result:
        b.text_value(result["result_id"], "returned message identity", 1000)


def view(value):
    counts = {state: 0 for state in STATES}
    for index in range(len(value["selection"]["ids"])):
        counts[value["results"].get(str(index), {}).get("status", "pending")] += 1
    blocked = any(counts[state] for state in ("reported", "uncertain", "failed"))
    pending = [index for index in range(len(value["selection"]["ids"])) if value["results"].get(str(index), {}).get("status", "pending") == "pending"]
    batch = [] if blocked else [{"id": value["selection"]["ids"][index], "current_id": value["results"].get(str(index), {}).get("result_id", value["selection"]["ids"][index])} for index in pending[:value["selection"]["batch_size"]]]
    return {"id": value["id"], "revision": value["revision"], "scope": value["selection"]["scope"], "counts": counts, "selected": len(value["selection"]["ids"]), "status": "blocked" if blocked else ("complete" if not pending else "pending"), "next_batch": batch, "mailbox_changed": False, "permission_granted": False}


def save(root, value):
    b.atomic_json(root / "bulk" / (value["id"] + ".json"), value)
    b.require(load(root, value["id"]) == value, "Bulk checkpoint readback failed")


def create(root, selection):
    validate_selection(selection)
    root = b.private_directory(root)
    with b.locked(root):
        b.private_directory(root / "bulk", create=True)
        job_id = "B-" + b.digest(selection)[:24]
        if (root / "bulk" / (job_id + ".json")).exists():
            return view(load(root, job_id))
        value = {"id": job_id, "selection": selection, "revision": 0, "results": {}}
        save(root, value)
        return view(value)


def status(root, job_id):
    root = b.private_directory(root)
    with b.locked(root):
        return view(load(root, job_id))


def record(root, job_id, request, reconcile=False):
    b.fields(request, {"base_revision", "results"})
    b.integer(request["base_revision"], 0, 1_000_000, "base revision")
    b.require(type(request["results"]) is list and request["results"], "Expected operation results")
    root = b.private_directory(root)
    with b.locked(root):
        value = load(root, job_id)
        b.require(value["revision"] == request["base_revision"], "Bulk job changed; inspect its checkpoint")
        before = view(value)
        expected = {item["id"] for item in before["next_batch"]}
        supplied = set()
        for item in request["results"]:
            b.fields(item, {"id", "status", "evidence"}, {"result_id"})
            b.text_value(item["id"], "original message identity", 1000)
            b.require(item["id"] in value["selection"]["ids"] and item["id"] not in supplied, "Unknown or duplicate result")
            supplied.add(item["id"])
            index = str(value["selection"]["ids"].index(item["id"]))
            old = value["results"].get(index, {})
            if reconcile:
                b.require(old.get("status") in {"reported", "uncertain", "failed"}, "Only unresolved results can be reconciled")
                b.require(item["status"] in {"pending", "verified", "failed"}, "Reconcile through a fresh read before retrying")
            else:
                b.require(item["id"] in expected and item["status"] != "pending", "Result is outside the current connector batch")
            result = {key: val for key, val in item.items() if key != "id"}
            if "result_id" not in result and "result_id" in old:
                result["result_id"] = old["result_id"]
            validate_result(result)
            value["results"][index] = result
        if not reconcile:
            b.require(supplied == expected, "Record every item in the current batch, including uncertain outcomes")
        value["revision"] += 1
        save(root, value)
        return view(value)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["create", "status", "record", "reconcile"])
    parser.add_argument("--state-dir", type=Path, default=b.default_state_directory())
    parser.add_argument("--id")
    parser.add_argument("--input", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "status":
            result = status(args.state_dir, args.id)
        else:
            b.require(args.input is not None, "This command requires --input")
            payload = b.load_json(args.input)
            result = create(args.state_dir, payload) if args.command == "create" else record(args.state_dir, args.id, payload, args.command == "reconcile")
        print(json.dumps(result, indent=2))
        return 0
    except (b.ButlerError, OSError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error), "mailbox_changed": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
