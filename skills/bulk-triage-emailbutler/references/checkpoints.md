# Bulk filing checkpoint contract
All commands run from the canonical package root. Resolve registered skill symlinks before locating shared resources; `skills/setup-emailbutler/scripts/package_info.py --skill-dir SKILL_DIRECTORY` reports the package root/version. Use the same private `--state-dir` across all helpers. These records contain no authorization and make no connector calls.

For per-account first-run outcomes, use [Setup’s outcome record](../../setup-emailbutler/SKILL.md#first-run-outcome-record). Record active before approved initial filing, then completed, deliberately deferred or blocked according to the evidence.

## Filing selection
Create a private selection file from actual connector identities and the exact approved preview:
```json
{
  "scope": {"account": "owner@example.com", "provider": "outlook", "action": "move", "source": "inbox", "destination": "provider-folder-id", "selection": "Exact frozen group with the reviewed cutoff and exclusions"},
  "ids": ["message-1", "message-2"],
  "batch_size": 5
}
```
`action` is move, archive or label. Resolve and verify the actual destination semantics, including localized system trash/junk names that a local validator cannot recognize. A label-only operation does not file mail out of Inbox. Up to 1000 distinct IDs fit one local job, with bounded encoded size; use smaller jobs for unusually long IDs/results. Larger approved operations can span multiple frozen jobs. A local storage limit is not a connector cap or a need to ask the same approval again.

```text
python3 -B skills/bulk-triage-emailbutler/scripts/bulk.py create --input SELECTION.json
python3 -B skills/bulk-triage-emailbutler/scripts/bulk.py status --id B-ID
python3 -B skills/bulk-triage-emailbutler/scripts/bulk.py record --id B-ID --input RESULTS.json
python3 -B skills/bulk-triage-emailbutler/scripts/bulk.py reconcile --id B-ID --input READBACK.json
```
The output supplies `next_batch`, counts, revision and blocked/complete/pending state. Each row has its original `id` and currently known `current_id`. Execute only the approved action on those current identities. Record the original ID plus returned identity where relevant:
```json
{"base_revision": 0, "results": [{"id": "message-1", "status": "verified", "evidence": "Independent destination metadata read", "result_id": "moved-message-1"}, {"id": "message-2", "status": "uncertain", "evidence": "The move request timed out"}]}
```
Every item in the current batch needs exactly one result. `verified` requires independent mailbox evidence; `reported`, `uncertain` and `failed` block the next write batch. Read back the checkpoint after recording. Reconcile unresolved rows through fresh reads; the same result format can set them to verified, failed or pending. Pending means the read established that retry is safe; preserve or update returned IDs as needed. Successful rows never reappear in the next batch. A stale revision, edited selection or foreign/duplicate identity is refused.

The helper does not call connectors, wait/retry, infer throttling, authenticate human approval, independently verify evidence or guarantee exactly-once remote writes. After a crash between a write and checkpoint save, inspect pending targets before retrying. A fresh session gets a new preview/approval for remaining work. Do not store bodies, tokens, full chat history or permission grants here. The general [action journal](../../review-inbox-emailbutler/references/observations.md) retains recurring-stream observations; bulk checkpoints are operational progress rather than inferred preference changes.

## Interrupted-call recovery
A process may stop after the provider changed mail but before the checkpoint saved that result. The saved next batch can therefore still include completed moves. Do not replay it from the ledger alone. Inspect the saved revision, current batch and any available call/returned-ID evidence; use the existing reconciliation route for rows already recorded as reported, uncertain or failed.

When an interrupted current batch has no saved outcomes, reconstruct uncertainty before fresh readback:
1. Use ordinary `record` for every row in the current saved batch, with status `uncertain` and evidence that the interrupted call has no durable per-item result. Include known returned IDs. This is a local checkpoint operation; it neither moves mail nor claims every row was attempted. Do not create confirmed action-journal receipts from this uncertainty.
2. Independently locate those frozen logical messages using supported current/returned identities or a verified stable lookup. A stale original ID or failed lookup does not prove that a moved message is absent. Missing or ambiguous lookup leaves the rows uncertain and stops writes.
3. Use `reconcile` with `verified` for an independently confirmed destination and `pending` only where fresh state establishes that the remaining approved action is safe. Preserve returned identities. A known failed result stays distinct. Mixed verified/pending readback is valid on the staged uncertain rows; it cannot be applied directly through reconciliation to rows with no prior result.
4. Read back the checkpoint. Completed rows must disappear from the pending batch. Resume only the remaining approved scope, with a new preview/approval after a new chat or lost context. Recovery reads and checkpoint edits do not grant mailbox permission.

For example, if two of five moves completed before interruption, stage all five as uncertain, independently confirm the two destination messages and three remaining source messages, then reconcile two verified and three pending. Never mislabel the remaining three as completed or failed just to satisfy a full-batch write. When readback confirms the entire saved batch completed, ordinary `record` can instead save that full batch as verified. These routes use the existing helper; no connector settings or credentials are changed.
