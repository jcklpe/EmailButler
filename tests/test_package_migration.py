from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import bulk
import butler
import install_skill
import onboarding
import package_info


class PackageMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.skills = self.base / "skills"
        self.names = package_info.load_package(ROOT)["skills"]

    def test_legacy_registration_is_replaced_only_after_all_new_links_exist(self):
        self.skills.mkdir()
        (self.skills / "email-butler").symlink_to(ROOT)
        result = install_skill.install(ROOT, "codex", self.skills, execute=True)
        self.assertTrue(result["retire_legacy"])
        self.assertFalse((self.skills / "email-butler").is_symlink())
        self.assertEqual(len(result["registrations"]), 5)
        for name in self.names:
            self.assertEqual((self.skills / name).resolve(), ROOT / "skills" / name)
        self.assertEqual(install_skill.install(ROOT, "codex", self.skills, execute=True)["status"], "already_registered")

    def test_one_conflict_leaves_the_whole_installation_untouched(self):
        target = self.skills / self.names[-1]
        target.mkdir(parents=True)
        (target / "SKILL.md").write_text("User's skill")
        with self.assertRaises(install_skill.InstallError):
            install_skill.install(ROOT, "codex", self.skills, execute=True)
        self.assertEqual(list(self.skills.iterdir()), [target])
        self.assertEqual((target / "SKILL.md").read_text(), "User's skill")

    def test_failed_link_creation_rolls_back_and_keeps_legacy_link(self):
        self.skills.mkdir()
        (self.skills / "email-butler").symlink_to(ROOT)
        original = Path.symlink_to
        def fail_second(path, target, *args, **kwargs):
            if path.name == self.names[1]:
                raise OSError("simulated filesystem failure")
            return original(path, target, *args, **kwargs)
        with patch.object(Path, "symlink_to", fail_second), self.assertRaises(install_skill.InstallError):
            install_skill.install(ROOT, "codex", self.skills, execute=True)
        self.assertEqual(list(self.skills.iterdir()), [self.skills / "email-butler"])
        self.assertEqual((self.skills / "email-butler").resolve(), ROOT)

    def test_relocation_repairs_only_explicitly_recognized_old_links(self):
        previous = self.base / "previous-checkout"
        self.skills.mkdir()
        (self.skills / "email-butler").symlink_to(previous)
        for name in self.names:
            (self.skills / name).symlink_to(previous / "skills" / name)
        with self.assertRaises(install_skill.InstallError):
            install_skill.install(ROOT, "codex", self.skills, execute=True)
        install_skill.install(ROOT, "codex", self.skills, execute=True, previous_source=previous)
        for name in self.names:
            self.assertEqual((self.skills / name).resolve(), ROOT / "skills" / name)

    def test_registered_skill_can_find_shared_resources_from_another_cwd(self):
        install_skill.install(ROOT, "claude-code", self.skills, execute=True)
        discovered = self.skills / self.names[1]
        result = package_info.locate(discovered)
        self.assertEqual(Path(result["root"]), ROOT)
        output = subprocess.check_output([sys.executable, "-B", str(discovered.resolve().parents[1] / "scripts/package_info.py"), "--skill-dir", str(discovered)], cwd=self.base, text=True)
        self.assertEqual(json.loads(output)["name"], "EmailButler")


class CheckpointTests(unittest.TestCase):
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
        output = subprocess.check_output([sys.executable, "-B", str(ROOT / "scripts/bulk.py"), "status", "--state-dir", str(self.root), "--id", view["id"]], text=True)
        self.assertEqual(json.loads(output)["next_batch"], [{"id": "mail-5", "current_id": "mail-5"}])

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
