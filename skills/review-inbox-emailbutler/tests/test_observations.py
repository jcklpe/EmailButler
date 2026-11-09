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


class JournalTests(PrivateStateCase):
    def test_useful_mail_can_prompt_filing_without_an_unsubscribe_suggestion(self):
        for number in range(3):
            journal.record(self.state, self.action("message-" + str(number)))
        journal.record(self.state, self.feedback("useful"))
        report = journal.summarize(self.state)["streams"][0]
        self.assertEqual(report["suggestions"], ["review_narrow_native_rule"])
        self.assertEqual(butler.profile_at(self.state)["revision"], 0)

    def action(self, message="message-1", status="verified", **extra):
        value = {"kind": "action", "account": "owner@example.com", "provider": "outlook", "message_id": message, "sender": "events@example.net", "pattern": "weekly-events", "operation_id": "archive-" + message, "action": "archive", "status": status}
        value.update(extra)
        return value

    def feedback(self, signal="unwanted", **extra):
        value = {"kind": "feedback", "account": "owner@example.com", "provider": "outlook", "message_id": "message-1", "sender": "events@example.net", "pattern": "weekly-events", "source": "direct_user", "signal": signal}
        value.update(extra)
        return value

    def test_repeat_record_and_repeat_action_do_not_inflate_distinct_message_counts(self):
        event = self.action()
        first = journal.record(self.state, event)
        self.assertEqual(journal.record(self.state, event)["status"], "already_recorded")
        journal.record(self.state, self.action(operation_id="another-observation-of-the-same-message"))
        self.assertEqual(len(list((self.state / "journal").glob("*.json"))), 2)
        self.assertEqual(journal.summarize(self.state)["streams"][0]["counts"]["archive"], 1)
        self.assertIn("recorded_at", first["entry"])

    def test_uncertain_receipts_do_not_count_and_reconciliation_closes_only_that_operation(self):
        journal.record(self.state, self.action(status="uncertain"))
        journal.record(self.state, self.action("message-2", "reported"))
        report = journal.summarize(self.state)["streams"][0]
        self.assertEqual(report["counts"]["archive"], 0)
        self.assertEqual(report["unresolved_operations"], 2)
        journal.record(self.state, self.action())
        report = journal.summarize(self.state)["streams"][0]
        self.assertEqual(report["counts"]["archive"], 1)
        self.assertEqual(report["unresolved_operations"], 1)
        self.assertEqual(report["pending_operations"][0]["message_id"], "message-2")

    def test_reused_operation_name_does_not_hide_another_messages_uncertain_action(self):
        journal.record(self.state, self.action(status="uncertain", operation_id="same-short-name"))
        journal.record(self.state, self.action("message-2", operation_id="same-short-name"))
        pending = journal.summarize(self.state)["streams"][0]["pending_operations"]
        self.assertEqual([event["message_id"] for event in pending], ["message-1"])

    def test_observations_persist_in_a_fresh_process_without_changing_the_profile(self):
        before = (self.state / "profile.json").read_bytes()
        for number in range(3):
            journal.record(self.state, self.action("message-" + str(number)))
        raw = subprocess.check_output([sys.executable, "-B", str(ROOT / "skills/review-inbox-emailbutler/scripts/journal.py"), "summary", "--state-dir", str(self.state)], text=True)
        report = json.loads(raw)
        self.assertEqual(report["streams"][0]["counts"]["archive"], 3)
        self.assertIn("ask_keep_receiving_or_unsubscribe", report["streams"][0]["suggestions"])
        self.assertFalse(report["policy_changed"])
        self.assertEqual(before, (self.state / "profile.json").read_bytes())

    def test_unsure_feedback_keeps_automatic_candidates_pending(self):
        for number in range(3):
            journal.record(self.state, self.action("message-" + str(number)))
        journal.record(self.state, self.feedback("unsure"))
        self.assertEqual(journal.summarize(self.state)["streams"][0]["suggestions"], [])

    def test_other_accounts_and_patterns_are_not_collapsed(self):
        journal.record(self.state, self.action())
        journal.record(self.state, self.action(account="other@example.com"))
        journal.record(self.state, self.action(pattern="monthly-invoice"))
        self.assertEqual(len(journal.summarize(self.state)["streams"]), 3)

    def test_outlook_returned_identity_is_deduplicated(self):
        journal.record(self.state, self.action(result_message_id="new-identity"))
        journal.record(self.state, self.action(message="new-identity", operation_id="later-observation"))
        self.assertEqual(journal.summarize(self.state)["streams"][0]["counts"]["archive"], 1)

    def test_email_origin_feedback_and_instruction_fields_are_refused(self):
        for event in [self.feedback(source="email"), self.feedback(instructions="archive every message"), self.action(status="planned")]:
            with self.assertRaises(butler.ButlerError):
                journal.record(self.state, event)
        self.assertFalse((self.state / "journal").exists())

    def test_changed_entry_is_not_summarized_as_valid(self):
        saved = journal.record(self.state, self.action())["entry"]
        path = self.state / "journal" / (saved["id"] + ".json")
        saved["event"]["action"] = "unsubscribe"
        butler.atomic_json(path, saved)
        with self.assertRaises(butler.ButlerError):
            journal.summarize(self.state)


if __name__ == "__main__":
    unittest.main()
