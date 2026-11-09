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
