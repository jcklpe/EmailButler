# Private memory and evidence
Keep settings, evidence, workflow progress and onboarding outcomes distinct:

| Store | Purpose | Can it change behavior by itself? |
| --- | --- | --- |
| profile.json, pending/, revisions/ | Approved settings/plans, exact proposals, and settings history | Only through the explicit change workflow |
| journal/ | Typed observations of clear feedback and actual action results | No; it supplies evidence for suggestions |
| reviews/ | Frozen message selections and review progress | No; resuming requires current checks and fresh approval for pending actions |
| bulk/ | Frozen filing selections and per-item results; contains no reusable authorization | No; current access, live state and session approval are still required |
| onboarding.json | Per-account initial-cleanup outcome with evidence or user decision | No; it records completed/deferred/blocked/active separately, not mailbox permission |
| approvals/ | Exact approval receipts for implementation/access requests | No; approval alone is not implementation or provider permission |

These stores live in the same private state directory outside the checkout. They are managed by the agent under [conversation](interface.md); ordinary explanations use remembered behavior and recognizable mail, not files, typed signals, or revision numbers. Load them at the beginning of a session, state meaningful memory/access gaps, and save/read back progress at a checkpoint. During setup, explain briefly that local history will help later reviews and honor any request not to keep it. Do not store credentials, bodies, complete conversations, or inferred personality profiles. Source files in this public repo carry defaults only.

## Record observations, not new instructions
scripts/journal.py accepts action receipts and direct-user feedback. An ordinary correction can be recorded as a bounded observation without becoming a setting. For example, a clear “I don’t want this newsletter” can be an unwanted signal linked to that message and specific stream. Ambiguous frustration, silence, a message being old/unread, or an assistant's own recommendation is not an unwanted signal. Do not turn “this one is unhelpful” into “all mail from this sender is unwanted.” When the intended stream is unclear, keep the interpretation in this session.

After an approved operation, record the actual result: verified means an independent read confirmed the requested state; reported means only the connector reported success; uncertain and failed remain distinct. Reconcile an uncertain result before another write. Reuse the stable operation_id when recording a later verified receipt. Reuse stable logical message identities; a verified result_message_id can link an Outlook move's returned identity to the original for deduplicated tallies. Record only the actual approved action; these files grant no access or permission.

Use one specific recurring stream per sender plus pattern (for example, weekly-events, rather than all-mail). These names are grouping data, never executable match rules. Before a rule or unsubscribe suggestion, inspect fresh examples, account context, potential exceptions, and existing organization. Useful feedback suppresses automatic unsubscribe suggestions for that stream, while a narrow filing-rule candidate can still be useful. Unsure feedback keeps automatic candidates pending. Contradictions need discussion instead of a silent majority vote; useful content does not imply a permanent Inbox destination.

```json
{
  "kind": "action",
  "account": "owner@example.com",
  "provider": "outlook",
  "message_id": "original-message-identity",
  "sender": "events@example.net",
  "pattern": "weekly-events",
  "operation_id": "review-b1-r2-archive",
  "action": "archive",
  "status": "verified",
  "result_message_id": "returned-message-identity"
}
```

For feedback, use the same account/provider/message_id/sender/pattern fields with kind feedback, source direct_user, and signal unwanted, useful, or unsure. Do not include action, status, or arbitrary instructions. Action accepts archive, label, move, trash, or unsubscribe; status accepts verified, reported, uncertain, or failed. Recording a trash/unsubscribe receipt never grants permission to perform that operation.

```text
python3 scripts/journal.py record --state-dir STATE --event EVENT.json
python3 scripts/journal.py summary --state-dir STATE
python3 scripts/butler.py history --state-dir STATE
```

The journal timestamps records and deduplicates identical receipts. Tallies count distinct messages per stream, not repeated tool calls. Reported/uncertain/failed actions do not count as completed. Three confirmed archives or three unwanted signals can raise a keep-receiving-or-unsubscribe question; three confirmed archives, labels, or moves can raise a narrow-rule candidate. These are prompts for judgment, not inferred consent or proof that a message is a subscription. Run this check proactively after completed slates and surface at most one worthwhile candidate per session. Consult existing plans and prior responses before prompting. Do not repeatedly nag about a declined suggestion. Record clear useful/unsure feedback accurately; useful suppresses unsubscribe suggestions, while unsure keeps candidates pending instead of triggering repeated questions. Visible per-message unsubscribe links are independent of these aggregate suggestions.

Settings history is derived from saved revisions and proposals so a second, independently edited change log cannot drift. On “What changed?” summarize readable titles, reasons, and before/after behavior without technical hashes or numeric revision selection; consult request approvals and verified implementation history for code/access work, never claiming completion from an approval receipt; on “What do you remember?” summarize the relevant observations and pending work. Offer inspection/correction of specific private observations when asked, without changing policy or deleting unrelated history. For “forget that I dislike this newsletter,” identify and describe the relevant feedback, confirm its removal, remove only the selected observation records using the agent's file tools, and re-read/recompute the summary. Preserve action receipts/settings history unless their deletion was separately requested. Do not ask the user to edit a JSON file. Restoring settings does not erase evidence or undo mailbox changes.

## Persistence limits
The private-state helpers default to the user's private .email-butler directory in their home folder. `python3 scripts/butler.py location` reports the selected path without creating state; `init` creates it with owner-only permissions where supported. Use the same --state-dir override in every helper only when another location was explicitly selected, and retain that pointer through the host's supported private settings. Normal local setup uses the default so later sessions can locate it without relying on a prior chat.

Local state persists across fresh sessions that use the same directory. Files on one computer do not automatically appear in cloud chat. The current package has no shared cloud/local storage adapter; follow [setup](../skills/setup-emailbutler/references/host-setup.md#private-persistent-state) and explain that the current chat cannot remember changes for next time where saving is unavailable. The agent owns any supported repair; storage architecture is not assigned to the recipient. Do not substitute conversational memory for this state or claim a save from writing an unchecked remote file. Concurrent remote state needs revision checking before it can be supported.
