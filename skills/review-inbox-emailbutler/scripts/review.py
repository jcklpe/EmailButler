#!/usr/bin/env python3
"""Save a bounded review queue and resume one small slate at a time; never fetch or mutate mail."""

import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "setup-emailbutler" / "scripts"))
import private_state as b
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "customize-emailbutler" / "scripts"))
from profiles import profile_at


QUEUE_ID = re.compile(r"Q-[a-f0-9]{24}\Z")
OUTCOMES = {"action_verified", "kept_visible", "deferred"}


def timestamp(value):
    b.text_value(value, "timestamp", 40)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    b.require(parsed.tzinfo is not None, "Timestamps need a timezone")
    return parsed


def unsubscribe(value):
    b.require(type(value) is dict, "Expected unsubscribe metadata")
    if value.get("status") == "available":
        b.fields(value, {"status", "url", "kind", "description"})
        b.text_value(value["url"], "sender-provided unsubscribe link", 2000)
        b.text_value(value["description"], "link type and relevance", 400)
        b.require(value["kind"] in {"list_unsubscribe", "preferences", "body_unsubscribe", "mailto"}, "Unknown unsubscribe link kind")
        parsed = urlsplit(value["url"])
        if value["kind"] == "mailto":
            b.require(parsed.scheme == "mailto", "A mailto option needs a mailto link")
            b.email(parsed.path, "unsubscribe recipient")
        else:
            b.require(parsed.scheme in {"https", "http"} and bool(parsed.hostname) and parsed.username is None and parsed.password is None, "Expected a sender-provided web link")
    else:
        b.fields(value, {"status"})
        b.require(value["status"] in {"not_found", "unavailable", "not_applicable", "unsafe"}, "Unknown unsubscribe status")


def validate_input(value):
    b.fields(value, {"scope", "items"})
    b.require(type(value["scope"]) is list and 1 <= len(value["scope"]) <= 10, "Expected 1–10 account scopes")
    accounts = set()
    for scope in value["scope"]:
        b.fields(scope, {"account", "provider", "folder", "after", "before", "complete"})
        b.account_provider(scope)
        b.require(scope["folder"] == "inbox", "Review queues cover Inbox mail")
        b.require(timestamp(scope["after"]) < timestamp(scope["before"]), "Invalid review time range")
        b.require(type(scope["complete"]) is bool, "complete must be a boolean")
        key = (scope["provider"], scope["account"].casefold())
        b.require(key not in accounts, "Duplicate account scope")
        accounts.add(key)
    b.require(type(value["items"]) is list and len(value["items"]) <= 200, "A queue holds at most 200 selected messages; use bounded provider searches for a larger backlog")
    seen = set()
    for item in value["items"]:
        b.fields(item, {"account", "provider", "id", "sender", "subject", "received_at", "body_read"}, {"link", "read_state", "starred", "flagged", "unsubscribe"})
        b.account_provider(item)
        b.require((item["provider"], item["account"].casefold()) in accounts, "Message account is outside this queue")
        b.text_value(item["id"], "message identity", 1000)
        b.email(item["sender"], "sender")
        b.text_value(item["subject"], "recognizable subject", 500)
        received = timestamp(item["received_at"])
        scope = next(s for s in value["scope"] if (s["provider"], s["account"].casefold()) == (item["provider"], item["account"].casefold()))
        b.require(timestamp(scope["after"]) <= received < timestamp(scope["before"]), "Message falls outside the frozen time range")
        b.require(type(item["body_read"]) is bool, "body_read must be a boolean")
        if "link" in item:
            b.text_value(item["link"], "provider source link", 2000)
            b.require(item["link"].startswith("https://"), "Only provider-returned HTTPS source links belong here")
        if "read_state" in item:
            b.require(item["read_state"] in {"read", "unread", "unknown"}, "Unknown read state")
        for marker in ("starred", "flagged"):
            if marker in item:
                b.require(type(item[marker]) is bool or item[marker] is None, "Star/flag state must be boolean or unknown (null)")
        if "unsubscribe" in item:
            unsubscribe(item["unsubscribe"])
        key = (item["provider"], item["account"].casefold(), item["id"])
        b.require(key not in seen, "Duplicate message identity")
        seen.add(key)
    return value


def queue_at(root, queue_id):
    b.require(type(queue_id) is str and QUEUE_ID.fullmatch(queue_id), "Invalid queue identity")
    value = b.load_json(root / "reviews" / (queue_id + ".json"))
    b.fields(value, {"id", "input", "batch_size", "revision", "cursor", "pages"})
    validate_input(value["input"])
    b.integer(value["batch_size"], 1, 20, "batch size")
    seed = {"input": value["input"], "batch_size": value["batch_size"]}
    b.require(value["id"] == queue_id == "Q-" + b.digest(seed)[:24], "Queue input changed")
    b.integer(value["cursor"], 0, len(value["input"]["items"]), "cursor")
    b.integer(value["revision"], 0, 200, "queue revision")
    b.require(type(value["pages"]) is list and len(value["pages"]) == value["revision"], "Invalid queue history")
    cursor = 0
    for page in value["pages"]:
        validate_decisions(page, cursor, value)
        cursor += len(page)
    b.require(cursor == value["cursor"], "Queue cursor and decisions differ")
    return value


def rows(value):
    start = value["cursor"]
    return [{"row": "R" + str(index + 1), **item} for index, item in enumerate(value["input"]["items"][start:start + value["batch_size"]], start)]


def validate_decisions(decisions, start, value):
    b.require(type(decisions) is list, "Expected a decision list")
    end = min(start + value["batch_size"], len(value["input"]["items"]))
    expected = {"R" + str(index + 1) for index in range(start, end)}
    b.require(bool(expected) and len(decisions) == len(expected), "Resolve or explicitly defer the whole current slate before advancing")
    actual = set()
    for decision in decisions:
        b.fields(decision, {"row", "outcome"})
        b.require(decision["outcome"] in OUTCOMES, "Pending, failed, or uncertain actions cannot be marked complete; inspect or explicitly defer them")
        b.require(type(decision["row"]) is str and decision["row"] in expected, "Decision is outside the current slate")
        actual.add(decision["row"])
    b.require(actual == expected, "Missing or duplicate row decision")


def open_queue(root, source, batch_size=None):
    validate_input(source)
    root = b.private_directory(root)
    with b.locked(root):
        default_size = profile_at(root)["preferences"]["batch_size"]
        batch_size = default_size if batch_size is None else batch_size
        b.integer(batch_size, 1, 20, "one-off batch size")
        seed = {"input": source, "batch_size": batch_size}
        queue_id = "Q-" + b.digest(seed)[:24]
        folder = b.private_directory(root / "reviews", create=True)
        path = folder / (queue_id + ".json")
        if path.exists():
            value = queue_at(root, queue_id)
        else:
            value = {"id": queue_id, **seed, "revision": 0, "cursor": 0, "pages": []}
            b.atomic_json(path, value)
            b.require(queue_at(root, queue_id) == value, "Queue save could not be verified")
        return slate(value)


def slate(value):
    return {"id": value["id"], "revision": value["revision"], "scope": value["input"]["scope"], "rows": rows(value), "reviewed": value["cursor"], "selected": len(value["input"]["items"]), "remaining_selected": len(value["input"]["items"]) - value["cursor"], "mailbox_changed": False}


def peek(root, queue_id):
    root = b.private_directory(root)
    with b.locked(root):
        return slate(queue_at(root, queue_id))


def list_queues(root):
    root = b.private_directory(root)
    with b.locked(root):
        folder = root / "reviews"
        if not folder.exists():
            return {"queues": [], "mailbox_changed": False}
        b.private_directory(folder)
        queues = []
        for path in sorted(folder.glob("*.json")):
            value = queue_at(root, path.stem)
            deferred_count = sum(decision["outcome"] == "deferred" for page in value["pages"] for decision in page)
            queues.append({"id": value["id"], "scope": value["input"]["scope"], "reviewed": value["cursor"], "selected": len(value["input"]["items"]), "complete": value["cursor"] == len(value["input"]["items"]), "deferred_rows": deferred_count})
        return {"queues": queues, "mailbox_changed": False}


def deferred(root, queue_id, offset=0):
    root = b.private_directory(root)
    with b.locked(root):
        value = queue_at(root, queue_id)
        labels = {decision["row"] for page in value["pages"] for decision in page if decision["outcome"] == "deferred"}
        items = [{"row": "R" + str(index + 1), **item} for index, item in enumerate(value["input"]["items"]) if "R" + str(index + 1) in labels]
        b.integer(offset, 0, len(items), "deferred offset")
        end = min(offset + value["batch_size"], len(items))
        return {"id": queue_id, "revision": value["revision"], "rows": items[offset:end], "recorded_deferrals": len(items), "next_offset": end if end < len(items) else None, "mailbox_changed": False}


def finish(root, queue_id, request):
    b.fields(request, {"base_revision", "decisions"})
    b.integer(request["base_revision"], 0, 200, "base revision")
    root = b.private_directory(root)
    with b.locked(root):
        value = queue_at(root, queue_id)
        b.require(request["base_revision"] == value["revision"], "Queue changed; inspect the current slate")
        validate_decisions(request["decisions"], value["cursor"], value)
        value["pages"].append(request["decisions"])
        value["cursor"] += len(request["decisions"])
        value["revision"] += 1
        b.atomic_json(root / "reviews" / (queue_id + ".json"), value)
        b.require(queue_at(root, queue_id) == value, "Queue progress could not be verified")
        return {"id": queue_id, "revision": value["revision"], "reviewed": value["cursor"], "selected": len(value["input"]["items"]), "status": "complete" if value["cursor"] == len(value["input"]["items"]) else "paused", "mailbox_changed": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["open", "list", "peek", "deferred", "finish"])
    parser.add_argument("--state-dir", type=Path, default=b.default_state_directory())
    parser.add_argument("--input", type=Path)
    parser.add_argument("--id")
    parser.add_argument("--request", type=Path)
    parser.add_argument("--batch-size", type=int)
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args()
    try:
        if args.command == "open":
            b.require(args.input is not None, "open requires --input")
            result = open_queue(args.state_dir, b.load_json(args.input), args.batch_size)
        elif args.command == "peek":
            result = peek(args.state_dir, args.id)
        elif args.command == "list":
            result = list_queues(args.state_dir)
        elif args.command == "deferred":
            result = deferred(args.state_dir, args.id, args.offset)
        else:
            b.require(args.request is not None, "finish requires --request")
            result = finish(args.state_dir, args.id, b.load_json(args.request))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (b.ButlerError, OSError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error), "mailbox_changed": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
