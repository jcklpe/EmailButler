import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / 'skills/setup-emailbutler/scripts'))
sys.path.insert(0, str(ROOT / 'skills/customize-emailbutler/scripts'))
sys.path.insert(0, str(ROOT / 'skills/bulk-triage-emailbutler/scripts'))
import bulk
import butler


class BulkCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "state"
        butler.initialize(self.root)
        self.account = {"account": "owner@example.com", "provider": "outlook"}

    def selection(self, count=13):
        return {"scope": {**self.account, "action": "move", "source": "inbox", "destination": "folder-review", "selection": "Synthetic frozen reviewed group"}, "ids": ["mail-" + str(i) for i in range(count)], "batch_size": 5}

    def receipts(self, view):
        return {"base_revision": view["revision"], "results": [{"id": item["id"], "status": "verified", "evidence": "synthetic destination read", "result_id": "moved-" + item["id"]} for item in view["next_batch"]]}

    def test_five_item_connector_bound_continues_without_repeating_completed_items(self):
        view = bulk.create(self.root, self.selection())
        sizes, seen = [], []
        while view["status"] != "complete":
            sizes.append(len(view["next_batch"]))
            seen.extend(item["id"] for item in view["next_batch"])
            view = bulk.record(self.root, view["id"], self.receipts(view))
        self.assertEqual(sizes, [5, 5, 3])
        self.assertEqual(len(set(seen)), 13)
        self.assertEqual(view["counts"]["verified"], 13)
        self.assertEqual(bulk.create(self.root, self.selection())["next_batch"], [])
        self.assertFalse(view["mailbox_changed"])
        self.assertFalse(view["permission_granted"])

    def test_partial_uncertain_write_blocks_continuation_until_readback(self):
        view = bulk.create(self.root, self.selection())
        request = self.receipts(view)
        request["results"][0]["status"] = "uncertain"
        view = bulk.record(self.root, view["id"], request)
        self.assertEqual(view["status"], "blocked")
        self.assertEqual(view["next_batch"], [])
        resolved = {"base_revision": view["revision"], "results": [{"id": "mail-0", "status": "verified", "evidence": "fresh synthetic destination read"}]}
        view = bulk.record(self.root, view["id"], resolved, reconcile=True)
        self.assertEqual(view["counts"]["verified"], 5)
        self.assertEqual([item["id"] for item in view["next_batch"]], ["mail-" + str(i) for i in range(5, 10)])

    def test_reconciled_retry_retains_changed_provider_identity(self):
        view = bulk.create(self.root, self.selection(1))
        request = self.receipts(view)
        request["results"][0]["status"] = "reported"
        view = bulk.record(self.root, view["id"], request)
        view = bulk.record(self.root, view["id"], {"base_revision": view["revision"], "results": [{"id": "mail-0", "status": "pending", "evidence": "Fresh read establishes the remaining approved action is safe"}]}, reconcile=True)
        self.assertEqual(view["next_batch"][0]["current_id"], "moved-mail-0")

    def test_stale_or_incomplete_batch_cannot_advance_progress(self):
        view = bulk.create(self.root, self.selection())
        request = self.receipts(view)
        request["results"].pop()
        with self.assertRaises(butler.ButlerError):
            bulk.record(self.root, view["id"], request)
        self.assertEqual(bulk.status(self.root, view["id"])["revision"], 0)
        request = self.receipts(view)
        bulk.record(self.root, view["id"], request)
        with self.assertRaises(butler.ButlerError):
            bulk.record(self.root, view["id"], request)

    def test_foreign_results_and_changed_scope_are_refused(self):
        view = bulk.create(self.root, self.selection())
        request = self.receipts(view)
        request["results"][0]["id"] = "foreign-mail"
        with self.assertRaises(butler.ButlerError):
            bulk.record(self.root, view["id"], request)
        path = self.root / "bulk" / (view["id"] + ".json")
        saved = json.loads(path.read_text())
        saved["selection"]["scope"]["destination"] = "different-folder"
        path.write_text(json.dumps(saved))
        with self.assertRaises(butler.ButlerError):
            bulk.status(self.root, view["id"])

    def test_fresh_process_resume_returns_only_remaining_targets(self):
        view = bulk.create(self.root, self.selection(6))
        view = bulk.record(self.root, view["id"], self.receipts(view))
        output = subprocess.check_output([sys.executable, "-B", str(ROOT / "skills/bulk-triage-emailbutler/scripts/bulk.py"), "status", "--state-dir", str(self.root), "--id", view["id"]], text=True)
        self.assertEqual(json.loads(output)["next_batch"], [{"id": "mail-5", "current_id": "mail-5"}])


if __name__ == "__main__":
    unittest.main()
