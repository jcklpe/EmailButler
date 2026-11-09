import copy
import asyncio
from collections import Counter
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
for owner in ("setup", "customize", "bulk-triage"):
    sys.path.insert(0, str(ROOT / "skills" / (owner + "-emailbutler") / "scripts"))
import bulk
import butler
import execute_bulk as execution
import private_state as b


class Connector:
    def __init__(self, provider, count):
        self.provider = provider
        self.mail = {"m" + str(i): {"id": "m" + str(i), "internetMessageId": "<synthetic-" + str(i) + "@example.com>", "parentFolderId": "source", "labelIds": ["INBOX", "STARRED", "UNREAD", "Label_old"]} for i in range(count)}
        self.calls = Counter()
        self.moves = Counter()
        self.throttle = False
        self.timeout = False
        self.deny = False
        self.wrong_account = False
        self.bad_readback = False
        self.duplicate = False
        self.selected = set(self.mail)

    def __call__(self, request):
        operation, args = request["operation"], request["arguments"]
        self.calls[operation] += 1
        if operation.endswith(".profile"):
            return {"emailAddress": "other@example.com" if self.wrong_account else "owner@example.com"}
        if operation == "gmail.labels":
            return {"labels": [{"id": "Label_target", "type": "user", "name": "Review"}]}
        if operation == "gmail.read":
            if self.bad_readback:
                return {"id": args["message_id"]}
            return copy.deepcopy(self.mail[args["message_id"]])
        if operation == "outlook.list":
            if self.bad_readback:
                return {"value": [], "has_more": True}
            keys = [key.replace("''", "'") for key in re.findall(r"internetMessageId eq '((?:[^']|'')*)'", args["filter"])]
            rows = [copy.deepcopy(row) for row in self.mail.values() if row["internetMessageId"] in keys and row["parentFolderId"] == args["folder_id"]]
            if self.duplicate and rows:
                rows.append(copy.deepcopy(rows[0]))
            return {"value": rows, "has_more": False}
        if self.deny:
            return {"error": {"status": 403}}
        if self.throttle:
            self.throttle = False
            return {"error": {"status": 429, "retry_after": 0}}
        if operation == "outlook.move":
            original = next(key for key, item in self.mail.items() if item["id"] == args["message_id"])
            self.moves[original] += 1
            if self.moves[original] > 1:
                raise AssertionError("Repeated move")
            self.mail[original]["id"] = "moved-" + original
            self.mail[original]["parentFolderId"] = args.get("destination_folder_id", args.get("destination_well_known_folder"))
        elif operation == "gmail.modify":
            for original in args["message_ids"]:
                self.moves[original] += 1
                labels = set(self.mail[original]["labelIds"])
                labels.update(args["add_labels"])
                labels.difference_update(args["remove_labels"])
                self.mail[original]["labelIds"] = sorted(labels)
        else:
            raise AssertionError("Unexpected connector operation")
        if self.timeout:
            self.timeout = False
            return {"error": {"code": "transport_timeout"}}
        return {"id": "moved-" + original} if operation == "outlook.move" else {}


class ExecutorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name).resolve() / "state"
        butler.initialize(self.state)

    def prepare(self, provider="outlook", count=13, chunk=1000, batch=5):
        self.remote = Connector(provider, count)
        scope = {"account": "owner@example.com", "provider": provider, "action": "move", "source": "source" if provider == "outlook" else "inbox", "destination": "destination" if provider == "outlook" else "Label_target", "selection": "Synthetic frozen approved scope"}
        jobs, files = [], {}
        ids = list(self.remote.mail)
        for offset in range(0, count, chunk):
            part = ids[offset:offset + chunk]
            job = bulk.create(self.state, {"scope": scope, "ids": part, "batch_size": batch})
            jobs.append(job["id"])
            if provider == "outlook":
                path = Path(self.temporary.name).resolve() / (job["id"] + ".json")
                b.atomic_json(path, {key: self.remote.mail[key]["internetMessageId"] for key in part})
                files[job["id"]] = str(path)
        self.job_ids = jobs
        self.run = execution.prepare(self.state, {"jobs": jobs, "identity_files": files})
        return self.run

    def execute(self, **options):
        return execution.Executor(self.state, self.run["run_id"], self.run["run_id"], self.remote, sleep=lambda delay: None, **options).execute()

    def test_real_executor_drives_all_outlook_calls_across_jobs_without_per_batch_agent_input(self):
        self.prepare(count=23, chunk=10)
        result = self.execute()
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["counts"]["verified"], 23)
        self.assertEqual(self.remote.calls["outlook.move"], 23)
        self.assertTrue(all(n == 1 for n in self.remote.moves.values()))
        self.assertFalse(result["permission_granted"])

    def test_gmail_groups_writes_and_reads_only_minimal_metadata(self):
        self.prepare("gmail", count=23, batch=10)
        result = self.execute(write_limit=4)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.remote.calls["gmail.modify"], 7)
        for message in self.remote.mail.values():
            self.assertEqual(set(message["labelIds"]), {"STARRED", "UNREAD", "Label_old", "Label_target"})

    def test_throttle_retries_are_bounded_and_followed_by_fresh_inspection(self):
        self.prepare(count=7)
        self.remote.throttle = True
        self.assertEqual(self.execute()["status"], "complete")
        self.assertEqual(self.remote.calls["outlook.move"], 8)
        self.assertTrue(all(n == 1 for n in self.remote.moves.values()))

    def test_last_attempt_throttle_retains_account_deadline_without_another_call(self):
        self.prepare(count=3)
        original = self.remote
        calls = []
        def throttled(request):
            calls.append(request["operation"])
            if request["operation"] == "outlook.list":
                return {"error": {"status": 429, "retry_after": 120}}
            return original(request)
        self.remote = throttled
        self.assertEqual(self.execute(max_attempts=1)["status"], "blocked")
        self.assertGreater(execution.account_state(execution.account_folder(self.state, {"provider": "outlook", "account": "owner@example.com"}))["retry_not_before"], execution.time.time())
        calls.clear()
        self.assertEqual(self.execute()["reason"], "provider_retry_delay")
        self.assertEqual(calls, [])

    def test_ambiguous_write_stops_and_requires_settled_call_before_resume(self):
        self.prepare(count=7)
        self.remote.timeout = True
        first = self.execute()
        self.assertEqual(first["reason"], "write_outcome_uncertain")
        self.assertEqual(self.remote.calls["outlook.move"], 1)
        self.assertEqual(self.execute()["reason"], "previous_write_not_settled")
        result = self.execute(previous_call_settled=True)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(self.remote.calls["outlook.move"], 7)

    def test_budget_stop_after_write_resumes_from_provider_truth_without_repeating_it(self):
        self.prepare(count=7)
        paused = self.execute(max_calls=4)
        self.assertEqual(paused["status"], "paused")
        self.assertEqual(paused["write_calls"], 1)
        self.assertEqual(self.execute()["status"], "complete")
        self.assertEqual(sum(self.remote.moves.values()), 7)

    def test_wrong_account_and_ambiguous_or_truncated_readback_never_write(self):
        for failure in ("wrong_account", "duplicate", "bad_readback"):
            with self.subTest(failure=failure):
                self.prepare(count=3)
                setattr(self.remote, failure, True)
                self.assertEqual(self.execute()["status"], "blocked")
                self.assertEqual(sum(self.remote.moves.values()), 0)

    def test_permission_failure_does_not_retry_or_change_access(self):
        self.prepare(count=3)
        self.remote.deny = True
        self.assertEqual(self.execute()["reason"], "access_denied")
        self.assertEqual(self.remote.calls["outlook.move"], 1)
        self.assertEqual(sum(self.remote.moves.values()), 0)

    def test_cancellation_requires_no_connector_call(self):
        self.prepare(count=3)
        self.assertEqual(self.execute(cancelled=lambda: True)["reason"], "cancelled")
        self.assertFalse(self.remote.calls)

    def test_recover_intent_saved_before_ledger_staging(self):
        self.prepare(count=3)
        folder = execution.run_dir(self.state, self.run["run_id"])
        b.atomic_json(folder / "execution.json", {"sequence": 0, "active": {"job_id": self.job_ids[0], "ids": list(self.remote.mail)}})
        self.assertEqual(self.execute()["status"], "complete")

    def test_missing_identity_map_and_unapproved_run_are_refused(self):
        self.prepare(count=3)
        with self.assertRaises(b.ButlerError):
            execution.prepare(self.state, {"jobs": self.job_ids})
        with self.assertRaises(b.ButlerError):
            execution.Executor(self.state, self.run["run_id"], "another-run", self.remote)

    def test_overlapping_executions_cannot_claim_the_same_job(self):
        self.prepare(count=7, chunk=4)
        with self.assertRaises(b.ButlerError):
            execution.prepare(self.state, {"jobs": [self.job_ids[0]], "identity_files": {self.job_ids[0]: str(Path(self.temporary.name).resolve() / (self.job_ids[0] + ".json"))}})

    def test_execution_lock_prevents_two_workers_for_one_plan(self):
        self.prepare(count=3)
        with execution.execution_lock(execution.run_dir(self.state, self.run["run_id"])):
            with self.assertRaises(OSError):
                self.execute()
        self.assertFalse(self.remote.calls)

    def test_plan_and_identity_map_integrity_are_checked_before_writes(self):
        self.prepare(count=3)
        path = execution.run_dir(self.state, self.run["run_id"]) / (self.job_ids[0] + ".keys.json")
        b.atomic_json(path, {})
        self.assertEqual(self.execute()["status"], "blocked")
        self.assertEqual(sum(self.remote.moves.values()), 0)

    def test_one_attempt_still_performs_a_write_and_verifies_it(self):
        self.prepare(count=3)
        self.assertEqual(self.execute(max_attempts=1)["status"], "complete")

    def test_long_retry_after_is_persisted_instead_of_shortened(self):
        self.prepare(count=3)
        original = self.remote
        def throttle(request):
            if request["operation"] == "outlook.move":
                return {"error": {"status": 429, "retry_after": 120}}
            return original(request)
        executor = execution.Executor(self.state, self.run["run_id"], self.run["run_id"], throttle)
        self.assertEqual(executor.execute()["reason"], "provider_retry_delay")
        self.assertEqual(self.execute()["reason"], "provider_retry_delay")


    def test_actual_executor_handles_more_than_one_thousand_targets(self):
        self.prepare(count=1203, batch=100)
        excluded = {"id": "excluded", "internetMessageId": "<excluded@example.com>", "parentFolderId": "source", "labelIds": ["INBOX", "STARRED"]}
        self.remote.mail["excluded"] = copy.deepcopy(excluded)
        result = self.execute(max_calls=3000)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["counts"]["verified"], 1203)
        self.assertEqual(self.remote.calls["outlook.move"], 1203)
        self.assertEqual(self.remote.mail["excluded"], excluded)

    def test_cli_worker_drives_the_transport_protocol_through_completion(self):
        self.prepare("gmail", count=3)
        async def run():
            command = [sys.executable, "-B", str(ROOT / "skills/bulk-triage-emailbutler/scripts/execute_bulk.py"), "run", "--state-dir", str(self.state), "--id", self.run["run_id"], "--approved-run-id", self.run["run_id"]]
            process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            try:
                for _ in range(50):
                    line = await asyncio.wait_for(process.stdout.readline(), 10)
                    self.assertTrue(line)
                    packet = json.loads(line)
                    if packet["type"] == "summary":
                        await asyncio.wait_for(process.wait(), 5)
                        self.assertEqual(process.returncode, 0)
                        return packet
                    result = self.remote(packet)
                    process.stdin.write((json.dumps({"id": packet["id"], "result": result}) + "\n").encode())
                    await process.stdin.drain()
                self.fail("Executor exceeded its bounded protocol")
            finally:
                if process.returncode is None:
                    process.kill()
                    await process.wait()
        summary = asyncio.run(run())
        self.assertEqual(summary["status"], "complete")
        self.assertEqual(summary["counts"]["verified"], 3)
        self.assertEqual(summary["write_calls"], 1)

    def test_metadata_transport_chunks_remain_bounded_for_long_identifiers(self):
        from connector_adapters import groups_by_size
        rows = [{"id": "x" * 1000 + str(i)} for i in range(100)]
        groups = groups_by_size(rows, 100, lambda row: row["id"])
        self.assertGreater(len(groups), 1)
        self.assertEqual(sum(len(group) for group in groups), 100)
        self.assertTrue(all(len(json.dumps(group)) < 6000 for group in groups))

    def test_graph_nested_throttle_metadata_is_preserved(self):
        from connector_adapters import ConnectorFailure, payload
        with self.assertRaises(ConnectorFailure) as caught:
            payload({"error": {"code": "TooManyRequests", "innerError": {"status": "429"}}, "headers": {"Retry-After": "7"}})
        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.retry_after, 7)

    def test_duplicate_stable_key_in_both_folders_and_trash_state_block_writes(self):
        self.prepare(count=3)
        duplicate = copy.deepcopy(self.remote.mail["m0"])
        duplicate.update(id="copy", parentFolderId="destination")
        self.remote.mail["copy"] = duplicate
        self.assertEqual(self.execute()["status"], "blocked")
        self.assertEqual(sum(self.remote.moves.values()), 0)
        self.prepare("gmail", count=3)
        self.remote.mail["m0"]["labelIds"].append("TRASH")
        self.assertEqual(self.execute()["status"], "blocked")
        self.assertEqual(sum(self.remote.moves.values()), 0)


    def test_private_execution_data_cannot_be_created_in_the_package_checkout(self):
        with self.assertRaises(b.ButlerError):
            execution.ensure_private_location(ROOT / "private-state")

    def test_retry_after_survives_a_budget_stop_and_blocks_immediate_restart(self):
        self.prepare(count=3)
        remote = self.remote
        def throttle(request):
            if request["operation"] == "outlook.move":
                return {"error": {"status": 429, "retry_after": 10}}
            return remote(request)
        executor = execution.Executor(self.state, self.run["run_id"], self.run["run_id"], throttle, max_seconds=1)
        self.assertEqual(executor.execute()["reason"], "retry_delay_exceeds_budget")
        self.assertEqual(self.execute()["reason"], "provider_retry_delay")

    def test_explicit_root_http_error_is_not_misread_as_success(self):
        from connector_adapters import ConnectorFailure, payload
        with self.assertRaises(ConnectorFailure) as caught:
            payload({"status": 403})
        self.assertEqual(caught.exception.status, 403)


    def test_account_lock_serializes_even_different_execution_plans(self):
        self.prepare(count=3)
        folder = execution.account_folder(self.state, {"account": "owner@example.com", "provider": "outlook"})
        with execution.execution_lock(folder):
            with self.assertRaises(OSError):
                self.execute()
        self.assertFalse(self.remote.calls)

    def test_unsettled_write_blocks_another_plan_on_the_same_account(self):
        self.prepare(count=3)
        self.remote.timeout = True
        self.assertEqual(self.execute()["reason"], "write_outcome_uncertain")
        self.prepare(count=4)
        self.assertEqual(self.execute(previous_call_settled=True)["reason"], "another_execution_has_unsettled_write")
        self.assertFalse(self.remote.calls)

    def test_retry_after_applies_to_another_execution_on_the_same_account(self):
        self.prepare(count=3)
        remote = self.remote
        def throttle(request):
            if request["operation"] == "outlook.move":
                return {"error": {"status": 429, "retry_after": 120}}
            return remote(request)
        first = execution.Executor(self.state, self.run["run_id"], self.run["run_id"], throttle)
        self.assertEqual(first.execute()["reason"], "provider_retry_delay")
        self.prepare(count=4)
        self.assertEqual(self.execute()["reason"], "provider_retry_delay")
        self.assertFalse(self.remote.calls)


    def test_independent_readback_follows_small_provider_pages_without_model_input(self):
        self.prepare(count=7)
        remote = self.remote
        def paginated(request):
            result = remote(request)
            if request["operation"] == "outlook.list":
                rows = result["value"]
                cursor = request["arguments"]["skip"]
                result["value"] = rows[cursor:cursor + 1]
                result["has_more"] = cursor + 1 < len(rows)
                result["next_from_index"] = cursor + 1
            return result
        worker = execution.Executor(self.state, self.run["run_id"], self.run["run_id"], paginated)
        self.assertEqual(worker.execute()["status"], "complete")
        self.assertEqual(sum(remote.moves.values()), 7)


if __name__ == "__main__":
    unittest.main()
