# Package, registration and private-state migration
Update EmailButler owns this process. Read it when comparing an installed version with a target or handling a setup migration handoff. Keep the complete collection together; source candidates, active installations and private recovery copies have different roles.

## Source and state inventory
Establish the installed source from its actual client registration and files. Supported discovery clues include:

| Layout | Version evidence to inspect |
| --- | --- |
| Legacy single skill | Root SKILL.md, old helpers/instructions and a verifiable Git revision/source baseline; a package manifest may be absent |
| Early skill collection | Root `emailbutler.json`, skill membership and source revision |
| Current collection | `docs/emailbutler.json`, skill membership and source revision |

These are discovery clues, not proof that every similarly named checkout is a known release. In a current checkout, `skills/setup-emailbutler/scripts/package_info.py --skill-dir REGISTERED_SKILL` resolves membership/root/version. Do not run a current-layout validator against an older layout and infer that its data can be replaced.

Find private data from the installed instructions/runtime, supported host settings and explicit location overrides. Preserve an existing nondefault location when the target supports it. A missing file at today's default does not establish that data is absent. Follow only relevant declared pointers; avoid an unbounded search or collecting unrelated credential files. If different clients use different roots, record their consumers and establish which data belongs to this update. Conflicting copies need an explained choice or checked merge; timestamps alone cannot settle them. Distinguish a format version from a mutable revision or cursor.

| Component | What must remain usable and verifiable |
| --- | --- |
| Settings and organization/rule plans | Effective saved preferences and exact account/provider/plan meanings; newer defaults must not replace existing choices |
| Saved revisions and proposal history | Readable before/after history, ordering, identities and integrity links; no invented dates or completion claims |
| Pending proposals and approval receipts | Original scope and pending/approved status, bound preview/base data and provenance; receipts remain separate from implementation/access completion |
| Observations and action receipts | Account/stream grouping, stable logical and returned message IDs, operation IDs, timestamps, deduplication and actual result status |
| Bulk execution manifests | Frozen job membership/key hashes, active dispatch intent, retry deadlines, job claims and account write guards; settle/quiesce workers before changing readers or restoring data |
| Review and bulk checkpoints | Frozen scope/selection, completed/deferred/unresolved outcomes, cursor/revision relationships and current provider identities; completed work must not reappear as pending |
| Onboarding outcomes | Per-account completed/deferred/blocked/active evidence; absence stays unknown rather than becoming fabricated completion or deferral |
| Local code/instruction customizations | Intent and behavior carried against their original upstream baseline; private changes never become an upstream publication |
| Host pointers and registrations | Which retained checkout and private-state roots the selected clients actually use |

Inspect the installed contracts and the corresponding target owners: [Setup storage](../../setup-emailbutler/SKILL.md#private-state-and-recovery), [Customize profiles/history](../../customize-emailbutler/SKILL.md#profile-defaults-and-persistence), [Review observations](../../review-inbox-emailbutler/references/observations.md), [review queues](../../review-inbox-emailbutler/references/review-format.md#saved-queues) and [bulk checkpoints](../../bulk-triage-emailbutler/references/checkpoints.md). Missing optional stores can be legitimate for that source version. Unknown stores/fields must be retained and assessed, not stripped until a validator accepts the result.

## Verified backups and private working copies
Prepare the migration plan and recoverable copies before live changes. Keep state backups, converted working data and restoration scratch outside every source checkout, under private access controls; ignored paths inside a public checkout are insufficient separation. Include all persistent records required by the installed version, source/customization recovery, registrations and nonsecret location pointers. Do not copy connector credentials, tokens or unrelated personal files. If the installed arrangement mixes secrets with state, separate only the understood migration inputs and leave the protected originals intact; incomplete discovery blocks a claimed complete backup.

Coordinate with actual writers so the snapshot represents one consistent state. Respect unknown locks and concurrent work. Distinguish persistent records from transient lock files using the installed contract; do not restore a stale lock as an active writer. Record internal checksums/revisions, copy and verify file contents and relevant access permissions, then restore into separate private scratch. Exercise the installed readers there before treating recovery as ready. Preserve source records byte-for-byte in the backup, including unrecognized data, even when the working copy needs conversion.

For each component, record source location/format, target location/format, transformation or no-op, verification and recovery route. Keep compatible storage in place. A required location change uses copy → verify → cut over the supported pointer; never clobber a populated destination or delete the original as part of the update. All helpers/clients must resolve the intended active root consistently. Do not invent cloud/local synchronization or new storage access where the current host has no supported adapter.

Use a released migration for the understood source/target pair where available. Otherwise prepare a bounded converter/adapter only from inspected contracts and test the exact mapping before presenting it as ready. Test synthetic records for retained values, unknown fields, interrupted/partial attempts and safe repeat behavior. Never put real user records into public fixtures. A valid JSON file is not sufficient compatibility evidence. If meaning or an integrity chain cannot be preserved, keep the old installation usable and report that component as unsupported.

Hash-bound histories, pending proposals and checkpoints need special care. Preserve originals and explicit identity mappings; never rewrite an approval receipt to make it appear to approve a changed candidate. Use a checked legacy reader/adapter where required to keep history inspectable. A pending change needing a new binding requires a fresh proposal/preview later; conversion does not apply it. Keep observations separate from settings and preserve result statuses and returned IDs without inferring consent or replaying a mailbox action.

## Verification before activation
Run the supported package tests, skill validation and resource checks, then inspect the actual private working copy with the target's readers and explicit state location. Readers may need local locks in that copy; they must not point at the live source state or silently initialize a default elsewhere. Check all relevant records, not a sample or counts alone:

1. Effective preferences and plans match the agreed preservation/migration map. The target can inspect history across the version boundary, including integrity relationships and any declared legacy adapter.
2. Pending proposals remain pending and approval receipts retain their original meanings. Observation/receipt identities, grouping, deduplication and unresolved outcomes remain consistent.
3. Review/bulk resume inspection exposes exactly the remaining work, retains completed/deferred results and returned provider identities, and preserves checkpoint relationships. Onboarding evidence remains per-account and honest about absence. No mail operation or proposal activation is used to test this.
4. Local customizations still behave as intended in representative offline examples. Registrations and host-pointer plans identify the final retained checkout and intended private state. Recovery scratch can be read by the matching old readers without corrupting history or discarding records.

When no conversion is needed, verify the same data with the target readers and preserve its bytes. The current helpers validate known formats; the registration helper does not discover, convert or back up private state. This guidance is not a universal converter or proof that a particular legacy format has been tested. Unknown formats, inaccessible data, unsupported location pointers or missing integrity checks must be resolved before activating affected changes.

## Registration mechanics
The complete current checkout contains `docs/emailbutler.json`, five `skills/<name>/` directories with their own scripts, profile defaults and tests, plus documentation artwork under `docs/assets/`. Registration links point into that retained checkout. The installer derives its source from its own resolved script path: invoke it from the approved final checkout after preparing its source/state, not from a disposable download that will be removed.

The 0.1 package registered one root `email-butler` skill. The 0.2 package has no root SKILL.md. Ordinary installer calls return `update_required` for a recognized old root registration and leave registrations untouched, even with `--execute`. Update owns the exact migration plan. The helper requires `--migrate-registrations` before retiring that link or repairing explicitly relocated registrations. It preflights every destination, creates/verifies child links, then retires a root link only when it points to the operated checkout. A conflicting directory, copied skill, foreign link or unrecognized dangling link remains intact. On a failed registration it removes only its own new links and restores replaced recognized links.

```text
python3 -B skills/setup-emailbutler/scripts/install_skill.py --client codex --migrate-registrations
python3 -B skills/setup-emailbutler/scripts/install_skill.py --client codex --migrate-registrations --execute
python3 -B skills/setup-emailbutler/scripts/install_skill.py --client claude-code --migrate-registrations
python3 -B skills/setup-emailbutler/scripts/install_skill.py --client claude-code --migrate-registrations --execute
```
Preview first and match the actual plan with the approved targets before execution. These are Update-workflow commands, not first-time setup commands or proof of authorization. Use selected clients only. A deliberate relocation additionally needs the inspected `--previous-source EXACT_PREVIOUS_CHECKOUT`; the helper can replace only registrations targeting that checkout's matching members or old root. `--skills-dir` selects an inspected host-specific location or isolated test destination. Registration-only migration leaves source and compatible private state unchanged, with recovery and readability checked.

## Coordinated activation and recovery
Before live writes, compare source/state/pointer baselines with the verified snapshot. New writes invalidate the candidate assumptions; preserve them and prepare a fresh snapshot and checks. Keep writers quiescent through the approved cutover. Coordinate source, converted data, private-location pointers and registrations before client reload; these changes are not automatically atomic. Only the exact reviewed transformation may change state. Report saved changes separately from verified active discovery and data readability.

On a failed activation, restore the matching source, state, pointers and registrations from the verified recovery set. Check the post-cutover baseline first: do not overwrite new user work, unowned registration changes or new records. Preserve both versions and resolve a checked recovery/merge if a direct rollback would discard them. Reverting code alone is unsafe when its readers cannot understand migrated data. Retain recovery copies and old private state until cleanup is explicitly authorized, with one identified active root per intended consumer and no ambiguous competing active copies.

## Known compatible transition and limits
The 0.1 and 0.2.2 sources keep the default `.email-butler` private directory and profile schema 1. This is a specific compatibility case; verify the actual installed records and any location override. It does not establish compatibility for all future formats, copied/customized installations or every store merely because a profile loads. Do not move state to match product branding. An older installation's missing onboarding evidence remains unknown; reconstruct only from actual evidence or the user's decision, without claiming completed cleanup or starting a surprise sweep.

A registration success proves filesystem targets, not active client discovery, preserved data usability or mailbox behavior. The package updates through its official Git source without a custom API, auth app or background updater. Mailbox state and native rules remain canonical in the provider and are outside this migration's writes.
