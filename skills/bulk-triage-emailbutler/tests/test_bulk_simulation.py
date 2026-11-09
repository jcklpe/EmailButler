"""Offline provider simulation; no network, client registration or connector settings."""
from collections import Counter
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
for owner in ("setup", "customize", "bulk-triage"):
    sys.path.insert(0, str(ROOT / "skills" / (owner + "-emailbutler") / "scripts"))
import bulk
import butler
import onboarding


class SimulatedMailbox:
    """Model five-item writes, changing move IDs and independent metadata lookup by frozen stable keys."""
    def __init__(self, count, readback=True):
        self.messages = {"mail-" + str(i): {"id": "mail-" + str(i), "folder": "inbox", "flagged": i % 11 == 0, "read": i % 3 == 0, "categories": ["existing-category"], "selected": i < count} for i in range(count + 3)}
        self.initial = copy.deepcopy(self.messages)
        self.readback_available = readback
        self.fail_after = None
        self.writes = Counter()
        self.calls = Counter()

    def freeze(self):
        selected = []
        offset = 0
        while True:
            self.calls["search"] += 1
            page = [key for key, value in self.messages.items() if value["selected"] and value["folder"] == "inbox"][offset:offset + 50]
            if not page:
                return selected
            selected.extend(page)
            offset += len(page)

    def move(self, current_ids):
        if not 1 <= len(current_ids) <= 5:
            raise ValueError("Synthetic connector accepts at most five identities per write")
        self.calls["move"] += 1
        returned = {}
        for index, identity in enumerate(current_ids):
            if self.fail_after is not None and index == self.fail_after:
                self.fail_after = None
                raise TimeoutError("Synthetic timeout after partial remote success")
            original = next(key for key, value in self.messages.items() if value["id"] == identity)
            item = self.messages[original]
            if item["folder"] != "inbox":
                raise AssertionError("A completed move was repeated")
            self.writes[original] += 1
            item["id"] = "moved-" + identity
            item["folder"] = "folder-review"
            returned[original] = item["id"]
        return returned

    def readback(self, rows):
        self.calls["readback"] += 1
        if not self.readback_available:
            raise NotImplementedError("No independent lookup exposed in this synthetic scenario")
        results = []
        for row in rows:
            item = self.messages[row["id"]]
            results.append({"id": row["id"], "status": "verified" if item["folder"] == "folder-review" else "pending", "evidence": "Synthetic independent metadata lookup by frozen stable key", "result_id": item["id"]})
        return results


class BulkSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name).resolve() / "synthetic-state"
        butler.initialize(self.state)
        self.account = {"account": "owner@example.com", "provider": "outlook"}
        onboarding.record(self.state, {"base_revision": 0, "outcome": {**self.account, "status": "active", "reason": "Synthetic approved cleanup", "evidence": "Offline fixture, not user authorization"}})

    def jobs(self, mailbox):
        frozen = mailbox.freeze()
        return [bulk.create(self.state, {"scope": {**self.account, "action": "move", "source": "inbox", "destination": "folder-review", "selection": "Synthetic approved union frozen before any writes"}, "ids": frozen[offset:offset + 1000], "batch_size": 5}) for offset in range(0, len(frozen), 1000)]

    def drive(self, mailbox, job, write_call_budget=None):
        batches = 0
        while job["status"] == "pending" and (write_call_budget is None or batches < write_call_budget):
            rows = job["next_batch"]
            try:
                returned = mailbox.move([row["current_id"] for row in rows])
                results = [{"id": row["id"], "status": "reported", "evidence": "Synthetic provider acknowledgement", "result_id": returned[row["id"]]} for row in rows]
            except TimeoutError:
                results = [{"id": row["id"], "status": "uncertain", "evidence": "Synthetic timeout; result unknown"} for row in rows]
            job = bulk.record(self.state, job["id"], {"base_revision": job["revision"], "results": results})
            batches += 1
            try:
                results = mailbox.readback(rows)
            except NotImplementedError:
                return job
            job = bulk.record(self.state, job["id"], {"base_revision": job["revision"], "results": results}, reconcile=True)
        return job

    def assert_mailbox(self, mailbox, complete):
        for original, item in mailbox.messages.items():
            before = mailbox.initial[original]
            for field in ("flagged", "read", "categories"):
                self.assertEqual(item[field], before[field])
            if before["selected"] and complete:
                self.assertEqual(item["folder"], "folder-review")
                self.assertEqual(mailbox.writes[original], 1)
            elif not before["selected"]:
                self.assertEqual(item, before)
        self.assertLessEqual(max(mailbox.writes.values(), default=0), 1)

    def test_1203_selected_messages_cross_job_and_connector_limits_without_touching_exclusions(self):
        mailbox = SimulatedMailbox(1203)
        jobs = self.jobs(mailbox)
        self.assertEqual([job["selected"] for job in jobs], [1000, 203])
        self.assertEqual(mailbox.calls["move"], 0)
        finished = [self.drive(mailbox, job) for job in jobs]
        self.assertEqual(sum(job["counts"]["verified"] for job in finished), 1203)
        self.assertTrue(all(job["status"] == "complete" for job in finished))
        self.assertEqual(mailbox.calls, Counter(search=26, move=241, readback=241))
        self.assert_mailbox(mailbox, complete=True)

    def test_timeout_after_two_moves_is_read_back_before_remaining_messages_are_retried(self):
        mailbox = SimulatedMailbox(12)
        job = self.jobs(mailbox)[0]
        mailbox.fail_after = 2
        finished = self.drive(mailbox, job)
        self.assertEqual(finished["counts"]["verified"], 12)
        self.assertEqual(mailbox.calls["move"], 3)
        self.assert_mailbox(mailbox, complete=True)

    def test_acknowledgement_without_independent_readback_blocks_further_writes(self):
        mailbox = SimulatedMailbox(12, readback=False)
        job = self.drive(mailbox, self.jobs(mailbox)[0])
        self.assertEqual(job["status"], "blocked")
        self.assertEqual(job["counts"]["reported"], 5)
        self.assertEqual(job["counts"]["verified"], 0)
        self.assertEqual(job["next_batch"], [])
        self.assertEqual(mailbox.calls["move"], 1)
        self.assert_mailbox(mailbox, complete=False)

    def test_write_call_budget_preserves_progress_and_onboarding_remains_active_until_verified_completion(self):
        mailbox = SimulatedMailbox(23)
        job = self.drive(mailbox, self.jobs(mailbox)[0], write_call_budget=2)
        self.assertEqual(job["counts"]["verified"], 10)
        self.assertEqual(job["counts"]["pending"], 13)
        self.assertFalse(onboarding.check(self.state, [self.account])["cleanup_completed"])
        fresh = json.loads(subprocess.check_output([sys.executable, "-B", str(ROOT / "skills/bulk-triage-emailbutler/scripts/bulk.py"), "status", "--state-dir", str(self.state), "--id", job["id"]], text=True))
        self.assertEqual(fresh["next_batch"], job["next_batch"])
        self.assertFalse(fresh["permission_granted"])
        finished = self.drive(mailbox, fresh)
        self.assertEqual(finished["counts"]["verified"], 23)
        onboarding.record(self.state, {"base_revision": 1, "outcome": {**self.account, "status": "completed", "reason": "Synthetic selection fully filed", "evidence": "Independent synthetic readback of every selected message"}})
        self.assertTrue(onboarding.check(self.state, [self.account])["cleanup_completed"])
        self.assert_mailbox(mailbox, complete=True)

    def test_full_batch_crash_can_be_reconciled_from_fresh_metadata_without_repeating_moves(self):
        mailbox = SimulatedMailbox(7)
        job = self.jobs(mailbox)[0]
        rows = job["next_batch"]
        mailbox.move([row["current_id"] for row in rows])
        job = bulk.status(self.state, job["id"])
        self.assertEqual(job["counts"]["verified"], 0)
        job = bulk.record(self.state, job["id"], {"base_revision": job["revision"], "results": mailbox.readback(rows)})
        finished = self.drive(mailbox, job)
        self.assertEqual(finished["counts"]["verified"], 7)
        self.assert_mailbox(mailbox, complete=True)

    def test_partial_crash_is_staged_as_uncertain_before_fresh_readback_and_resume(self):
        # Missing receipts are uncertainty about an interrupted call, not proof that every item is still pending.
        mailbox = SimulatedMailbox(7)
        job = self.jobs(mailbox)[0]
        rows = job["next_batch"]
        mailbox.fail_after = 2
        with self.assertRaises(TimeoutError):
            mailbox.move([row["current_id"] for row in rows])
        job = bulk.status(self.state, job["id"])
        self.assertEqual(job["counts"]["verified"], 0)
        uncertain = [{"id": row["id"], "status": "uncertain", "evidence": "Interrupted call has no saved per-item outcome"} for row in rows]
        job = bulk.record(self.state, job["id"], {"base_revision": job["revision"], "results": uncertain})
        self.assertEqual(job["next_batch"], [])
        results = mailbox.readback(rows)
        self.assertEqual([row["status"] for row in results], ["verified", "verified", "pending", "pending", "pending"])
        job = bulk.record(self.state, job["id"], {"base_revision": job["revision"], "results": results}, reconcile=True)
        self.assertEqual(job["counts"]["verified"], 2)
        self.assertEqual(job["counts"]["pending"], 5)
        self.assertEqual(mailbox.calls["move"], 1)
        self.assert_mailbox(mailbox, complete=False)
        finished = self.drive(mailbox, job)
        self.assertEqual(finished["counts"]["verified"], 7)
        self.assert_mailbox(mailbox, complete=True)


if __name__ == "__main__":
    unittest.main()
