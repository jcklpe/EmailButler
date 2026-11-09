#!/usr/bin/env python3
"""Deterministic bulk executor using a host-supplied authenticated connector transport."""
import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import sys
import time

import bulk
from connector_adapters import ConnectorFailure, adapter, payload
import private_state as b


def ensure_private_location(path):
    path = b.no_symlinks(path)
    source = Path(__file__).resolve().parents[3]
    b.require(path != source and source not in path.parents, "Execution data must stay outside the package checkout")
    return path


class Stopped(Exception):
    pass


def run_dir(root, run_id):
    b.require(type(run_id) is str and len(run_id) == 34 and run_id.startswith("R-") and all(c in "0123456789abcdef" for c in run_id[2:]), "Invalid execution ID")
    return b.private_directory(root / "bulk-runs" / run_id)


def prepare(root, request):
    b.fields(request, {"jobs"}, {"identity_files"})
    jobs = request["jobs"]
    b.require(type(jobs) is list and 1 <= len(jobs) <= 1000 and all(type(job) is str for job in jobs) and len(set(jobs)) == len(jobs), "Expected 1–1000 distinct frozen job IDs")
    files = request.get("identity_files", {})
    b.require(type(files) is dict and set(files) <= set(jobs), "Identity inputs must belong to the frozen jobs")
    root = ensure_private_location(b.private_directory(root))
    with b.locked(root):
        items, scope, seen_ids, seen_keys = [], None, set(), set()
        keys_by_job = {}
        for job_id in jobs:
            job = bulk.load(root, job_id)
            selection = job["selection"]
            scope = scope or selection["scope"]
            b.require(selection["scope"] == scope, "One execution must have one exact account/action/source/destination scope")
            b.require(not seen_ids.intersection(selection["ids"]), "Frozen jobs contain duplicate message identities")
            seen_ids.update(selection["ids"])
            keys = b.load_json(ensure_private_location(Path(files[job_id]))) if job_id in files else {}
            if scope["provider"] == "outlook":
                b.require(type(keys) is dict and set(keys) == set(selection["ids"]), "Outlook requires frozen Internet Message-ID keys for every selected identity")
                for key in keys.values():
                    b.text_value(key, "Internet Message-ID", 1000)
                    b.require(key not in seen_keys, "Duplicate stable keys require manual identity resolution")
                    seen_keys.add(key)
            else:
                b.require(not keys, "Gmail uses its immutable message IDs")
            keys_by_job[job_id] = keys
            items.append({"id": job_id, "keys_digest": b.digest(keys)})
        adapter(scope, {}, 20)
        plan = {"schema_version": 1, "scope": scope, "jobs": items}
        run_id = "R-" + b.digest(plan)[:32]
        directory = b.private_directory(root / "bulk-runs", create=True)
        claims_path = directory / "claims.json"
        claims = b.load_json(claims_path) if claims_path.exists() else {}
        b.require(type(claims) is dict, "Execution claim registry is invalid")
        for item in items:
            b.require(item["id"] not in claims or claims[item["id"]] == run_id, "A frozen job already belongs to another execution")
        folder = b.private_directory(directory / run_id, create=True)
        if (folder / "plan.json").exists():
            b.require(b.load_json(folder / "plan.json") == plan, "Execution plan conflict")
        else:
            b.atomic_json(folder / "plan.json", plan)
        for job_id, keys in keys_by_job.items():
            path = folder / (job_id + ".keys.json")
            if path.exists():
                b.require(b.load_json(path) == keys, "Identity map conflict")
            else:
                b.atomic_json(path, keys)
            claims[job_id] = run_id
        b.atomic_json(claims_path, claims)
        return {"run_id": run_id, "account": scope["account"], "provider": scope["provider"], "action": scope["action"], "destination": scope["destination"], "jobs": len(items), "selected": len(seen_ids), "permission_granted": False}


@contextmanager
def execution_lock(folder):
    # Kernel locks release on process death, while the file is retained as a private diagnostic record.
    path = b.no_symlinks(folder / "executor.lock")
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        if os.name == "posix":
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif os.name == "nt":
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            raise b.ButlerError("No supported execution lock on this host")
        yield
    finally:
        os.close(fd)


def account_folder(root, scope):
    parent = b.private_directory(root / "bulk-runs", create=True)
    return b.private_directory(parent / ("account-" + b.digest({"provider": scope["provider"], "account": scope["account"].casefold()})[:32]), create=True)


def account_state(folder):
    path = folder / "write-guard.json"
    value = b.load_json(path) if path.exists() else {"unsettled_run": None}
    b.fields(value, {"unsettled_run"}, {"retry_not_before"})
    deadline = value.get("retry_not_before", 0)
    b.require(type(deadline) in (int, float) and math.isfinite(deadline) and deadline >= 0, "Invalid account retry deadline")
    return value


def persist_throttle(folder, error):
    if error.status != 429:
        return
    delay = error.retry_after if error.retry_after is not None else 1
    b.require(math.isfinite(delay) and delay >= 0, "Unsupported retry delay")
    guard = account_state(folder)
    guard["retry_not_before"] = max(guard.get("retry_not_before", 0), time.time() + delay)
    b.atomic_json(folder / "write-guard.json", guard)


class Executor:
    def __init__(self, root, run_id, approved_run_id, transport, *, max_calls=1000, max_seconds=600, max_attempts=3, read_limit=20, write_limit=100, previous_call_settled=False, sleep=time.sleep, clock=time.monotonic, cancelled=lambda: False):
        b.require(approved_run_id == run_id, "Current-session approval must identify this exact execution")
        for value, low, high, name in [(max_calls, 1, 100000, "call budget"), (max_seconds, 1, 3600, "time budget"), (max_attempts, 1, 5, "attempt bound"), (read_limit, 1, 100, "metadata read limit"), (write_limit, 1, 100, "write item limit")]:
            b.integer(value, low, high, name)
        self.root = ensure_private_location(b.private_directory(root))
        self.run_id, self.folder = run_id, run_dir(root, run_id)
        self.plan = b.load_json(self.folder / "plan.json")
        b.fields(self.plan, {"schema_version", "scope", "jobs"})
        b.require(self.plan["schema_version"] == 1 and run_id == "R-" + b.digest(self.plan)[:32], "Execution plan changed")
        self.account_folder = account_folder(self.root, self.plan["scope"])
        self.transport, self.max_calls, self.max_seconds, self.max_attempts = transport, max_calls, max_seconds, max_attempts
        self.read_limit, self.write_limit = read_limit, write_limit
        self.previous_call_settled = previous_call_settled
        self.sleep, self.clock, self.cancelled = sleep, clock, cancelled
        self.started, self.calls, self.write_calls = clock(), 0, 0
        self.state = None

    def save(self):
        b.atomic_json(self.folder / "execution.json", self.state)

    def account_guard(self, owner):
        guard = account_state(self.account_folder)
        guard["unsettled_run"] = owner
        b.atomic_json(self.account_folder / "write-guard.json", guard)

    def call(self, operation, arguments):
        if self.cancelled():
            raise Stopped("cancelled")
        if self.calls >= self.max_calls or self.clock() - self.started >= self.max_seconds:
            raise Stopped("budget_reached")
        self.calls += 1
        self.write_calls += operation in {"outlook.move", "gmail.modify"}
        self.state["last_request"] = {"operation": operation, "sequence": self.state["sequence"] + 1, "settled": False}
        self.state["sequence"] += 1
        self.save()
        writing = operation in {"outlook.move", "gmail.modify"}
        if writing:
            self.account_guard(self.run_id)
        value = self.transport({"id": self.state["sequence"], "operation": operation, "arguments": arguments, "timeout_seconds": max(1, min(60, self.max_seconds - (self.clock() - self.started)))})
        self.state["last_request"]["settled"] = True
        self.save()
        try:
            result = payload(value)
            if writing:
                self.account_guard(None)
            return result
        except ConnectorFailure as error:
            persist_throttle(self.account_folder, error)
            if writing and error.status is not None and error.status not in {408, 500, 502, 503, 504}:
                self.account_guard(None)
            if operation in {"outlook.move", "gmail.modify"} and (error.status is None or error.status in {408, 500, 502, 503, 504}):
                self.state["last_request"]["settled"] = False
                self.save()
            raise

    def inspect(self, bridge, rows, destination_first):
        for attempt in range(self.max_attempts):
            try:
                return bridge.inspect(self.call, rows, destination_first)
            except ConnectorFailure as error:
                if error.status not in {429, 500, 502, 503, 504} or attempt + 1 == self.max_attempts:
                    raise
                self.backoff(error, attempt)
        raise Stopped("readback_unavailable")

    def backoff(self, error, attempt):
        delay = error.retry_after if error.retry_after is not None else 2 ** attempt
        b.require(math.isfinite(delay) and delay >= 0, "Unsupported retry delay")
        self.state["retry_not_before"] = time.time() + delay
        self.save()
        guard = account_state(self.account_folder)
        guard["retry_not_before"] = max(guard.get("retry_not_before", 0), self.state["retry_not_before"])
        b.atomic_json(self.account_folder / "write-guard.json", guard)
        if self.clock() - self.started + delay >= self.max_seconds:
            raise Stopped("retry_delay_exceeds_budget")
        # Long provider delays are persisted as a stop, never shortened to force another request.
        if delay > 60:
            raise Stopped("provider_retry_delay")
        self.sleep(delay)
        self.state.pop("retry_not_before", None)
        self.save()
        guard.pop("retry_not_before", None)
        b.atomic_json(self.account_folder / "write-guard.json", guard)

    def load_job(self, item):
        with b.locked(self.root):
            claims = b.load_json(self.root / "bulk-runs" / "claims.json")
            b.require(claims.get(item["id"]) == self.run_id, "Execution lost its frozen-job claim")
            job = bulk.load(self.root, item["id"])
        b.require(job["selection"]["scope"] == self.plan["scope"], "Frozen job scope changed")
        keys = b.load_json(self.folder / (item["id"] + ".keys.json"))
        b.require(b.digest(keys) == item["keys_digest"], "Frozen identity map changed")
        return job, keys

    def summary(self, status, reason=None):
        counts = {name: 0 for name in bulk.STATES}
        for item in self.plan["jobs"]:
            view = bulk.status(self.root, item["id"])
            for name in counts:
                counts[name] += view["counts"][name]
        return {"status": status, "reason": reason, "run_id": self.run_id, "counts": counts, "connector_calls": self.calls, "write_calls": self.write_calls, "permission_granted": False, "inbox_filing": self.plan["scope"]["action"] != "label"}

    def execute(self):
        with execution_lock(self.account_folder), execution_lock(self.folder):
            path = self.folder / "execution.json"
            self.state = b.load_json(path) if path.exists() else {"sequence": 0, "active": None}
            b.fields(self.state, {"sequence", "active"}, {"last_request", "retry_not_before"})
            b.integer(self.state["sequence"], 0, 2**53 - 1, "execution sequence")
            try:
                guard = account_state(self.account_folder)
                if guard.get("retry_not_before", 0) > time.time():
                    raise Stopped("provider_retry_delay")
                if guard["unsettled_run"] is not None:
                    if guard["unsettled_run"] != self.run_id:
                        raise Stopped("another_execution_has_unsettled_write")
                    if not self.previous_call_settled:
                        raise Stopped("previous_write_not_settled")
                    self.account_guard(None)
                if self.state.get("retry_not_before", 0) > time.time():
                    raise Stopped("provider_retry_delay")
                self.state.pop("retry_not_before", None)
                last = self.state.get("last_request", {})
                if last.get("operation") in {"outlook.move", "gmail.modify"} and not last.get("settled") and not self.previous_call_settled:
                    raise Stopped("previous_write_not_settled")
                bridge = adapter(self.plan["scope"], {}, self.read_limit)
                bridge.check(self.call)
                for item in self.plan["jobs"]:
                    job, keys = self.load_job(item)
                    bridge = adapter(self.plan["scope"], keys, self.read_limit)
                    if bulk.view(job)["status"] == "complete":
                        if self.state["active"] and self.state["active"]["job_id"] == item["id"]:
                            self.state["active"] = None
                            self.save()
                        continue
                    while bulk.view(job)["status"] != "complete":
                        view = bulk.view(job)
                        active = self.state["active"]
                        if active is not None:
                            b.require(active["job_id"] == item["id"], "Saved active batch does not match job order")
                            ids = active["ids"]
                            b.require(type(ids) is list and bool(ids) and set(ids) <= set(job["selection"]["ids"]), "Invalid active batch")
                            rows = [{"id": identity} for identity in ids if job["results"].get(str(job["selection"]["ids"].index(identity)), {}).get("status") != "verified"]
                            if rows and all(job["results"].get(str(job["selection"]["ids"].index(row["id"])), {}).get("status", "pending") == "pending" for row in rows):
                                b.require([row["id"] for row in rows] == [row["id"] for row in view["next_batch"]], "Interrupted intent no longer matches the pending batch")
                                bulk.record(self.root, item["id"], {"base_revision": view["revision"], "results": [{"id": row["id"], "status": "uncertain", "evidence": "Recovered executor intent before any new write"} for row in rows]})
                                job, keys = self.load_job(item)
                            b.require(all(job["results"].get(str(job["selection"]["ids"].index(row["id"])), {}).get("status") in {"reported", "uncertain", "failed"} for row in rows), "Active checkpoint changed; inspect before resuming")
                        else:
                            # Existing unresolved rows also need readback before any new writes.
                            rows = [{"id": job["selection"]["ids"][int(index)]} for index, result in job["results"].items() if result["status"] in {"reported", "uncertain", "failed"}]
                            if not rows:
                                rows = view["next_batch"]
                                self.state["active"] = {"job_id": item["id"], "ids": [row["id"] for row in rows]}
                                self.save()
                                view = bulk.record(self.root, item["id"], {"base_revision": view["revision"], "results": [{"id": row["id"], "status": "uncertain", "evidence": "Executor write-ahead intent; no outcome inferred"} for row in rows]})
                            else:
                                self.state["active"] = {"job_id": item["id"], "ids": [row["id"] for row in rows]}
                                self.save()
                        if not rows:
                            self.state["active"] = None
                            self.save()
                            job, keys = self.load_job(item)
                            continue
                        for attempt in range(self.max_attempts + 1):
                            observed = self.inspect(bridge, rows, destination_first=True)
                            complete = [row for row in observed if row["status"] == "verified"]
                            if complete:
                                current = bulk.status(self.root, item["id"])
                                bulk.record(self.root, item["id"], {"base_revision": current["revision"], "results": complete}, reconcile=True)
                            pending = [row for row in observed if row["status"] == "pending"]
                            if not pending:
                                break
                            if attempt == self.max_attempts:
                                raise Stopped("no_progress_or_retry_bound")
                            error = None
                            for group in bridge.groups(pending, self.write_limit):
                                try:
                                    bridge.write(self.call, group)
                                except ConnectorFailure as caught:
                                    error = caught
                                    break
                            if error is not None:
                                if error.status in {401, 403}:
                                    raise Stopped("access_denied")
                                if error.status != 429:
                                    raise Stopped("write_outcome_uncertain" if error.status is None or error.status >= 500 or error.status == 408 else "connector_rejected_operation")
                                self.backoff(error, attempt)
                            rows = [{"id": row["id"]} for row in pending]
                        self.state["active"] = None
                        self.save()
                        job, keys = self.load_job(item)
                return self.summary("complete")
            except Stopped as error:
                return self.summary("paused", str(error))
            except ConnectorFailure as error:
                return self.summary("blocked", "access_denied" if error.status in {401, 403} else "connector_or_readback_failure")
            except b.ButlerError:
                if self.state.get("last_request", {}).get("operation") in {"outlook.move", "gmail.modify"}:
                    self.state["last_request"]["settled"] = False
                    self.account_guard(self.run_id)
                    self.save()
                return self.summary("blocked", "unsupported_response_or_changed_state")


def stdio_transport(request):
    print(json.dumps({"type": "request", **request}), flush=True)
    if os.name == "posix":
        import select
        if not select.select([sys.stdin], [], [], request.get("timeout_seconds", 60))[0]:
            raise Stopped("host_bridge_timeout")
    line = sys.stdin.buffer.readline(2_000_001)
    if not line or len(line) > 2_000_000:
        raise Stopped("host_bridge_disconnected")
    response = json.loads(line)
    b.fields(response, {"id"}, {"result", "cancelled"})
    b.require(response["id"] == request["id"], "Host response does not match current request")
    if response.get("cancelled") is True:
        raise Stopped("host_cancelled")
    b.require("result" in response, "Host response lacks a result")
    return response["result"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("prepare")
    create.add_argument("--input", type=Path, required=True)
    frozen = sub.add_parser("freeze")
    frozen.add_argument("--input", type=Path, required=True)
    frozen.add_argument("--max-calls", type=int, default=1000)
    frozen.add_argument("--max-seconds", type=int, default=600)
    run = sub.add_parser("run")
    run.add_argument("--id", required=True)
    run.add_argument("--approved-run-id", required=True)
    run.add_argument("--max-calls", type=int, default=1000)
    run.add_argument("--max-seconds", type=int, default=600)
    run.add_argument("--max-attempts", type=int, default=3)
    run.add_argument("--read-limit", type=int, default=20)
    run.add_argument("--write-limit", type=int, default=100)
    run.add_argument("--previous-call-settled", action="store_true")
    for command in (create, frozen, run):
        command.add_argument("--state-dir", type=Path, default=b.default_state_directory())
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            result = prepare(args.state_dir, b.load_json(args.input))
        else:
            ensure_private_location(b.private_directory(args.state_dir))
            if sys.stdin.isatty() and os.name == "posix":
                import tty
                tty.setraw(sys.stdin.fileno())
            if args.command == "freeze":
                from freeze_bulk import freeze
                result = freeze(args.state_dir, b.load_json(args.input), stdio_transport, prepare, max_calls=args.max_calls, max_seconds=args.max_seconds)
            else:
                result = Executor(args.state_dir, args.id, args.approved_run_id, stdio_transport, max_calls=args.max_calls, max_seconds=args.max_seconds, max_attempts=args.max_attempts, read_limit=args.read_limit, write_limit=args.write_limit, previous_call_settled=args.previous_call_settled).execute()
        print(json.dumps({"type": "summary", **result}), flush=True)
        return 0
    except (ConnectorFailure, Stopped) as error:
        print(json.dumps({"type": "summary", "status": "paused" if isinstance(error, Stopped) else "blocked", "reason": str(error) if isinstance(error, Stopped) else "connector_read_failure", "permission_granted": False}), flush=True)
        return 1
    except (b.ButlerError, OSError, ValueError, EOFError) as error:
        print(json.dumps({"type": "summary", "status": "blocked", "reason": type(error).__name__, "permission_granted": False}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
