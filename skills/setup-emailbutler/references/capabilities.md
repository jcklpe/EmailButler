# Recipient capability checks
This is an agent-only reference. Use [recipient-facing workflow](../SKILL.md#conversation-and-recovery) to report practical effects and supported next steps; never send the recipient this table or ask them to collect tool names, schemas, tokens, or provider IDs. At setup, inspect the recipient's own agent, installed connectors, selected accounts, permissions, and tool schemas. That environment determines what EmailButler can do. Checks on the developer's accounts do not verify the recipient's setup. Use the public documentation below as dated research pointers, not as installed-feature tests or hard-coded platform limits.

## Outlook checks to perform during setup
| Capability | Evidence | Shipping posture |
| --- | --- | --- |
| Archive or move a message to an arbitrary mail folder | Absent from the published Microsoft 365 read/write tool tables; category changes are distinct from folder moves | Unverified; never advertise full filing support without an actual tool/schema and approved test |
| Create an inbox rule | outlook_create_filter is documented | Documented; account permission/schema/readback still required |
| Delete an inbox rule | outlook_delete_filter is documented | Documented; do not claim safe removal without exact ID and absence verification |
| List/get rules with criteria, actions, exceptions, and ordering | No rule-list/get tool documented; read_resource is described as reading files, emails, or chat by URI | Unverified; generic read_resource is not evidence of arbitrary Graph access |
| Categories | Creation, rename/delete, and message/thread/batch category changes are documented | Supported in principle; verify actual enabled tools and account |

Public documentation checked on 2026-10-31 is incomplete: the directory lists seven read tools while the security guide documents additional writes. A catalog omission does not establish that a feature is absent from the recipient's installation. Inspect actual exposed schemas after the requested connector has been installed and connected. Do not query the developer's account as a substitute or create a custom authentication setup just to enumerate tools.

Setup should attempt the supported connector route and report the recipient's actual result. Continue useful supported work when an action is unavailable, and surface only the limitation that matters to the requested operation. A safe native-rule lifecycle needs inspection, exact-ID removal, and readback as well as creation.

## Programmable bulk execution schemas — November 9, 2026
The OpenAI tools exposed in the development session include `bulk_label_matching_emails` with query/label/archive arguments; `batch_modify_email` for exact Gmail message IDs; minimal `read_email`; and Outlook `move_email` for one message, with folder/filter/pagination fields on `list_messages` and optional Internet Message-ID metadata. This is schema inspection only: no account was called, no permission verified, no connection configured and no mailbox mutated. It does not change the dated Claude Microsoft 365 findings above.

[The bulk executor](../../bulk-triage-emailbutler/references/execution.md) needs callable authenticated tools plus a programmable process bridge. Verify both independently of the tool-name classifier. Exposed fields do not prove a raw filter is accepted, recovery keys are unambiguous or a recipient host can execute a long loop. Prefer server-side bulk work when its exact scope is approved; do not infer a universal five-message cap or silently create a different access route.

## Other relevant capabilities
Check unsubscribe separately from archive, spam reporting, and rule creation. A supported unsubscribe operation, a sender-provided link, and a verified subscription change are different things. Missing header/body access can also prevent finding a legitimate unsubscribe link. The workflow must report the actual available route and cannot infer success from a message disappearing from Inbox.

Claude's Gmail directory documents create_filter and list_filters, label creation/application/removal, and spam rescue. Its public list does not establish filter deletion. Gmail archive is removal of the INBOX system label, supported by the documented unlabel_thread schema. Some advanced Gmail filters are unsupported and attachment bodies are unavailable through Claude's Gmail connector.

The offline classifier in skills/setup-emailbutler/scripts/capabilities.py recognizes a bounded set of known tool names. It is a starting point for schema inspection, not a complete connector catalog. Unknown names and missing mappings require checking the recipient's actual tools. They do not establish permanent feature absence, permissions, successful execution, or setup readiness.

## Reported trial limitations
The [November 6 retrospective intake](../../../docs/active-spikes/initial-package.md#november-6-retrospective-intake), recorded 2026-11-07, holds de-identified reports about folder/tag differences, small batches and bulk-processing cost, with their evidence limits and unscheduled questions. No tool schema, permission or live mailbox result was verified during that intake. The evidence classifications above remain unchanged.

## Sources
- [Anthropic Microsoft 365 security guide and full tool table](https://support.claude.com/en/articles/12684923-microsoft-365-connector-security-guide)
- [Anthropic Microsoft 365 directory](https://claude.com/marketplace/connectors/microsoft-365)
- [Connect Claude to Microsoft 365](https://support.claude.com/en/articles/15183774-connect-to-microsoft-365)
- [Anthropic Gmail directory](https://claude.com/marketplace/connectors/gmail)
- [Claude Google Workspace limitations](https://support.claude.com/en/articles/10166901-use-google-workspace-connectors)
- [Google Gmail MCP unlabel_thread schema](https://developers.google.com/workspace/gmail/api/reference/mcp/tools_list/unlabel_thread)
- [OpenAI inbox workflow](https://learn.chatgpt.com/use-cases/manage-your-inbox)

## Recipient acceptance probe
During setup in the recipient's Claude or Codex agent, inspect the actual Microsoft 365/Outlook connector tools and resource templates without changing mail. Determine whether the available tools can move/archive a message, list/get inbox rules with their criteria/actions/ordering, create a rule, and delete a rule. Retain exact exposed names and schema fields privately, with unavailable and unknown kept distinct. Do not call a write tool or invent a Graph URI. Distinguish exposed support from a successful read or write test. Present the user with the short practical result; keep the technical inventory for the agent.

The inventory can settle exposed tool support. Identity checks and read-only rule inspection can settle read permission. An exact approved synthetic-message/rule cycle is still needed for write/readback verification. Do not create an uninspectable rule as a capability probe.
