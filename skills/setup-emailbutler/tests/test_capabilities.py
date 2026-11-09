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
import capabilities


class CapabilityTests(unittest.TestCase):
    def account(self, platform, provider, tools):
        return capabilities.classify({"platform": platform, "accounts": [{"key": "example", "provider": provider, "tools": tools}]})

    def test_claude_categories_do_not_imply_outlook_filing_or_rule_inspection(self):
        report = self.account("claude", "outlook", ["outlook_email_search", "read_resource", "outlook_modify_labels", "outlook_create_filter", "outlook_delete_filter"])
        checks = report["accounts"][0]["checks"]
        self.assertTrue(checks["review"]["known_names_present"])
        self.assertIn("archive", checks["filing"]["missing_known_operations"])
        self.assertEqual(checks["native_rule_lifecycle"]["missing_known_operations"], ["rules.inspect"])
        self.assertFalse(report["live_verified"])

    def test_openai_filing_does_not_imply_persistent_rules(self):
        report = self.account("openai", "outlook", ["mcp__codex_apps__microsoft_outlook_email_search_messages", "fetch_message", "set_message_categories", "move_email"])
        checks = report["accounts"][0]["checks"]
        self.assertTrue(checks["filing"]["known_names_present"])
        self.assertFalse(checks["native_rule_lifecycle"]["known_names_present"])

    def test_unknown_tools_are_not_treated_as_verified(self):
        report = self.account("claude", "outlook", ["outlook_move_new_version"])
        self.assertEqual(report["accounts"][0]["unknown_tools_to_inspect"], ["outlook_move_new_version"])
        self.assertFalse(report["accounts"][0]["checks"]["filing"]["known_names_present"])


if __name__ == "__main__":
    unittest.main()
