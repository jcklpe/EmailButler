#!/usr/bin/env python3
"""Classify known exposed connector tool names; no network or mailbox operations."""

import argparse
import json
from pathlib import Path
import sys

from private_state import ButlerError, fields, load_json, require, text_value


MAP = {
    ("claude", "gmail"): {
        "search_threads": ["search"], "get_message": ["read"], "get_thread": ["read"],
        "list_labels": ["labels.list"], "create_label": ["labels.create"],
        "label_message": ["labels.apply"], "label_thread": ["labels.apply"],
        "unlabel_thread": ["archive"], "unmark_message_spam": ["spam.rescue"],
        "list_filters": ["rules.inspect"], "create_filter": ["rules.create"],
    },
    ("claude", "outlook"): {
        "outlook_email_search": ["search"], "read_resource": ["read"],
        "outlook_create_label": ["labels.create"], "outlook_modify_labels": ["labels.apply"],
        "outlook_create_filter": ["rules.create"], "outlook_delete_filter": ["rules.delete"],
    },
    ("openai", "gmail"): {
        "search_emails": ["search"], "search_email_ids": ["search"],
        "read_email": ["read"], "batch_read_email": ["read"],
        "list_labels": ["labels.list"], "create_label": ["labels.create"],
        "apply_labels_to_emails": ["labels.apply"], "batch_modify_email": ["labels.apply", "spam.rescue"],
        "archive_emails": ["archive"], "bulk_label_matching_emails": ["labels.backfill"],
    },
    ("openai", "outlook"): {
        "search_messages": ["search"], "list_messages": ["search"], "fetch_message": ["read"],
        "fetch_messages_batch": ["read"], "list_categories": ["labels.list"],
        "create_category": ["labels.create"], "set_message_categories": ["labels.apply"],
        "move_email": ["archive", "move", "spam.rescue"],
    },
}
REQUIREMENTS = {
    "review": {"search", "read"},
    "filing": {"search", "read", "labels.apply", "archive"},
    "native_rule_lifecycle": {"rules.inspect", "rules.create", "rules.delete"},
}


def classify(inventory):
    fields(inventory, {"platform", "accounts"})
    require(inventory["platform"] in ("claude", "openai"), "platform must be claude or openai")
    accounts = inventory["accounts"]
    require(type(accounts) is list and 1 <= len(accounts) <= 20, "accounts must contain 1–20 entries")
    result, seen = [], set()
    for account in accounts:
        fields(account, {"key", "provider", "tools"})
        text_value(account["key"], "account key", 100)
        require(account["key"] not in seen, "Duplicate account key")
        seen.add(account["key"])
        provider = account["provider"]
        require(provider in ("gmail", "outlook"), "provider must be gmail or outlook")
        require(type(account["tools"]) is list and len(account["tools"]) <= 500, "tools must be a bounded list")
        mapping = MAP[(inventory["platform"], provider)]
        operations, unknown = set(), []
        prefix = "mcp__codex_apps__" + ("gmail_" if provider == "gmail" else "microsoft_outlook_email_")
        for full_name in account["tools"]:
            text_value(full_name, "tool name", 200)
            name = full_name.removeprefix(prefix) if inventory["platform"] == "openai" else full_name
            if name in mapping:
                operations.update(mapping[name])
            else:
                unknown.append(full_name)
        result.append({
            "key": account["key"], "provider": provider,
            "known_exposed_operations": sorted(operations),
            "unknown_tools_to_inspect": sorted(set(unknown)),
            "checks": {key: {"known_names_present": needed <= operations, "missing_known_operations": sorted(needed - operations)} for key, needed in REQUIREMENTS.items()},
        })
    return {
        "evidence_level": "tool_name_inventory_only",
        "live_verified": False,
        "notice": "Inspect schemas and permissions. Missing known names are gaps to investigate, not proof of permanent absence. No mailbox action was tested.",
        "accounts": result,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True, type=Path)
    args = parser.parse_args()
    try:
        print(json.dumps(classify(load_json(args.inventory)), indent=2))
        return 0
    except (ButlerError, OSError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
