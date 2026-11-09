#!/usr/bin/env python3
"""Keep typed private observations and action receipts without changing policy or mail."""

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "setup-emailbutler" / "scripts"))
import private_state as b
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "customize-emailbutler" / "scripts"))
from profiles import profile_at


EVENT_ID = re.compile(r"E-[a-f0-9]{24}\Z")
ACTIONS = {"archive", "label", "move", "trash", "unsubscribe"}
STATUSES = {"verified", "reported", "uncertain", "failed"}


def validate_event(value):
    b.require(type(value) is dict, "Expected an event object")
    common = {"kind", "account", "provider", "message_id", "sender", "pattern"}
    if value.get("kind") == "action":
        b.fields(value, common | {"operation_id", "action", "status"}, {"result_message_id"})
        b.text_value(value["operation_id"], "stable operation identity", 200)
        b.require(value["action"] in ACTIONS and value["status"] in STATUSES, "Unknown action or status")
        if "result_message_id" in value:
            b.text_value(value["result_message_id"], "returned message identity", 1000)
    else:
        b.fields(value, common | {"signal", "source"})
        b.require(value["kind"] == "feedback", "Unknown event kind")
        b.require(value["signal"] in {"unwanted", "useful", "unsure"}, "Unknown feedback signal")
        b.require(value["source"] == "direct_user", "Feedback must come from the direct user")
    b.account_provider(value)
    b.email(value["sender"], "sender")
    b.text_value(value["message_id"], "stable message identity", 1000)
    b.text_value(value["pattern"], "specific recurring stream name", 100)
    return value


def read_entry(path):
    value = b.load_json(path)
    b.fields(value, {"id", "recorded_at", "event"})
    validate_event(value["event"])
    b.require(value["id"] == "E-" + b.digest(value["event"])[:24], "Journal entry content changed")
    b.require(path.name == value["id"] + ".json", "Journal filename does not match its identity")
    b.text_value(value["recorded_at"], "recorded time", 40)
    return value


def record(root, event):
    validate_event(event)
    root = b.private_directory(root)
    with b.locked(root):
        profile_at(root)
        folder = b.private_directory(root / "journal", create=True)
        event_id = "E-" + b.digest(event)[:24]
        path = folder / (event_id + ".json")
        if path.exists():
            existing = read_entry(path)
            b.require(existing["event"] == event, "Journal identity conflict")
            return {"status": "already_recorded", "entry": existing, "policy_changed": False, "mailbox_changed": False}
        entry = {"id": event_id, "recorded_at": datetime.now(timezone.utc).isoformat(), "event": event}
        b.atomic_json(path, entry)
        b.require(read_entry(path) == entry, "Saved observation could not be verified")
        return {"status": "recorded", "entry": entry, "policy_changed": False, "mailbox_changed": False}


def summarize(root):
    root = b.private_directory(root)
    with b.locked(root):
        profile_at(root)
        folder = root / "journal"
        if not folder.exists():
            return {"streams": [], "policy_changed": False, "mailbox_changed": False}
        b.private_directory(folder)
        groups = defaultdict(list)
        aliases = {}
        def identity(provider, account, message):
            key = (provider, account.casefold(), message)
            while key in aliases:
                key = aliases[key]
            return key
        entries = [read_entry(path) for path in sorted(folder.glob("*.json"))]
        for entry in entries:
            event = entry["event"]
            if event["kind"] == "action" and "result_message_id" in event and event["status"] == "verified":
                original = identity(event["provider"], event["account"], event["message_id"])
                returned = identity(event["provider"], event["account"], event["result_message_id"])
                if returned != original:
                    aliases[returned] = original
        for entry in entries:
            event = entry["event"]
            key = (event["provider"], event["account"].casefold(), event["sender"].casefold(), event["pattern"].casefold())
            groups[key].append(event)
        streams = []
        for key, events in sorted(groups.items()):
            counts = defaultdict(set)
            verified, unresolved = set(), {}
            for event in events:
                message = identity(event["provider"], event["account"], event["message_id"])
                if event["kind"] == "feedback":
                    counts[event["signal"]].add(message)
                else:
                    operation = (event["operation_id"], event["action"], message)
                    if event["status"] == "verified":
                        counts[event["action"]].add(message)
                        verified.add(operation)
                    elif event["status"] in {"reported", "uncertain"}:
                        if operation not in unresolved or event["status"] == "uncertain":
                            unresolved[operation] = event
            tallies = {name: len(counts[name]) for name in sorted(ACTIONS | {"unwanted", "useful", "unsure"})}
            ideas = []
            if not tallies["unsure"] and not tallies["unsubscribe"]:
                if not tallies["useful"] and (tallies["unwanted"] >= 3 or tallies["archive"] >= 3):
                    ideas.append("ask_keep_receiving_or_unsubscribe")
                if max(tallies["archive"], tallies["label"], tallies["move"]) >= 3:
                    ideas.append("review_narrow_native_rule")
            pending = [event for operation, event in sorted(unresolved.items()) if operation not in verified]
            streams.append({"provider": key[0], "account": key[1], "sender": key[2], "pattern": key[3], "counts": tallies, "unresolved_operations": len(pending), "pending_operations": pending, "suggestions": ideas})
        return {"streams": streams, "policy_changed": False, "mailbox_changed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["record", "summary"])
    parser.add_argument("--state-dir", type=Path, default=b.default_state_directory())
    parser.add_argument("--event", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "record":
            b.require(args.event is not None, "record requires --event")
            result = record(args.state_dir, b.load_json(args.event))
        else:
            result = summarize(args.state_dir)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (b.ButlerError, OSError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error), "policy_changed": False, "mailbox_changed": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
