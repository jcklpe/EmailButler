# Offline operation planning
Read this before choosing a large-cleanup route or promising a call budget. `scripts/plan_bulk.py` reads saved evidence and counts or existing frozen jobs. It never calls connectors, changes settings, creates jobs, writes private state or grants approval. Setup owns evidence normalization in `scripts/capabilities.py`; Bulk owns route decisions and budget estimates. This is part of Bulk Triage, not another skill or an external manifest standard.

## Record evidence without promoting names to proof
Legacy tool-name inventories remain accepted discovery hints. They cannot establish a usable execution route. The versioned inventory adds inspected adapter contracts, bounds, account/access evidence and host support. These are recorded claims with a basis; the helper does not inspect the live host or validate the underlying tool schemas itself. Recheck them in the actual recipient environment before use. Unknown and unavailable are distinct.

Example shape, using synthetic evidence and illustrative short tool names:
```json
{
  "schema_version": 1,
  "platform": "openai",
  "host": {
    "callable_tools": {"status": "available", "basis": "Synthetic host inspection"},
    "process_io": {"status": "available", "basis": "Synthetic process bridge"},
    "posix_shell": {"status": "available", "basis": "Synthetic shell inspection"}
  },
  "accounts": [{
    "key": "work",
    "account": "owner@example.com",
    "provider": "gmail",
    "tools": ["get_profile", "list_labels", "read_email", "batch_modify_email"],
    "identity": {"status": "confirmed", "basis": "Synthetic selected identity"},
    "access": {"status": "allowed", "basis": "Synthetic account evidence"},
    "operations": {
      "gmail.profile": {"status": "exposed", "tool": "get_profile", "contract": "gmail.profile.v1", "basis": "Synthetic saved schema"},
      "gmail.labels": {"status": "exposed", "tool": "list_labels", "contract": "gmail.labels.v1", "basis": "Synthetic saved schema"},
      "gmail.read": {"status": "exposed", "tool": "read_email", "contract": "gmail.read.v1", "basis": "Synthetic minimal label-state read"},
      "gmail.modify": {"status": "exposed", "tool": "batch_modify_email", "contract": "gmail.modify.v1", "basis": "Synthetic exact-ID batch schema", "limits": {"max_items": 100}}
    }
  }]
}
```
Use actual saved callable names and source evidence. A confirmed identity needs its exact account address; a frozen operation must match that address and provider, not merely a friendly alias. Every exposed operation must bind to a tool present in that account's inventory. Identity statuses are confirmed/mismatch/unknown; access is allowed/denied/unknown; host support is available/unavailable/unknown; operations are exposed/unavailable/unknown. Omitted facts stay unknown. An unrecognized contract version needs inspection. No evidence field carries reusable permission.

The `.v1` identifiers describe EmailButler's current [adapter wire contracts](execution.md), not external product versions:

| Contract prefix | Required meaning |
| --- | --- |
| `gmail.profile`, `outlook.profile` | Selected-account identity response |
| `gmail.labels` | Existing label IDs and user/system type |
| `gmail.search` | Inbox-constrained exact message IDs with continuation tokens; record `max_page_size` |
| `gmail.read` | Minimal immutable message ID and current label IDs |
| `gmail.modify` | Exact message-ID label additions/removals; record `max_items` |
| `gmail.bulk_query` | Native query/label/archive action; internal requests and independent verification remain separate unknowns |
| `outlook.list` | Folder-scoped filters, Internet Message-ID/current ID metadata and explicit complete pagination; record `max_page_size` |
| `outlook.move` | One message per call, exact destination and returned move identity |
| `outlook.categories` | Category changes, which do not establish a move or independent category verification |

Bind a contract only after checking its arguments and result fields against the packaged adapter. The initial worker adapters support the recorded OpenAI wire schemas. A different platform or missing programmable bridge is a package-route limitation, not proof that the provider lacks a capability. Native query actions remain candidates until their exact scope, verification and recovery are understood; one host dispatch does not mean one provider request or a verified completed cleanup. An agent-mediated alternative has no automatic-continuation or worker-cost claim.

## Plan counts, then the actual frozen operation
Prepare a private request and inventory outside the checkout. Do not paste large ID lists, raw inventories or full reports into the conversation.
```json
{
  "account_key": "work",
  "action": "move",
  "selection": "frozen_ids",
  "workload": {"selected_count": 1200, "matched_count": 1203, "job_sizes": [1000, 200]},
  "limits": {"batch_size": 100, "read_limit": 20, "write_limit": 100, "page_size": 100},
  "budget": {"scope": "execution", "max_calls": 5000, "retry_reserve": 50, "max_seconds": 600}
}
```
Actions are move/archive/label; selection is frozen_ids/dynamic_query. `matched_count` includes excluded messages enumerated before freezing; selected_count is the target count. Job sizes must sum to selected_count. Omitted job sizes assume 1000-item jobs, with actual file-size splitting left to freeze. The estimate uses the lower of requested and inspected bounds; defaults are local working limits, not provider caps. A frozen run owns its actual jobs/batches, so remove workload from the request when using it.

```sh
python3 -B skills/bulk-triage-emailbutler/scripts/plan_bulk.py --inventory INVENTORY.json --request REQUEST.json
python3 -B skills/bulk-triage-emailbutler/scripts/plan_bulk.py --inventory INVENTORY.json --request FROZEN_REQUEST.json --state-dir PRIVATE_STATE --run-id RUN_ID
```
The second command reads the existing execution manifest, exact identity/key maps, job claims and checkpoints. It checks hashes, duplicates, scope and changes during reading without acquiring a writer lock. Started jobs or saved account write/cooldown guards block a fresh-operation forecast; use the existing recovery workflow first. It does not reconstruct an uncertain job as new work. Frozen metadata must still be current when execution begins.

## What the call estimate covers
The successful-pass model assumes all selected targets still need the approved action. Gmail includes profile/label setup, one minimal read per selected message before and after changes, and bounded label writes. Outlook includes profile setup, one move per message, and independent reads of both source and destination before and after each batch. Frozen identity maps use the adapter's actual encoded request-size grouping. Count-only reports span best packing to one item per request, with the stated batch/job assumptions. These are conditional estimates, not a measured throughput result or a hard completion bound.

Extra metadata pages, retries, throttling and interruption recovery can add work. `retry_reserve` is an explicit allowance, not a prediction. Reports keep these assumptions visible, never infer verification from an acknowledgement, and leave elapsed time, model-token usage and money unestimated. Underfilled or empty continuation pages prevent a reliable enumeration upper bound unless a supplied page count applies. A supplied count is still recorded evidence, not independently measured by this helper.

Budget scope execution covers one worker invocation. Scope selection_and_execution also covers enumeration: before freeze it forecasts page/setup calls; afterward supply `selection_calls_spent` from the actual freeze result. The remaining execution cap is the total budget minus that actual spending. It is not a second full allowance. Reserve calls remain within the cap. If actual spending exhausts it, do not start writes. Without actual spending, a combined-budget report cannot provide execution options.

The report returns estimated_fit, exceeds_budget or uncertain; estimated_fit is conditional on its stated assumptions. Replan from the actual frozen jobs before writes. For a supported unstarted frozen run with a fitting estimate, worker_options supplies max_calls/read_limit/write_limit and any explicit execution max_seconds. Feed those values to the [host bridge](execution.md#host-bridge-binding) rather than recomputing them. Preserve the existing current-scope approval, call/time stops and independent verification. Every report keeps ready_to_execute, permission_granted and live_verified false. A run ID, contract label or forecast does not authenticate consent.

## Keep the conversation short
Show the useful outcome, destination and agreed stopping limit. For example: “This estimate exceeds the call budget. Reduce the group or agree a larger limit.” If the estimate fits, explain only that it is an estimate and the cleanup may pause at the agreed limit. The full route matrix, binding names, formulas and assumptions are agent diagnostics. Expose a particular uncertainty only when it affects the user's choice; do not dump the report or ask the recipient to repair inventory files.
