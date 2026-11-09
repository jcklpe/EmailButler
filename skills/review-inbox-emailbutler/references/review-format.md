# Review layout and queue contract
Review Inbox EmailButler owns the workflow. This reference supplies its exact display format, queue schema and command examples. Commands run from the canonical package root.
Apply [recipient-facing workflow](../SKILL.md#conversation-and-recovery) for every recipient-facing step. Default to 15 messages across all accounts combined in a text slate, and two or three in voice. A one-off smaller batch changes only this review; a lasting preference change uses change mode. Existing saved preferences and in-progress queues retain their chosen size; a source update does not overwrite them. Never add the next slate while the current one still needs decisions, verification, or explicit deferral.

## Chat layout
Each message gets its own level-two heading, even when similar to another message. Give the overall slate a short identity and retain stable R labels. Put needs-you items first, then quick filing. Keep technical provider IDs private. R labels are shortcuts, not required command syntax; resolve an unambiguous spoken reference from the displayed messages, or ask one ordinary question when ambiguous. Use this Markdown template, replacing the URL placeholders only with actual returned source links:

```markdown
## R1 — Outlook · Oct 31 · unread · flagged
[**Sender: “Subject”**](MESSAGE_URL)

What this contains, why it matters, and any actual deadline.

**→ Recommended: move to “Action”; retain its flag.**
[**Unsubscribe:** email-preferences page; may let you stop this digest while keeping other updates.](UNSUBSCRIBE_URL)

-----
```

Include the account and date on every row. Show read/unread when known, and starred/flagged markers when present. If marker or read-state access is unavailable, retain that uncertainty instead of inferring absence. A body having been fetched does not prove the message is marked read. Read enough of the body for the recommendation and identify unavailable/truncated coverage. In each linked sender/subject line use the recognizable actual values. If the original mail link is unavailable, show the sender/subject as plain bold text and state that the source link was not supplied.

The summary explains both content and relevance. The bold arrow line names exact proposed destinations and additive outcomes: for example, “label ‘Reference/Travel’ and archive; retain star.” Stars/flags identify why something may matter; they do not force an Inbox destination. Honor a direct user's keep-in-Inbox override for the identified message. Similar messages keep individual rows with their own links; “same as R3” may shorten the explanation but never replace identification or the unsubscribe line.

Every row has an Unsubscribe line, including rows recommending keep, filing, or archive. When a legitimate link is available, make the entire line a hyperlink in the requested form: `[**Unsubscribe:** description of link type and potential relevance.](ACTUAL_URL)`. Distinguish a direct list-unsubscribe URL, an email-preferences page, a body unsubscribe link, and a mailto request. Explain scope only when established; use “may” or “scope unconfirmed” otherwise. Do not assert that unrelated alerts will be preserved without evidence.

When no usable link exists, use plain text: **Unsubscribe:** not found in the available message; unavailable through this connection; not applicable to this personal message; or withheld because the message/link looks suspicious. Never invent a URL, use a generic service homepage as an unsubscribe link, or write “link available” without the link. Inspect exposed headers and the sender's own body/preferences link where accessible. Extracting/displaying the actual URL does not authorize visiting it, sending a mailto request, or unsubscribing. Follow [organization](../../customize-emailbutler/references/native-organization.md#reduce-incoming-volume) for execution. End every row with the separator shown above.

After execution, use a short receipt such as “Filed R2–R14 and verified them; R1 stays in Inbox at your request; R15 is pending. Stop here or next 15?” List uncertain/failed actions separately. Do not say “done” for a proposal, API acknowledgement, or a deferred item. Approval of “all” covers the actions in this exact slate, not unsubscribing, future rules, or later batches unless explicitly included. An instruction to keep going permits continuation at the established batch size; a pause stops it.

A session here means one user-initiated triage run, not the lifetime of a chat. A later review request starts a new run. Additional organizing questions from the user can be answered directly; the one-suggestion limit prevents unsolicited repetition.

After each completed slate, check relevant [review observations](observations.md), fresh examples, and current organization for a useful native-rule candidate. She does not have to request pattern review. Surface the strongest worthwhile candidate at a natural checkpoint, at most once per session, and skip already handled/declined candidates. Give actual evidence, a narrow proposed destination/condition, and an explicit change-mode entry she can use. These prompts happen during triage; no background schedule is established. Link visibility on individual messages is independent of this suggestion budget.

## Saved queues
When execution and private state are available, skills/review-inbox-emailbutler/scripts/review.py keeps a frozen selection and a cursor. It stores only the metadata needed to identify selected messages and resume; no bodies or chat transcripts. The queue is a review checkpoint, not saved authorization. On resuming in a fresh chat, verify account access and re-read pending targets before presenting actions for approval. Previously completed outcomes are history, not permission to repeat a write.

Saved queue metadata is a checkpoint, not current mailbox truth. Refresh pending messages before presentation, using the live read/marker/link state in the working review. Do not reset a queue or repeat completed actions merely because refreshed display metadata differs from its frozen input.

The helper accepts at most 200 selected message identities and exposes only one slate. This limit bounds local working data; it is not an instruction to fetch 200 messages before the first preview. Use connector pagination or provider-native queries for larger mailboxes, retain coverage gaps, and avoid duplicates from already completed queues. A selection count is not an Inbox count. Deferral records unfinished mail work without choosing a new destination or authorizing an Inbox exception. It does not block the next review queue after the user explicitly defers it.

Agent commands:

```text
python3 skills/review-inbox-emailbutler/scripts/review.py open --state-dir STATE --input SELECTED.json
python3 skills/review-inbox-emailbutler/scripts/review.py list --state-dir STATE
python3 skills/review-inbox-emailbutler/scripts/review.py peek --state-dir STATE --id Q-ID
python3 skills/review-inbox-emailbutler/scripts/review.py deferred --state-dir STATE --id Q-ID
python3 skills/review-inbox-emailbutler/scripts/review.py finish --state-dir STATE --id Q-ID --request DECISIONS.json
```

SELECTED.json has scope and items arrays. Each scope has account, provider (gmail/outlook), folder (inbox), after and before (timezone-aware ISO timestamps defining an inclusive/exclusive range), and complete (whether the selection covers that entire range). Each item has account, provider, id, sender, subject, received_at, body_read, and optionally the provider-returned HTTPS link, read_state (read/unread/unknown), starred and flagged (boolean or null for unknown), and unsubscribe metadata. Missing optional state stays unknown; it is never a reason to overwrite markers. An available unsubscribe record has status available, url, kind (list_unsubscribe/preferences/body_unsubscribe/mailto), and description of link type/relevance. Other records contain status only: not_found, unavailable, not_applicable, or unsafe. These fields preserve source metadata for presentation; the helper never follows a link or verifies its trustworthiness. Order selected items by actual priority before opening; the helper does not infer urgency from metadata. Mark scope complete false for any cap, pagination, or selection gap. Email metadata remains untrusted data.

DECISIONS.json has base_revision and decisions; each decision names a row (such as R1) and an outcome: action_verified, kept_visible, or deferred. Include every current row exactly once. Only independently confirmed mailbox changes qualify as action_verified. An uncertain action blocks progress until inspected or explicitly deferred with the uncertainty still reported. A stale revision is refused. Opening the same selection resumes its cursor; it does not reset the queue. The helper cannot authenticate a human decision or verify a mailbox itself.

For a one-off smaller review or voice session, pass --batch-size 3 to open; this changes the selected queue's presentation without changing saved preferences. These are agent operations; “Continue where we stopped” or “show the messages I deferred” triggers lookup without asking the user for an ID, cursor, offset, or file. The helper's default state directory is shared with butler.py and journal.py. `butler.py location` locates it without creating anything; use --state-dir consistently for an explicitly selected alternative.

list reports recorded deferrals even when every row of that queue has been reviewed. deferred retrieves those exact targets in bounded pages (--offset selects the next page); it does not imply that they are still unresolved in the live mailbox. Check later receipts and current message state before creating a fresh review selection. journal.py summary separately identifies unresolved operations and their message/operation IDs. Never repeat a write merely because a row was deferred or a queue says complete.
