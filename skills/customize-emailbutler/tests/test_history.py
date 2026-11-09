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


class PrivateStateCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name).resolve() / "state"
        butler.initialize(self.state)

    def tearDown(self):
        self.temporary.cleanup()


class HistoryTests(PrivateStateCase):
    def preference(self, size):
        request = {"type": "preference.update", "base_revision": butler.profile_at(self.state)["revision"], "reason": "The user requested a different review size.", "payload": {"changes": {"batch_size": size}}}
        return butler.propose(self.state, request, "change my email butler: use smaller reviews")

    def test_pending_is_not_applied_history_and_applied_changes_are_reconstructed(self):
        pending = self.preference(3)
        self.assertEqual(butler.change_history(self.state)["changes"], [])
        butler.apply(self.state, pending["id"], "apply change " + pending["id"])
        history = butler.change_history(self.state)
        self.assertEqual(len(history["changes"]), 1)
        entry = history["changes"][0]
        self.assertEqual(entry["before"]["preferences"]["batch_size"], 15)
        self.assertEqual(entry["after"]["preferences"]["batch_size"], 3)
        self.assertEqual(entry["id"], pending["id"])

    def test_restore_is_a_new_history_entry_and_history_can_be_bounded(self):
        for size in [3, 4]:
            pending = self.preference(size)
            butler.apply(self.state, pending["id"], "apply change " + pending["id"])
        request = {"type": "configuration.restore", "base_revision": 2, "reason": "The user wants the original settings.", "payload": {"revision": 0}}
        pending = butler.propose(self.state, request, "change my email butler: go back to the original settings")
        butler.apply(self.state, pending["id"], "apply change " + pending["id"])
        history = butler.change_history(self.state, 1)
        self.assertEqual(history["revision"], 3)
        self.assertEqual(history["older_changes"], 2)
        self.assertEqual(history["changes"][0]["type"], "configuration.restore")
        self.assertEqual(history["changes"][0]["after"]["preferences"]["batch_size"], 15)

    def test_tampered_revision_is_not_reported_as_valid_history(self):
        pending = self.preference(3)
        butler.apply(self.state, pending["id"], "apply change " + pending["id"])
        value = butler.profile_at(self.state)
        value["preferences"]["batch_size"] = 4
        butler.atomic_json(self.state / "profile.json", value)
        with self.assertRaises(butler.ButlerError):
            butler.change_history(self.state)


if __name__ == "__main__":
    unittest.main()
