#!/usr/bin/env python3
"""Propose, confirm and version private EmailButler changes; never call mail APIs."""
import argparse
import copy
import json
from pathlib import Path
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "setup-emailbutler" / "scripts"))
from private_state import ButlerError, account_provider, atomic_json, canonical, default_state_directory, digest, email, fields, integer, load_json, locked, no_symlinks, private_directory, require, text_value
from profiles import CHANGE_ID, initialize, organization, preferences, profile_at, rule, validate_profile


TYPES = {
    "preference.update", "organization.plan", "rule.plan",
    "workflow.revision", "access.change", "configuration.restore",
}


PROPOSAL_ONLY = {"workflow.revision", "access.change"}


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
