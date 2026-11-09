import copy
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
for owner in ("setup", "customize", "bulk-triage"):
    sys.path.insert(0, str(ROOT / "skills" / (owner + "-emailbutler") / "scripts"))
import bulk
import butler
import execute_bulk as execution
from freeze_bulk import freeze
import private_state as b
from test_executor import Connector


class FreezeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.state = Path(self.temporary.name).resolve() / "state"
        butler.initialize(self.state)

    def setup(self, provider="outlook", count=13):
        self.remote = Connector(provider, count)
        self.pages = 0
        self.request = {"scope": {"account": "owner@example.com", "provider": provider, "action": "move", "source": "source" if provider == "outlook" else "inbox", "destination": "destination" if provider == "outlook" else "Label_target", "selection": "Synthetic fixed eligibility and explicit exclusions"}, "query": "synthetic eligibility", "page_size": 50}

    def transport(self, request):
        operation, args = request["operation"], request["arguments"]
        if operation == "gmail.search":
            self.pages += 1
            ids = [key for key, row in self.remote.mail.items() if "INBOX" in row["labelIds"]]
            offset = int(args.get("next_page_token", "0"))
            end = offset + args["max_results"]
            return {"message_ids": ids[offset:end], "next_page_token": str(end) if end < len(ids) else None}
        if operation == "outlook.list" and args["filter"] == self.request["query"]:
            self.pages += 1
            rows = [copy.deepcopy(row) for row in self.remote.mail.values() if row["parentFolderId"] == args["folder_id"]]
            offset, end = args["skip"], args["skip"] + args["top"]
            return {"value": rows[offset:end], "has_more": end < len(rows), "next_from_index": end}
        return self.remote(request)

    def test_all_selection_pages_and_recovery_keys_are_frozen_without_any_write(self):
        self.setup(count=1203)
        self.request["exclude_ids"] = ["m3", "m7", "m1000"]
        result = freeze(self.state, self.request, self.transport, execution.prepare)
        self.assertEqual(result["selected"], 1200)
        self.assertEqual(result["excluded"], 3)
        self.assertEqual(result["jobs"], 2)
        self.assertEqual(self.pages, 25)
        self.assertFalse(self.remote.moves)
        plan = b.load_json(execution.run_dir(self.state, result["run_id"]) / "plan.json")
        ids = set()
        for item in plan["jobs"]:
            job = bulk.load(self.state, item["id"])
            ids.update(job["selection"]["ids"])
            self.assertLessEqual(len(job["selection"]["ids"]), 1000)
        self.assertFalse(ids.intersection(self.request["exclude_ids"]))

    def test_frozen_plan_executes_without_model_reconstructing_message_ids(self):
        self.setup("gmail", count=23)
        self.request["exclude_ids"] = ["m0"]
        result = freeze(self.state, self.request, self.transport, execution.prepare)
        finished = execution.Executor(self.state, result["run_id"], result["run_id"], self.remote).execute()
        self.assertEqual(finished["counts"]["verified"], 22)
        self.assertEqual(self.remote.moves["m0"], 0)
        self.assertEqual(self.remote.calls["gmail.modify"], 1)

    def test_duplicate_pages_and_missing_stable_keys_stop_before_jobs_are_created(self):
        self.setup()
        original = self.transport
        def duplicate(request):
            page = original(request)
            if request["operation"] == "outlook.list":
                page["value"].append(copy.deepcopy(page["value"][0]))
            return page
        with self.assertRaises(b.ButlerError):
            freeze(self.state, self.request, duplicate, execution.prepare)
        self.assertFalse((self.state / "bulk").exists())
        self.remote.mail["m0"].pop("internetMessageId")
        with self.assertRaises(b.ButlerError):
            freeze(self.state, self.request, original, execution.prepare)
        self.assertFalse((self.state / "bulk").exists())

    def test_selection_budget_exhaustion_never_materializes_a_partial_operation(self):
        self.setup(count=1203)
        with self.assertRaises(b.ButlerError):
            freeze(self.state, self.request, self.transport, execution.prepare, max_calls=2)
        self.assertFalse((self.state / "bulk").exists())
        self.assertFalse(self.remote.moves)

    def test_read_only_throttle_is_preserved_across_a_new_freeze(self):
        self.setup()
        calls = []
        def throttled(request):
            calls.append(request["operation"])
            return {"error": {"status": 429, "retry_after": 120}}
        with self.assertRaises(execution.ConnectorFailure):
            freeze(self.state, self.request, throttled, execution.prepare)
        self.assertEqual(calls, ["outlook.profile"])
        calls.clear()
        with self.assertRaises(b.ButlerError):
            freeze(self.state, self.request, throttled, execution.prepare)
        self.assertEqual(calls, [])
        self.assertFalse((self.state / "bulk").exists())

    def test_empty_page_with_a_valid_cursor_continues(self):
        self.setup()
        original = self.transport
        empty_sent = False
        def page(request):
            nonlocal empty_sent
            if request["operation"] == "outlook.list" and not empty_sent:
                empty_sent = True
                return {"value": [], "has_more": True, "next_from_index": 10}
            if request["operation"] == "outlook.list":
                adjusted = copy.deepcopy(request)
                adjusted["arguments"]["skip"] = 0
                return original(adjusted)
            return original(request)
        result = freeze(self.state, self.request, page, execution.prepare)
        self.assertEqual(result["selected"], 13)

    def test_new_freeze_has_new_history_when_messages_return_to_the_source(self):
        self.setup("gmail", count=3)
        first = freeze(self.state, self.request, self.transport, execution.prepare)
        self.assertEqual(execution.Executor(self.state, first["run_id"], first["run_id"], self.remote).execute()["status"], "complete")
        for message in self.remote.mail.values():
            message["labelIds"].append("INBOX")
        second = freeze(self.state, self.request, self.transport, execution.prepare)
        self.assertNotEqual(first["run_id"], second["run_id"])
        plan = b.load_json(execution.run_dir(self.state, second["run_id"]) / "plan.json")
        self.assertEqual(bulk.status(self.state, plan["jobs"][0]["id"])["counts"]["pending"], 3)


if __name__ == "__main__":
    unittest.main()
