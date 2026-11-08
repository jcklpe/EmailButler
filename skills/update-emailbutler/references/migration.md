# Package update and registration migration
The complete checkout contains `emailbutler.json`, five `skills/<name>/` directories, shared `scripts/`, `assets/`, `docs/` and tests. A registered skill is a link into this checkout; do not copy a child directory away from its shared resources. Canonicalize the discovered path before following package-relative resources. `scripts/package_info.py --skill-dir REGISTERED_SKILL` validates membership and reports identity/version.

The 0.1 package registered one root `email-butler` skill. The 0.2 package has no root SKILL.md. Its registration helper preflights every destination before changing anything. It creates each child registration, verifies the full set, then removes a legacy root symlink only if it points to the operated checkout. An unrelated directory, copied skill, foreign link or unrecognized dangling link remains untouched. On a failed registration it removes only its own newly created links and restores replaced recognized links.

```text
python3 -B scripts/install_skill.py --client codex
python3 -B scripts/install_skill.py --client codex --execute
python3 -B scripts/install_skill.py --client claude-code
python3 -B scripts/install_skill.py --client claude-code --execute
```
Use only the already selected clients. For a deliberately moved checkout, inspect the old links and preview `--previous-source EXACT_PREVIOUS_CHECKOUT`; execution can replace only links targeting that checkout's matching skill directories or its old root registration. It is not permission to overwrite another installation. `--skills-dir` is for an inspected host-specific location or isolated verification.

The old private `.email-butler` state directory and profile schema remain compatible. Do not move or rename private state merely to match product branding. Onboarding is initially unknown for an older profile; ask/reconstruct its real initial-cleanup outcome from existing evidence rather than claiming it completed or running a surprise sweep. Existing review queues, proposals, observations and action receipts remain intact.

A registration success proves filesystem targets, not active client discovery or a successful mailbox operation. Verify reload/discovery through the current host where supported. This package can be updated through the official Git source; it does not install a custom API, auth app or background updater.
