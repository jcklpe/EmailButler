# Bulk and first-run checkpoint contracts
All commands run from the canonical package root. Resolve registered skill symlinks before locating shared resources; `scripts/package_info.py --skill-dir SKILL_DIRECTORY` reports the package root/version. Use the same private `--state-dir` across all helpers. These records contain no authorization and make no connector calls.

## First-run state
After initializing/preserving the profile, write the verified selected accounts to a private JSON list:
```json
[{"account": "owner@example.com", "provider": "outlook"}]
```
Run `python3 -B scripts/onboarding.py check --input ACCOUNTS.json`. Absent records are `required`. Record an outcome with `record --input OUTCOME.json`:
```json
{"base_revision": 0, "outcome": {"account": "owner@example.com", "provider": "outlook", "status": "active", "reason": "The user approved the selected initial filing group.", "evidence": "Current preview and approval; bulk job B-…"}}
```
Allowed outcomes are `active`, `completed`, `deferred` and `blocked`. Include a concrete source for the result/decision. A helper cannot authenticate that source; the agent checks it. Record completed only after independent verification or an inspected no-cleanup-needed result. Only an explicit user choice supports deferred. A host/tool limit or permission error supports blocked; a normal pause remains active. `cleanup_addressed` includes completed/deferred/blocked; `cleanup_completed` includes only completed. Records are per account and revision-guarded; a stale write is refused. Existing profiles are not rewritten.

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
python3 -B scripts/bulk.py create --input SELECTION.json
python3 -B scripts/bulk.py status --id B-ID
python3 -B scripts/bulk.py record --id B-ID --input RESULTS.json
python3 -B scripts/bulk.py reconcile --id B-ID --input READBACK.json
```
The output supplies `next_batch`, counts, revision and blocked/complete/pending state. Each row has its original `id` and currently known `current_id`. Execute only the approved action on those current identities. Record the original ID plus returned identity where relevant:
```json
{"base_revision": 0, "results": [{"id": "message-1", "status": "verified", "evidence": "Independent destination metadata read", "result_id": "moved-message-1"}, {"id": "message-2", "status": "uncertain", "evidence": "The move request timed out"}]}
```
Every item in the current batch needs exactly one result. `verified` requires independent mailbox evidence; `reported`, `uncertain` and `failed` block the next write batch. Read back the checkpoint after recording. Reconcile unresolved rows through fresh reads; the same result format can set them to verified, failed or pending. Pending means the read established that retry is safe; preserve or update returned IDs as needed. Successful rows never reappear in the next batch. A stale revision, edited selection or foreign/duplicate identity is refused.

The helper does not call connectors, wait/retry, infer throttling, authenticate human approval, independently verify evidence or guarantee exactly-once remote writes. After a crash between a write and checkpoint save, inspect pending targets before retrying. A fresh session gets a new preview/approval for remaining work. Do not store bodies, tokens, full chat history or permission grants here. The general [action journal](../../../docs/state.md) retains recurring-stream observations; bulk checkpoints are operational progress rather than inferred preference changes.
