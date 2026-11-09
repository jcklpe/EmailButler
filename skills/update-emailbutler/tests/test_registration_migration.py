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
import install_skill
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
        result = install_skill.install(ROOT, "codex", self.skills, execute=True, migrate_registrations=True)
        self.assertTrue(result["retire_legacy"])
        self.assertFalse((self.skills / "email-butler").is_symlink())
        self.assertEqual(len(result["registrations"]), 5)
        for name in self.names:
            self.assertEqual((self.skills / name).resolve(), ROOT / "skills" / name)
        self.assertEqual(install_skill.install(ROOT, "codex", self.skills, execute=True)["status"], "already_registered")

    def test_ordinary_setup_returns_update_handoff_without_changing_legacy_installation(self):
        self.skills.mkdir()
        legacy = self.skills / "email-butler"
        legacy.symlink_to(ROOT)
        for execute in (False, True):
            with self.subTest(execute=execute):
                result = install_skill.install(ROOT, "codex", self.skills, execute=execute)
                self.assertEqual(result["status"], "update_required")
                self.assertEqual(result["next_skill"], "update-emailbutler")
                self.assertFalse(result["retire_legacy"])
                self.assertFalse(result["migration_requested"])
                self.assertEqual(list(self.skills.iterdir()), [legacy])
                self.assertEqual(legacy.resolve(), ROOT)

    def test_relocated_registration_cannot_be_replaced_without_migration_mode(self):
        previous = self.base / "old-checkout"
        self.skills.mkdir()
        existing = self.skills / self.names[0]
        old_target = previous / "skills" / self.names[0]
        existing.symlink_to(old_target)
        with self.assertRaisesRegex(install_skill.InstallError, "Update EmailButler"):
            install_skill.install(ROOT, "codex", self.skills, execute=True, previous_source=previous)
        self.assertEqual(list(self.skills.iterdir()), [existing])
        self.assertEqual(existing.readlink(), old_target)

    def test_explicit_migration_still_preserves_an_unrecognized_legacy_copy(self):
        legacy = self.skills / "email-butler"
        legacy.mkdir(parents=True)
        marker = legacy / "SKILL.md"
        marker.write_text("A separately maintained installation")
        with self.assertRaises(install_skill.InstallError):
            install_skill.install(ROOT, "codex", self.skills, execute=True, migrate_registrations=True)
        self.assertEqual(list(self.skills.iterdir()), [legacy])
        self.assertEqual(marker.read_text(), "A separately maintained installation")

    def test_cli_separates_setup_handoff_migration_preview_and_execution(self):
        self.skills.mkdir()
        legacy = self.skills / "email-butler"
        legacy.symlink_to(ROOT)
        command = [sys.executable, "-B", str(ROOT / "skills/setup-emailbutler/scripts/install_skill.py"), "--client", "codex", "--skills-dir", str(self.skills)]
        ordinary = json.loads(subprocess.check_output(command + ["--execute"], text=True))
        self.assertEqual(ordinary["status"], "update_required")
        self.assertEqual(list(self.skills.iterdir()), [legacy])
        preview = json.loads(subprocess.check_output(command + ["--migrate-registrations"], text=True))
        self.assertEqual(preview["status"], "preview")
        self.assertTrue(preview["retire_legacy"])
        self.assertEqual(list(self.skills.iterdir()), [legacy])
        migrated = json.loads(subprocess.check_output(command + ["--migrate-registrations", "--execute"], text=True))
        self.assertEqual(migrated["status"], "registered")
        self.assertFalse(legacy.is_symlink())
        repeated = json.loads(subprocess.check_output(command + ["--migrate-registrations", "--execute"], text=True))
        self.assertEqual(repeated["status"], "already_registered")

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
            install_skill.install(ROOT, "codex", self.skills, execute=True, migrate_registrations=True)
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
        install_skill.install(ROOT, "codex", self.skills, execute=True, previous_source=previous, migrate_registrations=True)
        for name in self.names:
            self.assertEqual((self.skills / name).resolve(), ROOT / "skills" / name)


if __name__ == "__main__":
    unittest.main()
