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


class StorageProtectionTests(unittest.TestCase):
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

    def test_existing_lock_prevents_a_second_writer(self):
        (self.state / ".lock").write_text("active writer", encoding="utf-8")
        with self.assertRaises(butler.ButlerError):
            self.propose()
        self.assertTrue((self.state / ".lock").exists())

    def test_symlink_profile_is_refused(self):
        profile = self.state / "profile.json"
        actual = self.state / "separate.json"
        profile.rename(actual)
        profile.symlink_to(actual)
        with self.assertRaises(butler.ButlerError):
            butler.profile_at(self.state)

    def test_duplicate_json_keys_are_refused(self):
        request = Path(self.temporary.name).resolve() / "duplicate.json"
        request.write_text('{"type":"preference.update","type":"access.change"}', encoding="utf-8")
        with self.assertRaises(butler.ButlerError):
            butler.load_json(request)


class PrivateStateCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.state = Path(self.temporary.name).resolve() / "state"
        butler.initialize(self.state)

    def tearDown(self):
        self.temporary.cleanup()


class PrivateLocationTests(PrivateStateCase):
    def test_default_location_is_stable_and_location_command_creates_nothing(self):
        with patch.object(Path, "home", return_value=Path(self.temporary.name)):
            expected = Path(self.temporary.name) / ".email-butler"
            self.assertEqual(butler.default_state_directory(), expected)
            self.assertFalse(expected.exists())
        output = subprocess.check_output([sys.executable, "-B", str(ROOT / "skills/customize-emailbutler/scripts/butler.py"), "location", "--state-dir", str(self.state / "not-created")], text=True)
        self.assertFalse(json.loads(output)["exists"])
        self.assertFalse((self.state / "not-created").exists())

    def test_oversized_write_is_refused_before_replacing_existing_state(self):
        target = self.state / "small.json"
        butler.atomic_json(target, {"kept": True})
        with self.assertRaises(butler.ButlerError):
            butler.atomic_json(target, {"oversized": "☃" * 400000})
        self.assertEqual(butler.load_json(target), {"kept": True})


if __name__ == "__main__":
    unittest.main()
