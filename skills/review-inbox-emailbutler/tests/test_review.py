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
sys.path.insert(0, str(ROOT / 'skills/review-inbox-emailbutler/scripts'))
import butler
import journal
import review


class PrivateStateCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name).resolve() / "state"
        butler.initialize(self.state)

    def tearDown(self):
        self.temporary.cleanup()


class ReviewTests(PrivateStateCase):
    def test_existing_batch_preference_is_not_overwritten_by_new_defaults(self):
        value = butler.profile_at(self.state)
        value["preferences"]["batch_size"] = 5
        butler.atomic_json(self.state / "profile.json", value)
        result = butler.initialize(self.state)
        self.assertEqual(result["profile"]["preferences"]["batch_size"], 5)
        self.assertEqual(len(review.open_queue(self.state, self.source())["rows"]), 5)

    def test_markers_and_unsubscribe_metadata_survive_queue_resume_without_filtering(self):
        source = self.source(20)
        source["items"][0].update({"read_state": "unread", "starred": True, "flagged": True, "unsubscribe": {"status": "available", "url": "https://example.net/preferences?id=synthetic", "kind": "preferences", "description": "Email-preferences page; digest scope is unconfirmed."}})
        source["items"][1].update({"read_state": "unknown", "starred": None, "flagged": None, "unsubscribe": {"status": "unavailable"}})
        slate = review.open_queue(self.state, source)
        self.assertEqual(len(slate["rows"]), 15)
        self.assertEqual(slate["rows"][0]["id"], source["items"][0]["id"])
        resumed = review.peek(self.state, slate["id"])
        for key in ["read_state", "starred", "flagged", "unsubscribe"]:
            self.assertEqual(resumed["rows"][0][key], source["items"][0][key])
        self.assertIsNone(resumed["rows"][1]["flagged"])

    def test_invalid_marker_and_unsubscribe_payloads_cannot_enter_a_queue(self):
        updates = [
            {"flagged": "yes"},
            {"read_state": "body_read"},
            {"unsubscribe": {"status": "available", "url": "javascript:alert(1)", "kind": "body_unsubscribe", "description": "A link"}},
            {"unsubscribe": {"status": "available", "url": "https://example.net/unsubscribe", "kind": "body_unsubscribe"}},
            {"unsubscribe": {"status": "unsafe", "url": "https://example.net/unsubscribe"}},
        ]
        for update in updates:
            source = self.source(1)
            source["items"][0].update(update)
            with self.subTest(update=update), self.assertRaises(butler.ButlerError):
                review.open_queue(self.state, source)

    def test_one_off_size_does_not_change_saved_settings(self):
        before = (self.state / "profile.json").read_bytes()
        slate = review.open_queue(self.state, self.source(6), batch_size=3)
        self.assertEqual(len(slate["rows"]), 3)
        self.assertEqual((self.state / "profile.json").read_bytes(), before)
        review.finish(self.state, slate["id"], self.decisions(slate))
        self.assertEqual(len(review.peek(self.state, slate["id"])["rows"]), 3)

    def test_deferrals_remain_identifiable_after_queue_completion(self):
        slate = review.open_queue(self.state, self.source(3))
        request = self.decisions(slate)
        request["decisions"][1]["outcome"] = "deferred"
        review.finish(self.state, slate["id"], request)
        self.assertTrue(review.list_queues(self.state)["queues"][0]["complete"])
        self.assertEqual(review.list_queues(self.state)["queues"][0]["deferred_rows"], 1)
        result = review.deferred(self.state, slate["id"])
        self.assertEqual([(row["row"], row["id"]) for row in result["rows"]], [("R2", "message-1")])

    def source(self, count=32):
        return {"scope": [{"account": "owner@example.com", "provider": "gmail", "folder": "inbox", "after": "2026-10-24T00:00:00Z", "before": "2026-11-01T00:00:00Z", "complete": False}], "items": [{"account": "owner@example.com", "provider": "gmail", "id": "message-" + str(number), "sender": "events@example.net", "subject": "Weekly event notice " + str(number), "received_at": "2026-10-31T12:00:00Z", "body_read": False} for number in range(count)]}

    def decisions(self, slate):
        return {"base_revision": slate["revision"], "decisions": [{"row": row["row"], "outcome": "kept_visible"} for row in slate["rows"]]}

    def test_only_one_slate_and_resume_keeps_stable_labels(self):
        source = self.source()
        slate = review.open_queue(self.state, source)
        self.assertEqual([row["row"] for row in slate["rows"]], ["R" + str(number) for number in range(1, 16)])
        self.assertEqual(slate["selected"], 32)
        self.assertFalse(slate["scope"][0]["complete"])
        result = review.finish(self.state, slate["id"], self.decisions(slate))
        self.assertEqual(result["status"], "paused")
        self.assertNotIn("rows", result)
        resumed = review.open_queue(self.state, source)
        self.assertEqual(resumed["rows"][0]["row"], "R16")
        self.assertEqual(resumed["reviewed"], 15)

    def test_current_slate_must_be_resolved_and_stale_decisions_cannot_skip_next_slate(self):
        slate = review.open_queue(self.state, self.source())
        request = self.decisions(slate)
        short = copy.deepcopy(request)
        short["decisions"].pop()
        with self.assertRaises(butler.ButlerError):
            review.finish(self.state, slate["id"], short)
        review.finish(self.state, slate["id"], request)
        with self.assertRaises(butler.ButlerError):
            review.finish(self.state, slate["id"], request)
        self.assertEqual(review.peek(self.state, slate["id"])["reviewed"], 15)

    def test_uncertain_and_duplicate_decisions_cannot_advance(self):
        slate = review.open_queue(self.state, self.source())
        for change in ["uncertain", "duplicate"]:
            request = self.decisions(slate)
            if change == "uncertain":
                request["decisions"][0]["outcome"] = "uncertain"
            else:
                request["decisions"][0] = request["decisions"][1]
            with self.assertRaises(butler.ButlerError):
                review.finish(self.state, slate["id"], request)
        self.assertEqual(review.peek(self.state, slate["id"])["reviewed"], 0)

    def test_cap_duplicate_identity_and_time_range_are_checked(self):
        oversized = self.source(201)
        duplicate = self.source(2)
        duplicate["items"][1] = copy.deepcopy(duplicate["items"][0])
        out_of_range = self.source(1)
        out_of_range["items"][0]["received_at"] = "2026-10-01T12:00:00Z"
        for source in [oversized, duplicate, out_of_range]:
            with self.assertRaises(butler.ButlerError):
                review.open_queue(self.state, source)

    def test_final_short_batch_and_empty_queue_are_complete(self):
        slate = review.open_queue(self.state, self.source(2))
        result = review.finish(self.state, slate["id"], self.decisions(slate))
        self.assertEqual(result["status"], "complete")
        self.assertEqual(review.peek(self.state, slate["id"])["rows"], [])
        empty = review.open_queue(self.state, self.source(0))
        self.assertEqual(empty["rows"], [])
        self.assertEqual(len(review.list_queues(self.state)["queues"]), 2)

    def test_saved_progress_survives_fresh_process_and_does_not_claim_mailbox_changes(self):
        slate = review.open_queue(self.state, self.source())
        review.finish(self.state, slate["id"], self.decisions(slate))
        raw = subprocess.check_output([sys.executable, "-B", str(ROOT / "skills/review-inbox-emailbutler/scripts/review.py"), "peek", "--state-dir", str(self.state), "--id", slate["id"]], text=True)
        resumed = json.loads(raw)
        self.assertEqual(resumed["rows"][0]["id"], "message-15")
        self.assertFalse(resumed["mailbox_changed"])


if __name__ == "__main__":
    unittest.main()
