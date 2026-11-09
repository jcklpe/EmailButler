#!/usr/bin/env python3
"""Validate and initialize EmailButler profiles without changing existing settings."""
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "setup-emailbutler" / "scripts"))
from private_state import ButlerError, account_provider, atomic_json, canonical, default_state_directory, digest, email, fields, integer, load_json, locked, no_symlinks, private_directory, require, text_value


SLUG = re.compile(r"[a-z][a-z0-9-]{0,63}\Z")


CHANGE_ID = re.compile(r"C-[a-f0-9]{16}\Z")


PROFILE_KEYS = {
    "schema_version", "revision", "last_change_id", "preferences",
    "organization_plans", "rule_plans",
}


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


def profile_at(root):
    return validate_profile(load_json(root / "profile.json"))


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
