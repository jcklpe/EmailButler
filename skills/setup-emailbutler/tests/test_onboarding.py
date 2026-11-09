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
import onboarding
import butler


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve() / "state"
        butler.initialize(self.root)
        self.account = {"account": "owner@example.com", "provider": "outlook"}

    def test_initial_cleanup_is_account_specific_and_deferral_is_not_completion(self):
        profile = (self.root / "profile.json").read_bytes()
        self.assertFalse(onboarding.check(self.root, [self.account])["cleanup_addressed"])
        outcome = {**self.account, "status": "deferred", "reason": "The user chose later", "evidence": "Direct-user decision in the current session"}
        onboarding.record(self.root, {"base_revision": 0, "outcome": outcome})
        result = onboarding.check(self.root, [self.account])
        self.assertTrue(result["cleanup_addressed"])
        self.assertFalse(result["cleanup_completed"])
        other = {"account": "other@example.com", "provider": "gmail"}
        self.assertFalse(onboarding.check(self.root, [self.account, other])["cleanup_addressed"])
        self.assertEqual((self.root / "profile.json").read_bytes(), profile)

    def test_onboarding_rejects_stale_updates_and_unsubstantiated_outcomes(self):
        outcome = {**self.account, "status": "active", "reason": "Approved group", "evidence": "Current preview approval"}
        onboarding.record(self.root, {"base_revision": 0, "outcome": outcome})
        with self.assertRaises(butler.ButlerError):
            onboarding.record(self.root, {"base_revision": 0, "outcome": {**outcome, "status": "completed"}})
        with self.assertRaises(butler.ButlerError):
            onboarding.record(self.root, {"base_revision": 1, "outcome": {**outcome, "status": "completed", "evidence": ""}})


if __name__ == "__main__":
    unittest.main()
