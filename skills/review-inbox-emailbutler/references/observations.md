# Private observations and action receipts
Read this when recording feedback/actions or checking recurring-stream patterns. [Review Inbox EmailButler](../SKILL.md) owns the procedure. Use the [Setup private-state location](../../setup-emailbutler/SKILL.md#private-state-and-recovery), preserve retention choices and keep observations separate from approved settings.

skills/review-inbox-emailbutler/scripts/journal.py accepts action receipts and direct-user feedback. An ordinary correction can be recorded as a bounded observation without becoming a setting. For example, a clear “I don’t want this newsletter” can be an unwanted signal linked to that message and specific stream. Ambiguous frustration, silence, a message being old/unread, or an assistant's own recommendation is not an unwanted signal. Do not turn “this one is unhelpful” into “all mail from this sender is unwanted.” When the intended stream is unclear, keep the interpretation in this session.

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
python3 skills/review-inbox-emailbutler/scripts/journal.py record --state-dir STATE --event EVENT.json
python3 skills/review-inbox-emailbutler/scripts/journal.py summary --state-dir STATE
```

The journal timestamps records and deduplicates identical receipts. Tallies count distinct messages per stream, not repeated tool calls. Reported/uncertain/failed actions do not count as completed. Three confirmed archives or three unwanted signals can raise a keep-receiving-or-unsubscribe question; three confirmed archives, labels, or moves can raise a narrow-rule candidate. These are prompts for judgment, not inferred consent or proof that a message is a subscription. In the first recipient draft, leave rule candidates unsurfaced: filter/rule setup is deferred until feedback and a separate scope discussion. Run the subscription check proactively after completed slates and surface at most one worthwhile subscription candidate per session. Consult existing plans and prior responses before prompting. Do not repeatedly nag about a declined suggestion. Record clear useful/unsure feedback accurately; useful suppresses unsubscribe suggestions, while unsure keeps candidates pending instead of triggering repeated questions. Visible per-message unsubscribe links are independent of these aggregate suggestions.
