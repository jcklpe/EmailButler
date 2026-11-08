# Host connection and runtime notes
Setup EmailButler owns the workflow; consult these details for the selected host or capability. All command examples run from the canonical package root.

## Official connection routes
For Claude Code using a claude.ai subscription login, connect Gmail and Microsoft 365 through [Claude connector settings](https://claude.ai/customize/connectors). Claude Code receives those connections from the account; the Anthropic-hosted Gmail/Microsoft 365 endpoints do not support a separate local OAuth setup. Inspect the current host and authentication mode before assuming account connections are available. Use an available host setup control; when a connection needs the user's clicks, provide the direct settings link and the next relevant click in ordinary language. Verify the connection in the current agent afterward. Do not add duplicate local MCP servers, create an OAuth app, or ask the recipient to change environment variables. Diagnose any actual account-mode or administrator restriction privately and explain the supported next step.

For ChatGPT/Codex, use the current host's official plugin installation/connection controls for Gmail and Outlook Email, then verify tools and selected identities in the agent doing the review. A connection in another app or a plugin listing alone is not proof of access here. If the current host lacks the required setup control, state that specific limit and guide the user through the official connection screen; do not claim to have installed a connection you could only describe.

## Local registration helper
For an agent with code execution, `python3 scripts/install_skill.py --client codex` or `--client claude-code` previews the destination. Add `--execute` to create the link within the requested installation scope. The documented defaults are the user's .agents/skills for Codex and .claude/skills for Claude Code. Inspect the actual client configuration first; --skills-dir selects a verified custom discovery directory. Never guess a client from the operating system alone or change discovery for other clients automatically.

An existing correct link is reused. A conflicting file, folder, or link is preserved and reported. If the operating system blocks link creation, handle that observed failure through the host's supported installation path; do not ask the user to run a terminal or silently overwrite the conflict. Source updates remain in the checkout; private personalization remains outside it. Verify discovery and connector access separately from filesystem registration.

## Capability probe
Inspect complete tool schemas and any read-only resource templates. Record their actual names and supported operations. Do not infer archive from a category tool, arbitrary Graph access from read_resource, rule inspection from a create response, or readback from delete returning success. Read [capability checks](capabilities.md) for the unresolved Outlook checks.

The bundled scripts/capabilities.py accepts an inventory of observed tool names and reports known mappings and missing requirements. It is an offline inventory classifier, not a network tester. Unknown tools require schema inspection; missing known names do not prove a permanent platform limitation. Its output never certifies an operation as live-tested.

Inventory shape:
```json
{
  "platform": "claude",
  "accounts": [
    {"key": "work-outlook", "provider": "outlook", "tools": ["outlook_email_search", "read_resource", "outlook_create_filter", "outlook_delete_filter"]}
  ]
}
```

Run `python3 scripts/capabilities.py --inventory /private/path/inventory.json` only when the agent has code execution. A full capability check also inspects the schemas, verifies selected identities/permissions through authorized reads, and records actual readback behavior. Do not invent tool names to make the report pass.

## Private persistent state
The agent selects and verifies one private canonical location the chosen agents can actually read and write, preferring the bundled default; do not ask a nontechnical recipient to choose a filesystem path. Keep it outside the source repository and installed skill directory. A local agent may use a dedicated private directory. A cloud chat may use an explicitly selected private file in an existing connected storage service; that is additional storage access, not a feature of the mail connector itself. Keep account data within the chosen service's applicable boundaries. Do not use the public source repository or generalized conversational memory as the state store.

When code execution is available, inspect `python3 scripts/butler.py location`, then initialize with `python3 scripts/butler.py init`. All helpers default to the user's private .email-butler directory in their home folder, so subsequent local sessions can locate the same state. An explicitly selected alternative uses --state-dir consistently in every command and needs a supported private host pointer. Python 3.11+ and its standard library are sufficient. Detect the runtime and resolve the chosen private directory to a canonical path first; managed state/input paths refuse symlinks. Do not make the user install Python or repair paths at a terminal. If the host lacks execution, the review can work without persistent customization. Read [private state](../../../docs/state.md) for the separation between observations and approved settings.

For remote-file storage, the host must download the latest state with its revision/ETag, run the same validator in private scratch, conditionally save the new state, and independently read it back. Do not perform remote persistent changes when the storage route lacks a revision guard. Concurrent updates must not silently overwrite one another. The current helper implements local storage only; a remote synchronization adapter is not included or claimed tested. Until that route exists and is verified, say “I can use that for this review, but this chat cannot remember it for next time.” Do not make the user build storage synchronization, choose an ETag strategy, or install another service to finish ordinary triage.

## Acceptance without surprise writes
Read-only setup cannot prove mailbox write success. Test a mailbox action only after showing an exact synthetic target and obtaining its specific approval. Check moving/archiving with a known test message and restore it only within the approved test scope. A test rule must have a unique name and a nonmatching exact condition; inspection, creation, deletion, and absence readback must each be supported and verified. If rule inspection is missing, do not create a rule merely to discover whether deletion works. Failure of a read never proves absence. An uncertain write is inspected before retrying.

Never silently fall back to browser automation or a new service integration. Continue supported triage and handle supported diagnosis/repair. If a real limit remains, explain only its practical effect and the specific next sign-in/consent/approval click or decision needed under conversation.md; do not hand over raw setup commands or a technical troubleshooting checklist.

## Host references
- [Codex local skill discovery and symlink support](https://learn.chatgpt.com/docs/build-skills)
- [Claude Code skill locations](https://code.claude.com/docs/en/skills)
- [Claude Code account connectors and local OAuth restrictions](https://code.claude.com/docs/en/mcp#use-mcp-servers-from-claudeai)
- [Claude connector setup](https://support.claude.com/en/articles/11176164-use-connectors-to-extend-claude-s-capabilities)

Check current host instructions when a route differs from these documented defaults. Filesystem registration does not connect accounts, grant permissions, or certify runtime discovery.
