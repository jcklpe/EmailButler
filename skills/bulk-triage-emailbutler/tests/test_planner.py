import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
for owner in ("setup", "customize", "bulk-triage"):
    sys.path.insert(0, str(ROOT / "skills" / (owner + "-emailbutler") / "scripts"))
import bulk
import butler
import capabilities
import execute_bulk
import plan_bulk
import private_state as b
from test_executor import Connector


def inventory(provider="gmail"):
    names = [name for name in capabilities.CONTRACTS if name.startswith(provider + ".")]
    operations = {name: {"status": "exposed", "tool": name, "contract": capabilities.CONTRACTS[name], "basis": "Synthetic saved schema, not a real account", "limits": {"max_items": 100, "max_page_size": 100}} for name in names}
    return {"schema_version": 1, "platform": "openai", "host": {name: {"status": "available", "basis": "Synthetic programmable host"} for name in ("callable_tools", "process_io", "posix_shell")}, "accounts": [{"key": "example", "account": "owner@example.com", "provider": provider, "tools": names, "identity": {"status": "confirmed", "basis": "Synthetic identity"}, "access": {"status": "allowed", "basis": "Synthetic permissions"}, "operations": operations}]}


def request(count=23):
    return {"account_key": "example", "action": "move", "selection": "frozen_ids", "workload": {"selected_count": count}, "budget": {"max_calls": 5000, "scope": "execution"}}


class PlannerTests(unittest.TestCase):
    def test_tool_names_without_schema_evidence_never_authorize_a_route(self):
        names = {"platform": "openai", "accounts": [{"key": "example", "provider": "outlook", "tools": ["move_email", "list_messages", "get_profile"]}]}
        result = plan_bulk.plan(names, request())
        self.assertIsNone(result["recommended_route"])
        self.assertEqual(result["routes"][0]["status"], "needs_evidence")
        self.assertIsNone(result["routes"][0]["budget"])
        self.assertFalse(result["permission_granted"])

    def test_wrong_contract_missing_binding_and_wrong_provider_are_not_promoted(self):
        source = inventory()
        source["accounts"][0]["operations"]["gmail.modify"]["contract"] = "new-uninspected-version"
        self.assertEqual(plan_bulk.plan(source, request())["routes"][0]["status"], "needs_evidence")
        source["accounts"][0]["operations"]["gmail.modify"]["tool"] = "absent-tool"
        with self.assertRaises(b.ButlerError):
            plan_bulk.plan(source, request())
        source = inventory()
        source["accounts"][0]["operations"]["outlook.move"] = {"status": "unknown", "basis": "Wrong provider"}
        with self.assertRaises(b.ButlerError):
            plan_bulk.plan(source, request())

    def test_denied_access_and_wrong_identity_block_every_executable_route(self):
        for field, status in (("access", "denied"), ("identity", "mismatch")):
            source = inventory()
            source["accounts"][0][field]["status"] = status
            selected = request()
            selected["budget"]["max_calls"] = 1
            result = plan_bulk.plan(source, selected)
            self.assertEqual(result["routes"][0]["status"], "unavailable")
            self.assertEqual(result["routes"][2]["status"], "unavailable")
            self.assertEqual(result["planning_status"], "unavailable")
            self.assertEqual(result["routes"][0]["budget"]["assessment"], "exceeds_budget")
            self.assertIsNone(result["recommended_route"])

    def test_host_or_access_unknown_keeps_cost_forecast_conditional(self):
        source = inventory()
        source["host"]["process_io"]["status"] = "unavailable"
        result = plan_bulk.plan(source, request())
        self.assertEqual(result["routes"][0]["status"], "unavailable")
        self.assertEqual(result["routes"][2]["status"], "manual_only")
        self.assertIsNotNone(result["routes"][0]["budget"])
        source["accounts"][0]["access"]["status"] = "unknown"
        result = plan_bulk.plan(source, request())
        self.assertIsNone(result["recommended_route"])
        self.assertFalse(result["ready_to_execute"])

    def test_count_only_budget_has_request_size_range_and_honest_boundary(self):
        selected = request()
        selected["limits"] = {"batch_size": 10, "write_limit": 4}
        selected["budget"]["max_calls"] = 80
        result = plan_bulk.plan(inventory(), selected)
        estimate = result["routes"][0]["budget"]
        self.assertEqual(estimate["total_calls_with_reserve"], {"min": 55, "max": 71})
        self.assertEqual(estimate["assessment"], "estimated_fit")
        self.assertTrue(estimate["replan_after_freeze"])
        self.assertFalse(estimate["completion_guaranteed"])
        selected["budget"]["max_calls"] = 55
        self.assertEqual(plan_bulk.plan(inventory(), selected)["routes"][0]["budget"]["assessment"], "uncertain")
        selected["budget"]["max_calls"] = 50
        self.assertEqual(plan_bulk.plan(inventory(), selected)["routes"][0]["budget"]["assessment"], "exceeds_budget")

    def test_inspected_bounds_control_effective_worker_arguments(self):
        source = inventory("outlook")
        source["accounts"][0]["operations"]["outlook.list"]["limits"]["max_page_size"] = 2
        result = plan_bulk.plan(source, request(5))
        self.assertEqual(result["effective_limits"]["read_limit"], 2)
        self.assertEqual(result["routes"][0]["budget"]["execution_calls"]["reads"]["min"], 12)
        source["accounts"][0]["operations"]["outlook.list"]["limits"] = {}
        self.assertIsNone(plan_bulk.plan(source, request())["routes"][0]["budget"])

    def test_native_query_does_not_hide_internal_work_or_verification(self):
        selected = request()
        selected["selection"] = "dynamic_query"
        result = plan_bulk.plan(inventory(), selected)
        self.assertEqual(result["routes"][0]["status"], "not_applicable")
        native = result["routes"][1]
        self.assertEqual(native["host_dispatch_calls"], 1)
        self.assertIsNone(native["total_calls"])
        self.assertIsNone(native["provider_internal_requests"])
        self.assertEqual(native["status"], "needs_evidence")
        self.assertIsNone(result["recommended_route"])
        selected["action"] = "archive"
        self.assertEqual(plan_bulk.plan(inventory(), selected)["routes"][1]["status"], "not_applicable")

    def test_label_only_does_not_claim_inbox_filing(self):
        selected = request()
        selected["action"] = "label"
        self.assertFalse(plan_bulk.plan(inventory(), selected)["inbox_filing"])
        self.assertEqual(plan_bulk.plan(inventory("outlook"), selected)["routes"][0]["status"], "unavailable")

    def test_combined_budget_subtracts_actual_selection_and_reserves_retries(self):
        selected = request(3)
        selected["budget"] = {"scope": "selection_and_execution", "max_calls": 20, "selection_calls_spent": 5, "retry_reserve": 2}
        estimate = plan_bulk.plan(inventory(), selected)["routes"][0]["budget"]
        self.assertEqual(estimate["total_calls_with_reserve"]["min"], 16)
        self.assertEqual(estimate["execution_call_cap"], 15)
        selected["budget"]["max_calls"] = 4
        estimate = plan_bulk.plan(inventory(), selected)["routes"][0]["budget"]
        self.assertEqual(estimate["execution_call_cap"], 0)
        self.assertEqual(estimate["assessment"], "exceeds_budget")

    def test_unknown_page_count_and_underfilled_pages_do_not_get_a_false_bound(self):
        selected = request(1203)
        selected["budget"]["scope"] = "selection_and_execution"
        result = plan_bulk.plan(inventory(), selected)["routes"][0]["budget"]
        self.assertEqual(result["selection_calls"]["min"], 15)
        self.assertIsNone(result["selection_calls"]["max"])
        self.assertIsNone(result["execution_call_cap"])
        self.assertIsNone(result["elapsed_time_estimate"])
        selected["workload"]["enumeration_pages"] = 1
        with self.assertRaises(b.ButlerError):
            plan_bulk.plan(inventory(), selected)

    def test_unrecognized_fields_and_malformed_numeric_inputs_stop_offline(self):
        for path, value in (("selected_count", True), ("selected_count", -1), ("job_sizes", [2])):
            selected = request(3)
            selected["workload"][path] = value
            with self.subTest(path=path), self.assertRaises(b.ButlerError):
                plan_bulk.plan(inventory(), selected)
        source = inventory()
        source["accounts"][0]["operations"]["gmail.modify"]["limits"]["max_items"] = True
        with self.assertRaises(b.ButlerError):
            plan_bulk.plan(source, request())

    def test_exact_frozen_estimate_matches_real_worker_calls_using_fake_bindings(self):
        for provider in ("gmail", "outlook"):
            with self.subTest(provider=provider), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary).resolve()
                state = root / "state"
                butler.initialize(state)
                remote = Connector(provider, 23)
                scope = {"account": "owner@example.com", "provider": provider, "source": "inbox" if provider == "gmail" else "source", "destination": "Label_target" if provider == "gmail" else "destination", "action": "move", "selection": "Synthetic frozen group"}
                jobs, identity_files = [], {}
                for offset in range(0, 23, 10):
                    ids = list(remote.mail)[offset:offset + 10]
                    job = bulk.create(state, {"scope": scope, "ids": ids, "batch_size": 5})
                    jobs.append(job["id"])
                    if provider == "outlook":
                        path = root / (job["id"] + ".json")
                        b.atomic_json(path, {identity:remote.mail[identity]["internetMessageId"] for identity in ids})
                        identity_files[job["id"]] = str(path)
                run = execute_bulk.prepare(state, {"jobs": jobs, "identity_files": identity_files})
                before = {str(p):p.read_bytes() for p in state.rglob('*') if p.is_file()}
                frozen = plan_bulk.frozen_workload(state, run["run_id"])
                selected = request()
                selected.pop("workload")
                wrong = inventory(provider)
                wrong["accounts"][0]["account"] = "different@example.com"
                with self.assertRaises(b.ButlerError):
                    plan_bulk.plan(wrong, selected, frozen)
                result = plan_bulk.plan(inventory(provider), selected, frozen)
                self.assertEqual(before, {str(p):p.read_bytes() for p in state.rglob('*') if p.is_file()})
                estimated = result["routes"][0]["budget"]["total_calls_with_reserve"]
                self.assertEqual(estimated["min"], estimated["max"])
                actual = execute_bulk.Executor(state, run["run_id"], run["run_id"], remote, **result["worker_options"]).execute()
                self.assertEqual(actual["status"], "complete")
                self.assertEqual(actual["connector_calls"], estimated["max"])
                self.assertEqual(plan_bulk.plan(inventory(provider), selected, plan_bulk.frozen_workload(state, run["run_id"]))["routes"][0]["status"], "needs_reconciliation")

    def test_frozen_identity_grouping_accounts_for_long_keys_and_account_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            state = root / "state"
            butler.initialize(state)
            remote = Connector("outlook", 9)
            for identity, message in remote.mail.items():
                message["internetMessageId"] = "<" + "x" * 900 + identity + "@example.com>"
            scope = {"account":"owner@example.com","provider":"outlook","source":"source","destination":"destination","action":"move","selection":"Synthetic long-key scope"}
            job = bulk.create(state, {"scope":scope,"ids":list(remote.mail),"batch_size":9})
            b.atomic_json(root / "keys.json", {identity:message["internetMessageId"] for identity,message in remote.mail.items()})
            run = execute_bulk.prepare(state, {"jobs":[job["id"]],"identity_files":{job["id"]:str(root / "keys.json")}})
            selected = request(); selected.pop("workload")
            result = plan_bulk.plan(inventory("outlook"), selected, plan_bulk.frozen_workload(state,run["run_id"]))
            self.assertEqual(result["routes"][0]["budget"]["execution_calls"]["reads"]["min"],12)
            guard_folder = execute_bulk.account_folder(state,scope)
            b.atomic_json(guard_folder / "write-guard.json", {"unsettled_run":"R-" + "f" * 32})
            blocked = plan_bulk.plan(inventory("outlook"), selected, plan_bulk.frozen_workload(state,run["run_id"]))
            self.assertEqual(blocked["routes"][0]["status"],"needs_reconciliation")
            self.assertIsNone(blocked["worker_options"])
            b.atomic_json(guard_folder / "write-guard.json", {"unsettled_run":None,"retry_not_before":plan_bulk.time.time()+120})
            self.assertEqual(plan_bulk.plan(inventory("outlook"), selected, plan_bulk.frozen_workload(state,run["run_id"]))["routes"][0]["status"],"provider_wait")
            b.atomic_json(guard_folder / "write-guard.json", {"unsettled_run":None})
            actual = execute_bulk.Executor(state,run["run_id"],run["run_id"],remote,**result["worker_options"]).execute()
            self.assertEqual(actual["connector_calls"],result["routes"][0]["budget"]["total_calls_with_reserve"]["max"])
            b.atomic_json(state / "bulk-runs" / run["run_id"] / (job["id"] + ".keys.json"),{})
            with self.assertRaises(b.ButlerError):
                plan_bulk.frozen_workload(state,run["run_id"])

    def test_zero_selection_has_no_execution_calls_but_keeps_selection_setup(self):
        selected=request(0)
        selected["budget"]["scope"]="selection_and_execution"
        estimate=plan_bulk.plan(inventory(),selected)["routes"][0]["budget"]
        self.assertEqual(estimate["execution_calls"]["setup"],0)
        self.assertEqual(estimate["selection_calls"]["min"],3)

    def test_large_count_estimate_needs_no_message_enumeration(self):
        selected=request(20000)
        selected["budget"]["max_calls"]=1000
        result=plan_bulk.plan(inventory(),selected)
        self.assertEqual(result["routes"][0]["budget"]["total_calls_with_reserve"],{"min":40202,"max":60002})
        self.assertEqual(result["routes"][0]["budget"]["assessment"],"exceeds_budget")
        self.assertIsNone(result["worker_options"])
        self.assertIsNone(result["recommended_route"])

    def test_confirmed_identity_cannot_use_an_alias_without_its_account_address(self):
        source=inventory()
        source["accounts"][0].pop("account")
        with self.assertRaises(b.ButlerError):
            plan_bulk.plan(source,request())
        source["accounts"][0]["identity"]["status"]="unknown"
        result=plan_bulk.plan(source,request())
        self.assertEqual(result["planning_status"],"needs_evidence")
        self.assertIsNone(result["account"])
        self.assertIsNone(result["worker_options"])

    def test_cli_uses_only_saved_files_and_reports_no_permission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            b.atomic_json(root / "inventory.json", inventory())
            b.atomic_json(root / "request.json", request())
            command = [sys.executable, "-B", str(ROOT / "skills/bulk-triage-emailbutler/scripts/plan_bulk.py"), "--inventory", str(root / "inventory.json"), "--request", str(root / "request.json")]
            result = json.loads(subprocess.check_output(command, text=True))
            self.assertFalse(result["ready_to_execute"])
            self.assertFalse(result["mailbox_changed"])
            self.assertEqual(sorted(path.name for path in root.iterdir()), ["inventory.json", "request.json"])


if __name__ == "__main__":
    unittest.main()
