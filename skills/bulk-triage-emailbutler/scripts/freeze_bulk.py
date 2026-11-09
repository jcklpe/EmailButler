"""Read-only deterministic selection/pagination into frozen jobs; never modify mail."""
import time
import secrets
import tempfile
from pathlib import Path

import bulk
import private_state as b
from connector_adapters import ConnectorFailure, adapter, collection, field, payload


def _freeze(root, request, transport, create_run, *, max_calls=1000, max_seconds=600, clock=time.monotonic):
    b.fields(request, {"scope", "query"}, {"exclude_ids", "max_messages", "page_size", "batch_size"})
    scope = dict(request["scope"])
    scope["selection"] += " [snapshot " + secrets.token_hex(8) + "]"
    bulk.validate_selection({"scope": scope, "ids": ["scope-validation-only"], "batch_size": 1})
    query = request["query"]
    b.require(type(query) is str and len(query) <= 5000 and not any(ord(c) < 32 for c in query), "Query must be a bounded connector filter/search string")
    exclude = request.get("exclude_ids", [])
    b.require(type(exclude) is list and len(exclude) <= 100000 and all(type(item) is str for item in exclude), "Invalid explicit exclusions")
    exclude = set(exclude)
    maximum, page_size, batch_size = request.get("max_messages", 100000), request.get("page_size", 100), request.get("batch_size", 100)
    for value, lower, upper, name in [(maximum, 1, 100000, "selection bound"), (page_size, 1, 500, "page size"), (batch_size, 1, 100, "batch size"), (max_calls, 1, 100000, "call budget"), (max_seconds, 1, 3600, "time budget")]:
        b.integer(value, lower, upper, name)
    root = b.private_directory(root)
    calls, started = 0, clock()
    def call(operation, arguments):
        nonlocal calls
        b.require(calls < max_calls and clock() - started < max_seconds, "Selection budget exhausted before all pages were frozen")
        calls += 1
        return payload(transport({"id": calls, "operation": operation, "arguments": arguments, "timeout_seconds": max(1, min(60, max_seconds - (clock() - started)))}))
    bridge = adapter(scope, {}, 20)
    bridge.check(call)
    entries, seen, cursor, previous = [], set(), None, set()
    while True:
        if scope["provider"] == "gmail":
            args = {"query": query, "label_ids": ["INBOX"], "max_results": page_size}
            if cursor is not None:
                args["next_page_token"] = cursor
            page = call("gmail.search", args)
            ids = page.get("message_ids")
            if ids is None:
                ids = [row.get("id") for row in collection(page, "messages")]
            b.require(type(ids) is list, "Unsupported Gmail ID-list response")
            rows = [{"id": identity} for identity in ids]
            next_cursor = page.get("next_page_token", page.get("nextPageToken"))
            b.require(not page.get("has_more") or next_cursor is not None, "Gmail page is truncated without a continuation token")
        else:
            page = call("outlook.list", {"folder_id": scope["source"], "filter": query, "order_by": "receivedDateTime asc", "select": ["internet_message_id"], "top": page_size, "skip": cursor or 0})
            rows = collection(page)
            b.require(type(page.get("has_more")) is bool, "Outlook page lacks explicit pagination metadata")
            b.require(page["has_more"] or not page.get("@odata.nextLink"), "Outlook continuation metadata conflicts")
            next_cursor = page.get("next_from_index") if page.get("has_more") else None
            if next_cursor is not None:
                b.require(type(next_cursor) is int and next_cursor > (cursor or 0), "Outlook cursor did not advance")
            b.require(not page.get("has_more") or next_cursor is not None, "Outlook page is truncated without a cursor")
        for row in rows:
            identity = field(row, "id", "message_id")
            b.text_value(identity, "message identity", 1000)
            b.require(identity not in seen, "Selection changed or repeated IDs across pages; freeze again before writing")
            seen.add(identity)
            b.require(len(seen) <= maximum, "Selection exceeds the reviewed enumeration bound")
            if identity not in exclude:
                key = field(row, "internetMessageId", "internet_message_id") if scope["provider"] == "outlook" else None
                if key is not None:
                    b.text_value(key, "stable message key", 1000)
                b.require(scope["provider"] != "outlook" or key is not None, "Outlook selection lacks stable recovery keys")
                entries.append((identity, key))
        if next_cursor is None:
            break
        b.require(type(next_cursor) in (str, int) and next_cursor not in previous, "Pagination cursor repeated")
        previous.add(next_cursor)
        cursor = next_cursor
    if not entries:
        return {"status": "empty_selection", "selected": 0, "connector_calls": calls, "permission_granted": False}
    chunks, current, weight = [], [], 0
    for entry in entries:
        size = len(b.canonical(entry)) + 32
        if current and (len(current) >= 1000 or weight + size > 250000):
            chunks.append(current)
            current, weight = [], 0
        current.append(entry)
        weight += size
    if current:
        chunks.append(current)
    b.require(len(chunks) <= 1000, "Frozen scope needs more local jobs than one execution supports")
    # All pages are complete before any jobs are materialized. No mutation call is available in this phase.
    jobs, files = [], {}
    with tempfile.TemporaryDirectory(prefix=".bulk-freeze-", dir=root) as temporary:
        for chunk in chunks:
            job = bulk.create(root, {"scope": scope, "ids": [entry[0] for entry in chunk], "batch_size": batch_size})
            jobs.append(job["id"])
            if scope["provider"] == "outlook":
                path = Path(temporary) / (job["id"] + ".json")
                b.atomic_json(path, dict(chunk))
                files[job["id"]] = str(path)
        result = create_run(root, {"jobs": jobs, "identity_files": files})
    result.update(status="frozen", connector_calls=calls, excluded=len(seen.intersection(exclude)))
    b.atomic_json(root / "bulk-runs" / result["run_id"] / "selection-origin.json", {"query": query, "source": scope["source"], "excluded_count": result["excluded"], "pages_complete": True})
    return result


def freeze(root, request, transport, create_run, **limits):
    from execute_bulk import ensure_private_location, account_folder, account_state, execution_lock, persist_throttle
    b.fields(request, {"scope", "query"}, {"exclude_ids", "max_messages", "page_size", "batch_size"})
    bulk.validate_selection({"scope": request["scope"], "ids": ["scope-validation-only"], "batch_size": 1})
    root = ensure_private_location(b.private_directory(root))
    folder = account_folder(root, request["scope"])
    with execution_lock(folder):
        guard = account_state(folder)
        b.require(guard["unsettled_run"] is None, "Settle and reconcile the previous execution before freezing another scope")
        b.require(guard.get("retry_not_before", 0) <= time.time(), "Provider retry deadline has not elapsed")
        try:
            return _freeze(root, request, transport, create_run, **limits)
        except ConnectorFailure as error:
            persist_throttle(folder, error)
            raise
