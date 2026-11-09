#!/usr/bin/env python3
"""Classify known exposed connector tool names; no network or mailbox operations."""

import argparse
import json
from pathlib import Path
import sys

from private_state import ButlerError, fields, load_json, require, text_value, email as validate_email


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

# These describe the packaged adapter's wire contract, not provider-wide capability or permission.
CONTRACTS = {name: name + ".v1" for name in (
    "gmail.profile", "gmail.labels", "gmail.search", "gmail.read", "gmail.modify", "gmail.bulk_query",
    "outlook.profile", "outlook.list", "outlook.move", "outlook.categories",
)}


def fact(value, statuses):
    if value is None:
        return {"status": "unknown", "basis": "No evidence recorded"}
    fields(value, {"status", "basis"})
    require(value["status"] in statuses, "Unknown evidence status")
    text_value(value["basis"], "evidence basis", 1000)
    return dict(value)


def inspect_inventory(inventory):
    """Normalize saved schema/host evidence; never call tools or promote names to proof."""
    require(type(inventory) is dict, "Expected a saved inventory object")
    if "schema_version" not in inventory:
        hints = classify(inventory)
        return {
            "recorded_evidence_only": True,
            "live_verified": False,
            "platform": inventory["platform"],
            "host": {},
            "accounts": [{
                "key": account["key"],
                "provider": account["provider"],
                "account": None,
                "discovery_hints": {key:value for key,value in account.items() if key not in {"key", "provider"}},
                "identity": fact(None, ()),
                "access": fact(None, ()),
                "operations": {},
            } for account in hints["accounts"]],
        }
    fields(inventory, {"schema_version", "platform", "accounts"}, {"host"})
    require(type(inventory["schema_version"]) is int and inventory["schema_version"] == 1, "Unsupported capability evidence schema")
    require(inventory["platform"] in {"openai", "claude"}, "Unknown platform")
    accounts = inventory["accounts"]
    require(type(accounts) is list and 1 <= len(accounts) <= 20, "Expected 1–20 accounts")
    host = inventory.get("host", {})
    fields(host, set(), {"callable_tools", "process_io", "posix_shell"})
    host = {key: fact(host.get(key), {"available", "unavailable", "unknown"}) for key in ("callable_tools", "process_io", "posix_shell")}
    result, seen = [], set()
    for account in accounts:
        fields(account, {"key", "provider", "tools"}, {"account", "identity", "access", "operations"})
        # Retain the legacy classifier only as discovery hints.
        hint = classify({"platform": inventory["platform"], "accounts": [{key: account[key] for key in ("key", "provider", "tools")}]})["accounts"][0]
        require(account["key"] not in seen, "Duplicate account key")
        seen.add(account["key"])
        address = account.get("account")
        if address is not None:
            validate_email(address, "inspected account")
        identity = fact(account.get("identity"), {"confirmed", "mismatch", "unknown"})
        require(identity["status"] != "confirmed" or address is not None, "Confirmed identity needs its exact account address")
        operations = account.get("operations", {})
        require(type(operations) is dict and len(operations) <= len(CONTRACTS), "Invalid operation evidence")
        checked = {}
        for name, operation in operations.items():
            require(name in CONTRACTS and name.startswith(account["provider"] + "."), "Operation belongs to another provider or contract version")
            fields(operation, {"status", "basis"}, {"tool", "contract", "limits"})
            item = fact({key: operation[key] for key in ("status", "basis")}, {"exposed", "unavailable", "unknown"})
            if "tool" in operation:
                require(operation["tool"] in account["tools"], "Evidence names a tool absent from the saved inventory")
                item["tool"] = operation["tool"]
            require(item["status"] != "exposed" or "tool" in item, "Exposed operation needs its actual tool binding")
            contract = operation.get("contract")
            if contract is not None:
                text_value(contract, "inspected contract", 100)
            item["contract_matches"] = contract == CONTRACTS[name]
            limits = operation.get("limits", {})
            fields(limits, set(), {"max_items", "max_page_size"})
            for key, value in limits.items():
                require(type(value) is int and 1 <= value <= 1000, "Invalid inspected connector bound")
            item["limits"] = dict(limits)
            checked[name] = item
        result.append({
            "key": hint["key"],
            "provider": hint["provider"],
            "account": address,
            "discovery_hints": {key:value for key,value in hint.items() if key not in {"key", "provider"}},
            "identity": identity,
            "access": fact(account.get("access"), {"allowed", "denied", "unknown"}),
            "operations": checked,
        })
    return {"schema_version": 1, "recorded_evidence_only": True, "live_verified": False, "platform": inventory["platform"], "host": host, "accounts": result}


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
        value = load_json(args.inventory)
        print(json.dumps(inspect_inventory(value) if "schema_version" in value else classify(value), indent=2))
        return 0
    except (ButlerError, OSError, ValueError, TypeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
