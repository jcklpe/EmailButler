#!/usr/bin/env python3
"""Plan bulk routes and connector-call budgets from saved evidence only; no dispatch or writes."""
import argparse
import json
from pathlib import Path
import re
import sys
import time

import bulk
import capabilities
import private_state as b
from connector_adapters import adapter, groups_by_size
from execute_bulk import account_state


def operation(account, name):
    item = account["operations"].get(name, {})
    if item.get("status") == "unavailable":
        return "unavailable"
    return "supported" if item.get("status") == "exposed" and item.get("contract_matches") else "unknown"


def frozen_workload(root, run_id):
    """Read an existing execution manifest and jobs without locking, creating or changing files."""
    b.require(type(run_id) is str and re.fullmatch(r"R-[a-f0-9]{32}", run_id), "Invalid frozen run identity")
    root = b.private_directory(root)
    folder = root / "bulk-runs" / run_id
    snapshots = []
    def read(path):
        value = b.load_json(path)
        snapshots.append((path, value))
        return value
    plan = read(folder / "plan.json")
    b.fields(plan, {"schema_version", "scope", "jobs"})
    b.require(type(plan["schema_version"]) is int and plan["schema_version"] == 1 and run_id == "R-" + b.digest(plan)[:32], "Frozen plan changed")
    b.require(type(plan["jobs"]) is list and 1 <= len(plan["jobs"]) <= 1000, "Invalid frozen jobs")
    claims = read(root / "bulk-runs" / "claims.json")
    b.require(type(claims) is dict, "Invalid frozen-job claims")
    batches, seen, stable, started = [], set(), set(), False
    jobs_seen = set()
    for item in plan["jobs"]:
        b.fields(item, {"id", "keys_digest"})
        b.require(item["id"] not in jobs_seen and claims.get(item["id"]) == run_id, "Frozen-job claim conflict")
        jobs_seen.add(item["id"])
        job = bulk.load(root, item["id"])
        snapshots.append((root / "bulk" / (item["id"] + ".json"), job))
        selection = job["selection"]
        b.require(selection["scope"] == plan["scope"], "Frozen job scope differs")
        ids = selection["ids"]
        b.require(not seen.intersection(ids), "Duplicate frozen message identities")
        seen.update(ids)
        keys = read(folder / (item["id"] + ".keys.json"))
        b.require(b.digest(keys) == item["keys_digest"], "Frozen identity map changed")
        if plan["scope"]["provider"] == "outlook":
            b.require(type(keys) is dict and set(keys) == set(ids), "Missing frozen recovery keys")
            for key in keys.values():
                b.text_value(key, "stable recovery key", 1000)
                b.require(key not in stable, "Ambiguous frozen recovery keys")
                stable.add(key)
        else:
            b.require(keys == {}, "Unexpected Gmail recovery map")
        started = started or bool(job["results"])
        size = selection["batch_size"]
        for offset in range(0, len(ids), size):
            batches.append(([{"id": identity} for identity in ids[offset:offset + size]], keys))
    if (folder / "execution.json").exists():
        state = read(folder / "execution.json")
        started = started or bool(state.get("active")) or bool(state.get("sequence"))
    account_folder = root / "bulk-runs" / ("account-" + b.digest({"provider": plan["scope"]["provider"], "account": plan["scope"]["account"].casefold()})[:32])
    guard = account_state(account_folder)
    if (account_folder / "write-guard.json").exists():
        snapshots.append((account_folder / "write-guard.json", guard))
    account_blocker = "unsettled_write" if guard["unsettled_run"] is not None else ("provider_wait" if guard.get("retry_not_before", 0) > time.time() else None)
    adapter(plan["scope"], {}, 20)
    b.require(all(b.load_json(path) == value for path, value in snapshots), "Frozen data changed during planning")
    return {"run_id": run_id, "scope": plan["scope"], "selected_count": len(seen), "batches": batches, "started": started, "account_blocker": account_blocker, "snapshot_digest": b.digest([value for path, value in snapshots])}


def validated_request(request, frozen):
    b.fields(request, {"account_key", "action", "selection", "budget"}, {"workload", "limits"})
    b.text_value(request["account_key"], "account key", 100)
    b.require(request["action"] in {"move", "archive", "label"}, "Unknown bulk action")
    b.require(request["selection"] in {"frozen_ids", "dynamic_query"}, "Unknown selection boundary")
    supplied_limits = request.get("limits", {})
    b.fields(supplied_limits, set(), {"batch_size", "read_limit", "write_limit", "page_size"})
    limits = {"batch_size": 100, "read_limit": 20, "write_limit": 100, "page_size": 100, **supplied_limits}
    b.fields(limits, {"batch_size", "read_limit", "write_limit", "page_size"})
    for name, value in limits.items():
        b.integer(value, 1, 100, name)
    budget = request["budget"]
    b.fields(budget, {"max_calls", "scope"}, {"selection_calls_spent", "retry_reserve", "max_seconds"})
    b.integer(budget["max_calls"], 1, 100000, "call budget")
    b.require(budget["scope"] in {"execution", "selection_and_execution"}, "Unknown budget coverage")
    for name in ("selection_calls_spent", "retry_reserve"):
        if name in budget:
            b.integer(budget[name], 0, 100000, name)
    b.require(budget["scope"] != "execution" or "selection_calls_spent" not in budget, "Selection spending needs a combined budget")
    if "max_seconds" in budget:
        b.integer(budget["max_seconds"], 1, 3600, "execution time cap")
    if frozen is not None:
        b.require("workload" not in request and request["selection"] == "frozen_ids", "Frozen jobs own their workload and selection")
        b.require(frozen["scope"]["action"] == request["action"], "Frozen action differs from requested action")
        count = frozen["selected_count"]
        sizes = [len(rows) for rows, keys in frozen["batches"]]
        workload = {"selected_count": count}
    else:
        workload = request.get("workload")
        b.fields(workload, {"selected_count"}, {"matched_count", "enumeration_pages", "job_sizes"})
        count = workload["selected_count"]
        b.integer(count, 0, 100000, "selected count")
        matched = workload.get("matched_count", count)
        b.integer(matched, count, 100000, "matched count before exclusions")
        if "enumeration_pages" in workload:
            b.integer(workload["enumeration_pages"], 1, 100000, "enumeration pages")
        jobs = workload.get("job_sizes", [min(1000, count - offset) for offset in range(0, count, 1000)])
        b.require(type(jobs) is list and len(jobs) <= 1000, "Invalid planned job sizes")
        for size in jobs:
            b.integer(size, 1, 1000, "planned job size")
        b.require(sum(jobs) == count, "Planned jobs do not match the selected count")
        sizes = [min(limits["batch_size"], size - offset) for size in jobs for offset in range(0, size, limits["batch_size"])]
    return workload, limits, budget, sizes


def route_status(missing, unavailable):
    return "unavailable" if unavailable else ("needs_evidence" if missing else "supported_by_recorded_evidence")


def estimate(account, action, workload, limits, budget, sizes, frozen):
    provider, count = account["provider"], workload["selected_count"]
    setup = 1 + (provider == "gmail" and action != "archive")
    overhead = setup
    writes_low = writes_high = reads_low = reads_high = 0
    for index, size in enumerate(sizes):
        if provider == "gmail":
            writes_low += (size + limits["write_limit"] - 1) // limits["write_limit"]
            writes_high += size
            reads_low += 2 * size
            reads_high += 2 * size
            if frozen:
                rows, keys = frozen["batches"][index]
                actual = len(groups_by_size(rows, limits["write_limit"], lambda row: row["id"]))
                writes_high += actual - size
                writes_low += actual - ((size + limits["write_limit"] - 1) // limits["write_limit"])
        else:
            groups = (size + limits["read_limit"] - 1) // limits["read_limit"]
            if frozen:
                rows, keys = frozen["batches"][index]
                groups = len(groups_by_size(rows, limits["read_limit"], lambda row: keys[row["id"]].replace("'", "''")))
            reads_low += 4 * groups
            reads_high += 4 * (groups if frozen else size)
            writes_low += size
            writes_high += size
    if count == 0:
        overhead = 0
    selection_low = selection_high = 0
    assumptions = ["All targets still need the approved action, with one complete metadata page per folder/filter query; retries, extra pagination and interruption recovery are additional."]
    if not frozen:
        assumptions.append("Counts/job sizes are planning inputs, not frozen mailbox evidence; request-size grouping spans best packing to one item per request. Replan from actual jobs before writing.")
    if budget["scope"] == "selection_and_execution":
        if "selection_calls_spent" in budget:
            selection_low = selection_high = budget["selection_calls_spent"]
        else:
            matched = workload.get("matched_count", count)
            pages = workload.get("enumeration_pages", max(1, (matched + limits["page_size"] - 1) // limits["page_size"]))
            selection_low = setup + pages
            selection_high = selection_low if "enumeration_pages" in workload else None
            assumptions.append("Selection pages are supplied or assume full requested pages. Underfilled/empty continuation pages can add calls; use actual freeze spending before execution.")
    reserve = budget.get("retry_reserve", 0)
    low = selection_low + overhead + reads_low + writes_low + reserve
    high = None if selection_high is None else selection_high + overhead + reads_high + writes_high + reserve
    status = "exceeds_budget" if low > budget["max_calls"] else ("estimated_fit" if high is not None and high <= budget["max_calls"] else "uncertain")
    cap = budget["max_calls"] if budget["scope"] == "execution" else (max(0, budget["max_calls"] - budget["selection_calls_spent"]) if "selection_calls_spent" in budget else None)
    return {
        "basis": "frozen_identity_grouping" if frozen else "count_scenario",
        "selection_calls": {"min": selection_low, "max": selection_high},
        "execution_calls": {
            "setup": overhead,
            "reads": {"min": reads_low, "max": reads_high},
            "writes": {"min": writes_low, "max": writes_high},
        },
        "retry_calls_reserved": reserve,
        "total_calls_with_reserve": {"min": low, "max": high},
        "assessment": status,
        "execution_call_cap": cap,
        "replan_after_freeze": not frozen or (budget["scope"] == "selection_and_execution" and "selection_calls_spent" not in budget),
        "assumptions": assumptions,
        "completion_guaranteed": False,
        "elapsed_time_estimate": None,
        "token_or_money_estimate": None,
    }


def plan(inventory, request, frozen=None):
    evidence = capabilities.inspect_inventory(inventory)
    workload, limits, budget, sizes = validated_request(request, frozen)
    matches = [account for account in evidence["accounts"] if account["key"] == request["account_key"]]
    b.require(len(matches) == 1, "Select one account from the saved inventory")
    account = matches[0]
    provider, action = account["provider"], request["action"]
    if frozen:
        b.require(frozen["scope"]["provider"] == provider, "Frozen provider differs from capability evidence")
        if account["account"] is not None:
            b.require(frozen["scope"]["account"].casefold() == account["account"].casefold(), "Frozen account differs from capability evidence")
    required = [provider + ".profile"] + (["gmail.labels", "gmail.read", "gmail.modify"] if provider == "gmail" else ["outlook.list", "outlook.move"])
    if budget["scope"] == "selection_and_execution" and "selection_calls_spent" not in budget:
        required.append("gmail.search" if provider == "gmail" else "outlook.list")
    missing = [name for name in required if operation(account, name) == "unknown"]
    unavailable = [name for name in required if operation(account, name) == "unavailable"]
    for name in ("callable_tools", "process_io", "posix_shell"):
        status = evidence["host"].get(name, {}).get("status", "unknown")
        if status != "available":
            (unavailable if status == "unavailable" else missing).append("host." + name)
    for name, good, bad in (("identity", "confirmed", "mismatch"), ("access", "allowed", "denied")):
        status = account[name]["status"]
        if status != good:
            (unavailable if status == bad else missing).append(name)
    if evidence["platform"] != "openai":
        unavailable.append("packaged_adapter_for_platform")
    if provider == "outlook" and action == "label":
        unavailable.append("packaged_category_adapter")
    bound_requirements = {"gmail.modify": "max_items"} if provider == "gmail" else {"outlook.list": "max_page_size"}
    if "gmail.search" in required:
        bound_requirements["gmail.search"] = "max_page_size"
    for name, bound in bound_requirements.items():
        limit = account["operations"].get(name, {}).get("limits", {}).get(bound)
        if limit is None:
            missing.append(name + "." + bound)
        elif name == "gmail.modify":
            limits["write_limit"] = min(limits["write_limit"], limit)
        else:
            limits["page_size"] = min(limits["page_size"], limit)
            if name == "outlook.list":
                limits["read_limit"] = min(limits["read_limit"], limit)
    if "enumeration_pages" in workload:
        b.require(workload["enumeration_pages"] * limits["page_size"] >= workload.get("matched_count", workload["selected_count"]), "Page count cannot cover the stated selection at the inspected page bound")
    worker = {"route": "connector_worker", "status": route_status(missing, unavailable), "missing_evidence": sorted(set(missing)), "unavailable": sorted(set(unavailable)), "automatic_continuation": not missing and not unavailable, "budget": None}
    if request["selection"] != "frozen_ids":
        worker.update(status="not_applicable", automatic_continuation=False)
    elif frozen and frozen["started"]:
        worker.update(status="needs_reconciliation", automatic_continuation=False)
    elif frozen and frozen["account_blocker"]:
        worker.update(status="needs_reconciliation" if frozen["account_blocker"] == "unsettled_write" else "provider_wait", automatic_continuation=False)
    elif evidence["platform"] == "openai" and not (provider == "outlook" and action == "label") and all(operation(account, name) == "supported" for name in required) and all(account["operations"].get(name, {}).get("limits", {}).get(bound) is not None for name, bound in bound_requirements.items()):
        worker["budget"] = estimate(account, action, workload, limits, budget, sizes, frozen)
    native_state = operation(account, "gmail.bulk_query") if provider == "gmail" else "unavailable"
    native = {"route": "native_query_bulk", "status": "unavailable" if native_state == "unavailable" else "needs_evidence", "automatic_continuation": False, "missing_evidence": ["native_verification_and_recovery_recipe"] if native_state == "supported" else ["native_query_contract"], "host_dispatch_calls": 1 if native_state == "supported" else None, "total_calls": None, "provider_internal_requests": None}
    if provider != "gmail":
        native.update(status="not_implemented", missing_evidence=["packaged_native_route_for_provider"])
    if request["selection"] != "dynamic_query" or action == "archive":
        native["status"] = "not_applicable"
    elif account["access"]["status"] == "denied" or account["identity"]["status"] == "mismatch":
        native["status"] = "unavailable"
    manual_ops = [provider + ".profile", "gmail.read" if provider == "gmail" else "outlook.list", "gmail.modify" if provider == "gmail" else ("outlook.categories" if action == "label" else "outlook.move")]
    manual_supported = all(operation(account, name) == "supported" for name in manual_ops) and account["identity"]["status"] == "confirmed" and account["access"]["status"] == "allowed"
    manual_status = "manual_only" if manual_supported and not (provider == "outlook" and action == "label") else "needs_evidence"
    if any(operation(account, name) == "unavailable" for name in manual_ops) or account["access"]["status"] == "denied" or account["identity"]["status"] == "mismatch":
        manual_status = "unavailable"
    manual = {"route": "agent_mediated", "status": manual_status, "automatic_continuation": False, "total_calls": None, "note": "Requires a separately bounded scope and independent verification; the packaged worker budget does not describe an agent-mediated loop."}
    assessment = worker["budget"]["assessment"] if worker["budget"] else None
    recommended = "connector_worker" if worker["status"] == "supported_by_recorded_evidence" and assessment == "estimated_fit" and workload["selected_count"] > 0 else None
    planning_status = assessment if worker["status"] == "supported_by_recorded_evidence" else worker["status"]
    if workload["selected_count"] == 0:
        planning_status = "empty_selection"
        summary = "No messages are selected."
    elif worker["status"] == "needs_reconciliation":
        summary = "This cleanup has saved progress that needs checking before starting again."
    elif worker["status"] == "provider_wait":
        summary = "The mail service’s wait period has not ended. Keep this cleanup paused."
    elif worker["status"] != "supported_by_recorded_evidence":
        summary = "This connection does not yet have a checked automatic cleanup route."
    elif assessment == "exceeds_budget":
        summary = "This estimate exceeds the call budget. Reduce the group or agree a larger limit."
    elif assessment == "uncertain":
        summary = "The estimate is too uncertain for this budget; narrow it after freezing the selection."
    elif recommended:
        summary = "The estimated calls fit the budget. The selected mail and current access still need checking before approval."
    else:
        summary = "More evidence is needed before planning automatic cleanup."
    worker_options = None
    if recommended and frozen and not worker["budget"]["replan_after_freeze"] and worker["budget"]["execution_call_cap"]:
        worker_options = {"max_calls": worker["budget"]["execution_call_cap"], "read_limit": limits["read_limit"], "write_limit": limits["write_limit"]}
        if "max_seconds" in budget:
            worker_options["max_seconds"] = budget["max_seconds"]
    return {
        "schema_version": 1,
        "account_key": account["key"],
        "account": account["account"],
        "provider": provider,
        "action": action,
        "selected_count": workload["selected_count"],
        "inbox_filing": action != "label",
        "recorded_evidence_only": True,
        "live_verified": False,
        "ready_to_execute": False,
        "permission_granted": False,
        "mailbox_changed": False,
        "recommended_route": recommended,
        "planning_status": planning_status,
        "routes": [worker, native, manual],
        "effective_limits": {**limits, "batch_size": None if frozen else limits["batch_size"]},
        "worker_options": worker_options,
        "budget_limit": budget,
        "frozen_snapshot": {"run_id": frozen["run_id"], "digest": frozen["snapshot_digest"]} if frozen else None,
        "requires_current_scope_access_and_approval": True,
        "user_summary": summary,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--run-id")
    args = parser.parse_args()
    try:
        b.require((args.state_dir is None) == (args.run_id is None), "Supply both private state and frozen run, or neither")
        frozen = frozen_workload(args.state_dir, args.run_id) if args.run_id else None
        print(json.dumps(plan(b.load_json(args.inventory), b.load_json(args.request), frozen), indent=2))
        return 0
    except (b.ButlerError, OSError, ValueError, TypeError) as error:
        print(json.dumps({"status": "blocked", "reason": type(error).__name__, "mailbox_changed": False, "permission_granted": False}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
