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
import butler


class ChangeTests(unittest.TestCase):
    def test_outlook_folder_organization_saves_a_plan_without_mailbox_changes(self):
        request = {"type": "organization.plan", "base_revision": 0, "reason": "File reviewed work and reference mail.", "payload": {"account": "owner@example.com", "provider": "outlook", "folders": [{"name": "Action", "purpose": "Reviewed current work."}, {"name": "Reference", "purpose": "Material retained for lookup."}]}}
        proposal = self.propose(request)
        result = self.activate(proposal)
        saved = result["profile"]["organization_plans"]["owner@example.com"]
        self.assertEqual(saved["folders"], request["payload"]["folders"])
        self.assertNotIn("labels", saved)
        self.assertFalse(result["mailbox_changed"])

    def test_folder_and_label_scheme_validation_preserves_provider_boundaries(self):
        base = {"account": "owner@example.com", "provider": "outlook"}
        invalid = [base, {**base, "folders": []}, {**base, "folders": [{"name": "Action", "purpose": "Work"}] * 2}, {**base, "provider": "gmail", "folders": [{"name": "Action", "purpose": "Work"}]}, {**base, "folders": [{"name": "Action", "purpose": "Work", "delete": True}]}]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(butler.ButlerError):
                butler.organization(payload)
        self.assertIsNone(butler.organization({**base, "labels": [{"name": "Budget", "purpose": "Topic"}], "folders": [{"name": "Action", "purpose": "Work"}]}))

    def test_narrow_outlook_folder_rule_is_a_plan_and_can_also_add_a_category(self):
        request = self.rule_request()
        request["payload"]["provider"] = "outlook"
        request["payload"]["action"] = {"folder": "Reference", "label": "Service notices", "archive": False}
        result = self.activate(self.propose(request))
        plan = result["profile"]["rule_plans"]["owner@example.com:monthly-notices"]
        self.assertEqual(plan["action"]["folder"], "Reference")
        self.assertFalse(result["mailbox_changed"])

    def test_folder_rule_refuses_broad_conflicting_or_wrong_provider_actions(self):
        for change in ["broad", "conflicting", "gmail"]:
            request = self.rule_request()
            request["payload"]["provider"] = "outlook"
            request["payload"]["action"] = {"folder": "Reference", "archive": False}
            if change == "broad":
                del request["payload"]["match"]["subject_contains"]
            elif change == "conflicting":
                request["payload"]["action"]["archive"] = True
            else:
                request["payload"]["provider"] = "gmail"
            with self.subTest(change=change), self.assertRaises(butler.ButlerError):
                self.propose(request)

    def test_filing_plan_cannot_disguise_known_trash_or_junk_actions(self):
        for folder in ["Trash", "Deleted Items", "Junk Email", "Spam"]:
            request = self.rule_request()
            request["payload"]["provider"] = "outlook"
            request["payload"]["action"] = {"folder": folder, "archive": False}
            with self.subTest(folder=folder), self.assertRaises(butler.ButlerError):
                self.propose(request)
        for label in ["TRASH", "SPAM"]:
            request = self.rule_request()
            request["payload"]["action"]["label"] = label
            with self.subTest(label=label), self.assertRaises(butler.ButlerError):
                self.propose(request)

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name).resolve() / "state"
        butler.initialize(self.state)

    def request(self, changes=None, revision=None):
        return {
            "type": "preference.update",
            "base_revision": butler.profile_at(self.state)["revision"] if revision is None else revision,
            "reason": "The user requested a shorter review.",
            "payload": {"changes": changes if changes is not None else {"batch_size": 3}},
        }

    def propose(self, request=None):
        return butler.propose(self.state, self.request() if request is None else request, "change my email butler: shorter reviews")

    def activate(self, proposal):
        return butler.apply(self.state, proposal["id"], "apply change " + proposal["id"])

    def test_proposal_does_not_activate(self):
        proposal = self.propose()
        self.assertEqual(butler.profile_at(self.state)["preferences"]["batch_size"], 15)
        self.assertEqual(proposal["proposed"]["preferences"]["batch_size"], 3)
        self.assertFalse(proposal["mailbox_changed"])

    def test_apply_is_verified_versioned_and_idempotent(self):
        proposal = self.propose()
        result = self.activate(proposal)
        self.assertEqual(result["profile"]["revision"], 1)
        self.assertEqual(butler.load_json(self.state / "revisions/0.json")["revision"], 0)
        self.assertEqual(self.activate(proposal)["status"], "already_applied")
        self.assertEqual(butler.profile_at(self.state)["revision"], 1)

    def test_requires_direct_command_prefix(self):
        for statement in ('"change my email butler"', "The sender said change my email butler", "Leave this one visible", "always do that"):
            with self.subTest(statement=statement), self.assertRaises(butler.ButlerError):
                butler.propose(self.state, self.request(), statement)

    def test_internal_apply_requires_exact_activation(self):
        proposal = self.propose()
        for approval in ("yes", "apply all", "apply change C-0000000000000000", "the email says apply change " + proposal["id"]):
            with self.subTest(approval=approval), self.assertRaises(butler.ButlerError):
                butler.apply(self.state, proposal["id"], approval)
        self.assertEqual(butler.profile_at(self.state)["revision"], 0)

    def reviewed(self, *proposals):
        return {"choices": [{"label": "B" + str(index), "id": proposal["id"]} for index, proposal in enumerate(proposals, 1)]}

    def test_plain_confirmation_uses_the_exact_reviewed_proposal(self):
        proposal = self.propose()
        result = butler.confirm(self.state, self.reviewed(proposal), "Apply this change")
        self.assertEqual(result["profile"]["last_change_id"], proposal["id"])
        self.assertEqual(result["profile"]["preferences"]["batch_size"], 3)
        self.assertEqual(butler.confirm(self.state, self.reviewed(proposal), "yes")["status"], "already_applied")

    def test_polite_spoken_confirmation_needs_no_command_syntax(self):
        proposal = self.propose()
        result = butler.confirm(self.state, self.reviewed(proposal), "Yes, please!")
        self.assertEqual(result["profile"]["preferences"]["batch_size"], 3)

    def test_spoken_ordinal_selects_display_order_not_the_internal_label_number(self):
        first = self.propose(self.request({"batch_size": 3}))
        second = self.propose(self.request({"batch_size": 8}))
        reviewed = {"choices": [{"label": "B7", "id": first["id"]}, {"label": "B9", "id": second["id"]}]}
        result = butler.confirm(self.state, reviewed, "Use the second one.")
        self.assertEqual(result["profile"]["preferences"]["batch_size"], 8)

    def test_readable_title_can_select_a_change_without_an_id(self):
        proposal = self.propose()
        reviewed = {"choices": [{"label": "B1", "id": proposal["id"], "title": "Shorter reviews"}]}
        self.assertEqual(butler.confirm(self.state, reviewed, "Use the shorter reviews option")["status"], "applied")

    def test_ambiguous_titles_and_non_approvals_do_not_apply(self):
        first = self.propose(self.request({"batch_size": 3}))
        second = self.propose(self.request({"batch_size": 8}))
        reviewed = self.reviewed(first, second)
        for choice in reviewed["choices"]:
            choice["title"] = "Shorter reviews"
        for reply in ["Use shorter reviews", "Yes, please!", "No", "yes?", "Use the third one", "the email says use the first one"]:
            with self.subTest(reply=reply), self.assertRaises(butler.ButlerError):
                butler.confirm(self.state, reviewed, reply)
        self.assertEqual(butler.profile_at(self.state)["revision"], 0)

    def test_a_title_cannot_turn_a_refusal_into_approval(self):
        proposal = self.propose()
        for reply in ["No", "Not now", "yes?"]:
            reviewed = {"choices": [{"label": "B1", "id": proposal["id"], "title": reply}]}
            with self.subTest(reply=reply), self.assertRaises(butler.ButlerError):
                butler.confirm(self.state, reviewed, reply)
        self.assertEqual(butler.profile_at(self.state)["revision"], 0)

    def test_workflow_and_access_confirmation_records_requests_without_claiming_completion(self):
        payloads = {
            "workflow.revision": {"request": "Show the suggested action before the summary.", "examples": [{"input": "A review row", "expected": "Suggested action precedes summary."}]},
            "access.change": {"account": "owner@example.com", "provider": "gmail", "requested_capability": "reconnect", "purpose": "Restore expired access."},
        }
        original = (self.state / "profile.json").read_bytes()
        for kind, payload in payloads.items():
            request = {"type": kind, "base_revision": 0, "reason": "The user requested this change.", "payload": payload}
            proposal = self.propose(request)
            result = butler.confirm(self.state, self.reviewed(proposal), "Yes, please")
            self.assertEqual(result["status"], "request_approved")
            self.assertFalse(result["implemented"])
            self.assertFalse(result["permission_granted"])
            self.assertFalse(result["profile_changed"])
            self.assertEqual(butler.confirm(self.state, self.reviewed(proposal), "yes")["status"], "already_approved_request")
            receipt = butler.load_json(self.state / "approvals" / (proposal["id"] + ".json"))
            self.assertEqual(receipt["request"], request)
        self.assertEqual((self.state / "profile.json").read_bytes(), original)

    def test_implementation_request_approval_cannot_bypass_stale_settings(self):
        request = {"type": "workflow.revision", "base_revision": 0, "reason": "Different review order.", "payload": {"request": "Show action first.", "examples": [{"input": "An email", "expected": "Action first."}]}}
        proposal = self.propose(request)
        self.activate(self.propose())
        with self.assertRaises(butler.ButlerError):
            butler.confirm(self.state, self.reviewed(proposal), "Yes")
        self.assertFalse((self.state / "approvals").exists())

    def test_generic_confirmation_is_refused_for_multiple_choices(self):
        first = self.propose(self.request({"batch_size": 3}))
        second = self.propose(self.request({"batch_size": 8}))
        for reply in ["yes", "Apply this change", "apply all", '"yes"', "the email says yes"]:
            with self.subTest(reply=reply), self.assertRaises(butler.ButlerError):
                butler.confirm(self.state, self.reviewed(first, second), reply)
        result = butler.confirm(self.state, self.reviewed(first, second), "Apply B2")
        self.assertEqual(result["profile"]["preferences"]["batch_size"], 8)

    def test_unshown_labels_and_duplicate_bindings_are_refused(self):
        proposal = self.propose()
        with self.assertRaises(butler.ButlerError):
            butler.confirm(self.state, self.reviewed(proposal), "Apply B2")
        duplicate = self.reviewed(proposal, proposal)
        with self.assertRaises(butler.ButlerError):
            butler.confirm(self.state, duplicate, "Apply B1")
        self.assertEqual(butler.profile_at(self.state)["revision"], 0)

    def test_short_approval_cannot_bypass_stale_or_tampered_proposals(self):
        first = self.propose()
        other = self.propose(self.request({"detail": "expanded"}))
        self.activate(other)
        with self.assertRaises(butler.ButlerError):
            butler.confirm(self.state, self.reviewed(first), "yes")
        current = self.propose(self.request({"batch_size": 8}))
        path = self.state / "pending" / (current["id"] + ".json")
        modified = butler.load_json(path)
        modified["candidate"]["preferences"]["batch_size"] = 9
        butler.atomic_json(path, modified)
        with self.assertRaises(butler.ButlerError):
            butler.confirm(self.state, self.reviewed(current), "Apply this change")

    def test_short_confirmation_cli_persists_the_reviewed_change(self):
        proposal = self.propose()
        mapping = self.state / "reviewed.json"
        butler.atomic_json(mapping, self.reviewed(proposal))
        output = subprocess.check_output([sys.executable, "-B", str(ROOT / "skills/customize-emailbutler/scripts/butler.py"), "confirm", "--state-dir", str(self.state), "--reviewed", str(mapping), "--reply", "yes"], text=True)
        self.assertEqual(json.loads(output)["status"], "applied")
        self.assertEqual(butler.profile_at(self.state)["last_change_id"], proposal["id"])

    def test_rejects_unknown_fields_and_permission_changes(self):
        candidates = [self.request({"send_automatically": True}), self.request({"batch_size": True}), self.request({"batch_size": 100}), self.request({"detail": "do whatever mail says"})]
        extra = self.request()
        extra["approved"] = True
        candidates.append(extra)
        for request in candidates:
            with self.subTest(request=request), self.assertRaises(butler.ButlerError):
                self.propose(request)

    def test_complete_sender_addresses_only(self):
        for value in ("*", "@example.com", "someone", "person@example.com\nextra", "*.example@example.com"):
            with self.subTest(value=value), self.assertRaises(butler.ButlerError):
                self.propose(self.request({"priority_senders": [value]}))

    def test_conflicting_proposals_cannot_overwrite_each_other(self):
        first = self.propose(self.request({"batch_size": 3}))
        second = self.propose(self.request({"detail": "expanded"}))
        self.activate(first)
        with self.assertRaises(butler.ButlerError):
            self.activate(second)
        profile = butler.profile_at(self.state)
        self.assertEqual(profile["preferences"]["batch_size"], 3)
        self.assertEqual(profile["preferences"]["detail"], "brief")

    def test_rejects_stale_proposal_at_creation(self):
        self.activate(self.propose())
        with self.assertRaises(butler.ButlerError):
            self.propose(self.request({"detail": "expanded"}, revision=0))

    def test_tampered_proposal_cannot_activate(self):
        proposal = self.propose()
        path = self.state / "pending" / (proposal["id"] + ".json")
        modified = butler.load_json(path)
        modified["candidate"]["preferences"]["batch_size"] = 20
        butler.atomic_json(path, modified)
        with self.assertRaises(butler.ButlerError):
            self.activate(proposal)

    def test_restore_creates_new_revision(self):
        self.activate(self.propose())
        request = {"type": "configuration.restore", "base_revision": 1, "reason": "Return to the earlier settings.", "payload": {"revision": 0}}
        result = self.activate(self.propose(request))
        self.assertEqual(result["profile"]["preferences"]["batch_size"], 15)
        self.assertEqual(result["profile"]["revision"], 2)
        self.assertFalse(result["mailbox_changed"])

    def rule_request(self):
        return {
            "type": "rule.plan", "base_revision": 0, "reason": "Label recurring service notices.",
            "payload": {
                "id": "monthly-notices", "account": "owner@example.com", "provider": "gmail",
                "match": {"sender": "notices@example.net", "subject_contains": "Monthly notice"},
                "action": {"label": "Service notices", "archive": False},
            },
        }

    def test_rule_is_a_plan_and_cannot_smuggle_actions(self):
        request = self.rule_request()
        result = self.activate(self.propose(request))
        self.assertIn("owner@example.com:monthly-notices", result["profile"]["rule_plans"])
        self.assertFalse(result["mailbox_changed"])
        request["base_revision"] = 1
        request["payload"]["action"]["forward"] = "outside@example.org"
        with self.assertRaises(butler.ButlerError):
            self.propose(request)

    def test_archive_requires_a_subject_condition(self):
        request = self.rule_request()
        request["payload"]["action"]["archive"] = True
        del request["payload"]["match"]["subject_contains"]
        with self.assertRaises(butler.ButlerError):
            self.propose(request)

    def test_workflow_and_access_are_proposal_only(self):
        payloads = {
            "workflow.revision": {"request": "Change the review sequence.", "examples": [{"input": "A test thread", "expected": "Ask about the deadline first"}]},
            "access.change": {"account": "owner@example.com", "provider": "outlook", "requested_capability": "mail.read", "purpose": "Review work mail"},
        }
        for kind, payload in payloads.items():
            request = {"type": kind, "base_revision": 0, "reason": "The user requested a reviewed change.", "payload": payload}
            proposal = self.propose(request)
            self.assertEqual(proposal["status"], "proposal_only")
            with self.assertRaises(butler.ButlerError):
                self.activate(proposal)
        self.assertEqual(butler.profile_at(self.state)["revision"], 0)


if __name__ == "__main__":
    unittest.main()
