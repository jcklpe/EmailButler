#!/usr/bin/env python3
"""Validate and version private EmailButler configuration; never call mail APIs."""

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone


class ButlerError(ValueError):
    pass


EMAIL = re.compile(r"[^\s@*?<>]+@[^\s@*?<>]+\.[^\s@*?<>]+\Z")
SLUG = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")
CHANGE_ID = re.compile(r"C-[a-f0-9]{16}\Z")
TYPES = {
    "preference.update", "organization.plan", "rule.plan",
    "workflow.revision", "access.change", "configuration.restore",
}
PROPOSAL_ONLY = {"workflow.revision", "access.change"}
PROFILE_KEYS = {
    "schema_version", "revision", "last_change_id", "preferences",
    "organization_plans", "rule_plans",
}


def require(condition, message):
    if not condition:
        raise ButlerError(message)


def fields(value, required, optional=()):
    require(type(value) is dict, "Expected an object")
    require(set(required) <= value.keys(), "Missing required fields: " + ", ".join(sorted(set(required) - value.keys())))
    require(value.keys() <= set(required) | set(optional), "Unknown fields: " + ", ".join(sorted(value.keys() - set(required) - set(optional))))


def text_value(value, name, limit=1000):
    require(type(value) is str and 0 < len(value.strip()) <= limit, f"{name} must be nonempty text of at most {limit} characters")
    require(not any(ord(c) < 32 for c in value), f"{name} cannot contain control characters")


def email(value, name="email"):
    require(type(value) is str and len(value) <= 254 and EMAIL.fullmatch(value) is not None, f"{name} must be a complete email address without wildcards")


def integer(value, low, high, name):
    require(type(value) is int and low <= value <= high, f"{name} must be an integer from {low} to {high}")


def preferences(value, partial=False):
    keys = {"batch_size", "detail", "priority_senders"}
    fields(value, () if partial else keys, keys if partial else ())
    require(bool(value), "Preference changes cannot be empty")
    if "batch_size" in value:
        integer(value["batch_size"], 1, 20, "batch_size")
    if "detail" in value:
        require(value["detail"] in ("brief", "expanded"), "detail must be brief or expanded")
    if "priority_senders" in value:
        senders = value["priority_senders"]
        require(type(senders) is list and len(senders) <= 50, "priority_senders must be a list of at most 50 addresses")
        for sender in senders:
            email(sender, "priority sender")
        require(len({s.casefold() for s in senders}) == len(senders), "Duplicate priority sender")


def account_provider(value):
    email(value["account"], "account")
    require(value["provider"] in ("gmail", "outlook"), "provider must be gmail or outlook")


def organization(value):
    fields(value, {"account", "provider"}, {"labels", "folders"})
    account_provider(value)
    total = 0
    for key in ("labels", "folders"):
        entries = value.get(key, [])
        require(type(entries) is list and len(entries) <= 20, f"{key} must be a list of at most 20 entries")
        require(key != "folders" or not entries or value["provider"] == "outlook", "Use labels for Gmail organization; folders are Outlook plans")
        names = []
        for entry in entries:
            fields(entry, {"name", "purpose"})
            text_value(entry["name"], key + " name", 100)
            text_value(entry["purpose"], key + " purpose", 500)
            names.append(entry["name"].casefold())
        require(len(set(names)) == len(names), "Duplicate " + key + " names")
        total += len(entries)
    require(1 <= total <= 20, "An organization plan needs 1–20 labels/folders in total")


def rule(value):
    fields(value, {"id", "account", "provider", "match", "action"})
    require(type(value["id"]) is str and SLUG.fullmatch(value["id"]) is not None, "Rule id must be a lowercase slug")
    account_provider(value)
    fields(value["match"], {"sender"}, {"recipient", "subject_contains"})
    email(value["match"]["sender"], "sender")
    if "recipient" in value["match"]:
        email(value["match"]["recipient"], "recipient")
    if "subject_contains" in value["match"]:
        text_value(value["match"]["subject_contains"], "subject condition", 200)
    fields(value["action"], {"archive"}, {"label", "folder"})
    require("label" in value["action"] or "folder" in value["action"], "Rule action needs a label or folder")
    if "label" in value["action"]:
        text_value(value["action"]["label"], "rule label", 100)
        require(value["action"]["label"].strip().casefold() not in {"trash", "spam"}, "Rule labels cannot target Trash or Spam")
    if "folder" in value["action"]:
        text_value(value["action"]["folder"], "rule folder", 100)
        require(value["provider"] == "outlook", "Gmail filing uses label plus archive, not folder moves")
        require(value["action"]["folder"].strip().casefold() not in {"trash", "deleted items", "deleteditems", "junk", "junk email", "junkemail", "spam"}, "Filing rules cannot target known Trash/Junk destinations")
        require(value["action"]["archive"] is False, "A specific folder and Archive are conflicting destinations")
    require(type(value["action"]["archive"]) is bool, "archive must be a boolean")
    require(not (value["action"]["archive"] or "folder" in value["action"]) or "subject_contains" in value["match"], "Out-of-Inbox filing requires a sender and a subject condition")


def validate_profile(value):
    fields(value, PROFILE_KEYS)
    require(type(value["schema_version"]) is int and value["schema_version"] == 1, "Unsupported profile schema")
    integer(value["revision"], 0, 2**53 - 1, "revision")
    require(value["last_change_id"] is None or (type(value["last_change_id"]) is str and CHANGE_ID.fullmatch(value["last_change_id"]) is not None), "Invalid last_change_id")
    preferences(value["preferences"])
    for key, validator in (("organization_plans", organization), ("rule_plans", rule)):
        require(type(value[key]) is dict and len(value[key]) <= 100, f"{key} must be an object of at most 100 plans")
        for name, plan in value[key].items():
            validator(plan)
            expected = plan["account"].casefold() if key == "organization_plans" else plan["account"].casefold() + ":" + plan["id"]
            require(name == expected, "Plan key does not match its account/id")
    return value


def validate_request(value):
    fields(value, {"type", "base_revision", "reason", "payload"})
    require(type(value["type"]) is str and value["type"] in TYPES, "Unknown change type")
    integer(value["base_revision"], 0, 2**53 - 1, "base_revision")
    text_value(value["reason"], "reason", 2000)
    payload = value["payload"]
    kind = value["type"]
    if kind == "preference.update":
        fields(payload, {"changes"})
        preferences(payload["changes"], partial=True)
    elif kind == "organization.plan":
        organization(payload)
    elif kind == "rule.plan":
        rule(payload)
    elif kind == "configuration.restore":
        fields(payload, {"revision"})
        integer(payload["revision"], 0, 2**53 - 1, "restore revision")
    elif kind == "workflow.revision":
        fields(payload, {"request", "examples"})
        text_value(payload["request"], "workflow request", 4000)
        require(type(payload["examples"]) is list and 1 <= len(payload["examples"]) <= 20, "Workflow revision needs 1–20 examples")
        for example in payload["examples"]:
            fields(example, {"input", "expected"})
            text_value(example["input"], "example input", 2000)
            text_value(example["expected"], "example expectation", 2000)
    else:
        fields(payload, {"account", "provider", "requested_capability", "purpose"})
        account_provider(payload)
        text_value(payload["requested_capability"], "requested capability", 200)
        text_value(payload["purpose"], "access purpose", 1000)
    return value


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def no_symlinks(path):
    path = Path(os.path.abspath(path))
    for candidate in (path, *path.parents):
        require(not candidate.is_symlink(), "State/input paths cannot contain symlinks")
    return path


def load_json(path):
    path = no_symlinks(path)
    require(path.is_file() and path.stat().st_size <= 1_000_000, "Expected a JSON file of at most 1 MB")
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            require(key not in result, "Duplicate JSON key: " + key)
            result[key] = value
        return result
    with path.open(encoding="utf-8") as stream:
        return json.load(stream, object_pairs_hook=unique_pairs)


def atomic_json(path, value):
    path = no_symlinks(path)
    encoded = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    require(len(encoded.encode("utf-8")) <= 1_000_000, "State file exceeds the 1 MB read/write limit")
    fd, temporary = tempfile.mkstemp(prefix=".butler-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def private_directory(path, create=False):
    path = no_symlinks(path)
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    require(path.is_dir(), "Private state directory is missing; run init")
    if os.name == "posix":
        require(path.stat().st_mode & 0o077 == 0, "State directory must be private (owner-only permissions)")
    return path


@contextmanager
def locked(root):
    lock = no_symlinks(root / ".lock")
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise ButlerError("State is locked; inspect the active writer instead of deleting its lock") from None
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def profile_at(root):
    return validate_profile(load_json(root / "profile.json"))


def default_state_directory():
    return Path.home() / ".email-butler"


def initialize(root):
    root = private_directory(root, create=True)
    with locked(root):
        for folder in ("pending", "revisions"):
            private_directory(root / folder, create=True)
        if (root / "profile.json").exists():
            return {"status": "existing", "profile": profile_at(root)}
        default = Path(__file__).resolve().parent.parent / "assets" / "default-profile.json"
        value = validate_profile(load_json(default))
        atomic_json(root / "profile.json", value)
        return {"status": "initialized", "profile": value}


def changed_profile(root, current, request):
    value = copy.deepcopy(current)
    kind, payload = request["type"], request["payload"]
    if kind == "preference.update":
        value["preferences"].update(payload["changes"])
    elif kind == "organization.plan":
        value["organization_plans"][payload["account"].casefold()] = payload
    elif kind == "rule.plan":
        value["rule_plans"][payload["account"].casefold() + ":" + payload["id"]] = payload
    elif kind == "configuration.restore":
        require(payload["revision"] < current["revision"], "Restore requires an earlier revision")
        value = copy.deepcopy(validate_profile(load_json(root / "revisions" / f'{payload["revision"]}.json')))
    value["revision"] = current["revision"] + 1
    value["last_change_id"] = None
    return validate_profile(value)


def propose(root, request, user_request):
    normalized = " ".join(user_request.strip().casefold().split())
    require(re.match(r"^change my email butler(?:\b)", normalized) is not None, "A direct user request must begin with change my email butler")
    validate_request(request)
    root = private_directory(root)
    with locked(root):
        current = profile_at(root)
        require(request["base_revision"] == current["revision"], "Stale base revision; prepare a fresh proposal")
        candidate = changed_profile(root, current, request)
        body = {"request": request, "base_digest": digest(current), "candidate": candidate}
        proposal_id = "C-" + digest(body)[:16]
        proposal = {"id": proposal_id, **body}
        pending = private_directory(root / "pending") / f"{proposal_id}.json"
        if pending.exists():
            require(load_json(pending) == proposal, "Existing proposal differs; refuse overwrite")
        else:
            atomic_json(pending, proposal)
        return {"status": "proposal_only" if request["type"] in PROPOSAL_ONLY else "pending", "id": proposal_id, "current": current, "proposed": candidate, "request": request, "mailbox_changed": False}


def read_proposal(root, proposal_id):
    require(type(proposal_id) is str and CHANGE_ID.fullmatch(proposal_id) is not None, "Invalid change ID")
    value = load_json(root / "pending" / f"{proposal_id}.json")
    fields(value, {"id", "request", "base_digest", "candidate"})
    body = {key: value[key] for key in ("request", "base_digest", "candidate")}
    require(value["id"] == proposal_id == "C-" + digest(body)[:16], "Proposal changed; approval no longer matches")
    validate_request(value["request"])
    validate_profile(value["candidate"])
    return value


def apply(root, proposal_id, approval):
    require(approval.strip().casefold() == "apply change " + proposal_id.casefold(), "Activation must identify the exact change ID")
    root = private_directory(root)
    with locked(root):
        proposal = read_proposal(root, proposal_id)
        request = proposal["request"]
        require(request["type"] not in PROPOSAL_ONLY, "This change requires separate implementation/authorization; it remains proposal-only")
        current = profile_at(root)
        if current["last_change_id"] == proposal_id:
            return {"status": "already_applied", "profile": current, "mailbox_changed": False}
        require(digest(current) == proposal["base_digest"] and current["revision"] == request["base_revision"], "Profile changed; prepare a fresh proposal and review")
        candidate = changed_profile(root, current, request)
        require(candidate == proposal["candidate"], "Candidate no longer matches the approved proposal")
        candidate["last_change_id"] = proposal_id
        history = private_directory(root / "revisions") / f'{current["revision"]}.json'
        if history.exists():
            require(load_json(history) == current, "Revision history conflict")
        else:
            atomic_json(history, current)
        atomic_json(root / "profile.json", candidate)
        saved = profile_at(root)
        require(saved == candidate, "Saved profile could not be verified")
        return {"status": "applied", "profile": saved, "mailbox_changed": False}


def approve_request(root, proposal_id):
    root = private_directory(root)
    with locked(root):
        proposal = read_proposal(root, proposal_id)
        request = proposal["request"]
        require(request["type"] in PROPOSAL_ONLY, "Only implementation/access requests use this route")
        current = profile_at(root)
        require(digest(current) == proposal["base_digest"] and current["revision"] == request["base_revision"], "Settings changed; prepare a fresh request preview")
        body = {"id": proposal_id, "request": request, "base_digest": proposal["base_digest"]}
        path = private_directory(root / "approvals", create=True) / (proposal_id + ".json")
        if path.exists():
            receipt = load_json(path)
            fields(receipt, set(body) | {"approved_at"})
            require({key: receipt[key] for key in body} == body, "Approved request differs from the reviewed request")
            status = "already_approved_request"
        else:
            receipt = {**body, "approved_at": datetime.now(timezone.utc).isoformat()}
            atomic_json(path, receipt)
            require(load_json(path) == receipt, "Request approval could not be verified")
            status = "request_approved"
        return {"status": status, "request": request, "profile_changed": False, "implemented": False, "permission_granted": False, "mailbox_changed": False}


def confirm(root, reviewed, reply):
    fields(reviewed, {"choices"})
    choices = reviewed["choices"]
    require(type(choices) is list and 1 <= len(choices) <= 20, "Expected the exact choices shown in the current change preview")
    labels, ids = {}, set()
    for choice in choices:
        fields(choice, {"label", "id"}, {"title"})
        require(type(choice["label"]) is str and re.fullmatch(r"B[1-9][0-9]{0,2}", choice["label"]), "Change labels must be short B labels, such as B1")
        require(type(choice["id"]) is str and CHANGE_ID.fullmatch(choice["id"]), "Invalid internal change identity")
        require(choice["label"] not in labels and choice["id"] not in ids, "Duplicate change label or identity")
        if "title" in choice:
            text_value(choice["title"], "displayed change title", 120)
        labels[choice["label"]] = choice["id"]
        ids.add(choice["id"])
    require(type(reply) is str, "Expected the direct user's confirmation")
    normalized = " ".join(reply.strip().casefold().split()).rstrip(".!").strip()
    require(normalized not in {"no", "no thanks", "not now", "cancel", "stop"} and not normalized.endswith("?"), "The reply does not confirm a change")
    if normalized in {"apply this change", "apply it", "yes", "yes, apply it", "go ahead", "yes please", "yes, please", "sure", "do that", "please apply this change"}:
        require(len(labels) == 1, "Several changes were shown; ask which short label the user wants to apply")
        selected = next(iter(labels.values()))
    else:
        match = re.fullmatch(r"(?:apply|approve) (b[1-9][0-9]{0,2})", normalized)
        ordinal = re.fullmatch(r"(?:(?:apply|approve|use|choose|go with) )?(?:the )?(first|second|third|fourth|fifth|[1-9]|1[0-9]|20)(?: one| option| change)?", normalized)
        if match is not None:
            require(match[1].upper() in labels, "That choice was not in the current preview")
            selected = labels[match[1].upper()]
        elif ordinal is not None:
            word = ordinal[1]
            numbers = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5}
            number = numbers[word] if word in numbers else int(word)
            require(number <= len(choices), "That choice was not in the current preview")
            selected = choices[number - 1]["id"]
        else:
            title = re.sub(r"^(?:apply|approve|use|choose|go with) (?:the )?", "", normalized)
            title = re.sub(r"^the ", "", title)
            title = re.sub(r" (?:option|change)$", "", title)
            matches = [choice for choice in choices if "title" in choice and " ".join(choice["title"].strip().casefold().split()) == title]
            require(len(matches) == 1, "Ask which displayed change the user means")
            selected = matches[0]["id"]
    if read_proposal(private_directory(root), selected)["request"]["type"] in PROPOSAL_ONLY:
        return approve_request(root, selected)
    return apply(root, selected, "apply change " + selected)


def change_history(root, limit=20):
    root = private_directory(root)
    integer(limit, 1, 100, "history limit")
    with locked(root):
        current = profile_at(root)
        entries = []
        def revision(number):
            return current if number == current["revision"] else validate_profile(load_json(root / "revisions" / f"{number}.json"))
        for number in range(max(1, current["revision"] - limit + 1), current["revision"] + 1):
            after = revision(number)
            require(after["revision"] == number, "Revision history has an inconsistent revision")
            proposal = read_proposal(root, after["last_change_id"])
            candidate = copy.deepcopy(proposal["candidate"])
            candidate["last_change_id"] = proposal["id"]
            before = revision(number - 1)
            require(candidate == after and digest(before) == proposal["base_digest"], "Change history does not match saved revisions")
            entries.append({"revision": number, "id": proposal["id"], "type": proposal["request"]["type"], "reason": proposal["request"]["reason"], "before": before, "after": after})
        return {"revision": current["revision"], "changes": entries, "older_changes": max(0, current["revision"] - len(entries)), "mailbox_changed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "show", "propose", "apply", "confirm", "inspect", "history", "location"):
        command = sub.add_parser(name)
        command.add_argument("--state-dir", type=Path, default=default_state_directory())
        if name == "propose":
            command.add_argument("--request", type=Path, required=True)
            command.add_argument("--user-request", required=True)
        if name in ("apply", "inspect"):
            command.add_argument("--id", required=True)
        if name == "apply":
            command.add_argument("--approval", required=True)
        if name == "confirm":
            command.add_argument("--reviewed", type=Path, required=True)
            command.add_argument("--reply", required=True)
        if name == "history":
            command.add_argument("--limit", type=int, default=20)
    args = parser.parse_args(argv)
    try:
        if args.command == "location":
            result = {"state_dir": str(no_symlinks(args.state_dir)), "exists": args.state_dir.is_dir(), "mailbox_changed": False}
        elif args.command == "init":
            result = initialize(args.state_dir)
        elif args.command == "propose":
            result = propose(args.state_dir, load_json(args.request), args.user_request)
        elif args.command == "apply":
            result = apply(args.state_dir, args.id, args.approval)
        elif args.command == "confirm":
            result = confirm(args.state_dir, load_json(args.reviewed), args.reply)
        elif args.command == "inspect":
            result = read_proposal(private_directory(args.state_dir), args.id)
        elif args.command == "history":
            result = change_history(args.state_dir, args.limit)
        else:
            result = profile_at(private_directory(args.state_dir))
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except (ButlerError, OSError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error), "mailbox_changed": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
