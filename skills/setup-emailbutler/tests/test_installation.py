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


class InstallationTests(unittest.TestCase):
    def test_preview_changes_no_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / "skills"
            result = install_skill.install(ROOT, "codex", target)
            self.assertEqual(result["status"], "preview")
            self.assertFalse(target.exists())
            self.assertFalse(result["discovery_verified"])

    def test_registration_is_idempotent_and_keeps_checkout_as_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary).resolve() / "skills"
            result = install_skill.install(ROOT, "codex", target, execute=True)
            self.assertEqual(result["status"], "registered")
            self.assertFalse((target / "email-butler").exists())
            for entry in result["registrations"]:
                self.assertEqual(Path(entry["destination"]).resolve(), Path(entry["source"]))
            self.assertEqual(install_skill.install(ROOT, "codex", target, execute=True)["status"], "already_registered")
            self.assertFalse(result["connectors_changed"])

    def test_existing_skill_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            target = base / "email-butler"
            target.mkdir()
            human_file = target / "SKILL.md"
            human_file.write_text("User-maintained skill", encoding="utf-8")
            with self.assertRaises(install_skill.InstallError):
                install_skill.install(ROOT, "codex", base, execute=True)
            self.assertEqual(human_file.read_text(), "User-maintained skill")

    def test_dangling_link_is_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            target = base / "email-butler"
            target.symlink_to(base / "missing", target_is_directory=True)
            with self.assertRaises(install_skill.InstallError):
                install_skill.install(ROOT, "claude-code", base, execute=True)
            self.assertTrue(target.is_symlink())
            self.assertEqual(target.readlink(), base / "missing")

    def test_missing_runtime_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            with self.assertRaises(install_skill.InstallError):
                install_skill.install(base, "codex", base / "skills", execute=True)
            self.assertFalse((base / "skills").exists())

    def test_client_defaults_are_isolated(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(Path, "home", return_value=Path(temporary).resolve()):
            codex = install_skill.install(ROOT, "codex")
            claude = install_skill.install(ROOT, "claude-code")
            self.assertTrue(codex["destination"].endswith(".agents/skills"))
            self.assertTrue(claude["destination"].endswith(".claude/skills"))


class PackageLocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.skills = self.base / "skills"
        self.names = package_info.load_package(ROOT)["skills"]

    def test_registered_skill_can_find_shared_resources_from_another_cwd(self):
        install_skill.install(ROOT, "claude-code", self.skills, execute=True)
        discovered = self.skills / self.names[1]
        result = package_info.locate(discovered)
        self.assertEqual(Path(result["root"]), ROOT)
        output = subprocess.check_output([sys.executable, "-B", str(ROOT / "skills/setup-emailbutler/scripts/package_info.py"), "--skill-dir", str(discovered)], cwd=self.base, text=True)
        self.assertEqual(json.loads(output)["name"], "EmailButler")


    def test_registered_helpers_resolve_sibling_dependencies_from_another_cwd(self):
        install_skill.install(ROOT, "codex", self.skills, execute=True)
        helpers = {
            "setup-emailbutler": ["package_info.py", "capabilities.py", "onboarding.py", "install_skill.py"],
            "customize-emailbutler": ["butler.py"],
            "bulk-triage-emailbutler": ["bulk.py"],
            "review-inbox-emailbutler": ["review.py", "journal.py"],
        }
        for name, scripts in helpers.items():
            for script in scripts:
                with self.subTest(skill=name, script=script):
                    subprocess.check_output([sys.executable, "-B", str(self.skills / name / "scripts" / script), "--help"], cwd=self.base, text=True)

    def test_registered_profile_initializer_preserves_existing_private_settings(self):
        install_skill.install(ROOT, "codex", self.skills, execute=True)
        state = self.base / "private-state"
        command = [sys.executable, "-B", str(self.skills / "customize-emailbutler" / "scripts" / "butler.py"), "init", "--state-dir", str(state)]
        first = json.loads(subprocess.check_output(command, cwd=self.base, text=True))
        self.assertEqual(first["status"], "initialized")
        self.assertEqual(first["profile"]["preferences"]["batch_size"], 15)
        existing = first["profile"]
        existing["preferences"]["batch_size"] = 5
        (state / "profile.json").write_text(json.dumps(existing))
        second = json.loads(subprocess.check_output(command, cwd=self.base, text=True))
        self.assertEqual(second["status"], "existing")
        self.assertEqual(second["profile"], existing)


if __name__ == "__main__":
    unittest.main()
