# Plugin reference

This document describes the two features of the [Wing plugin](../README.md).

## Email draft card contract

Ask Hermes to draft an email. With `wing` enabled and the `wing_email` toolset
available, `wing_email_draft` stores a draft and returns the card reference that
Hermes includes in its reply. Wing displays recipient, cc/bcc, subject and body.
Tap the envelope-arrow icon to choose an email app; edit and send there.
Opening the chooser never sends email or marks a draft as sent.

Email drafts need no OpenAI sign-in, API key, Gmail access or separate draft-cards
installation. `requires_auth: [openai-codex]` declares permission for subscription
voice's signer only; draft tools never call it. Hermes owns dashboard plugin authentication, profile routing and plugin-host
isolation; the plugin does not expose its own server.

The tool takes complete `to`, `cc`, `bcc` arrays of plain email addresses and
`subject`, `body` strings. Unknown recipients may be empty. To revise, pass
`draft_id` and the latest `base_version`; stale edits fail. Each successful revision
creates a new immutable version. `wing_email_draft_read` reads a specified version.
No automatic expiry, approval or delivery state is added. Fields are rejected,
never clipped: up to 50 addresses per field (320 characters each), subject up to
4,096 UTF-8 bytes and body up to 65,536 UTF-8 bytes.

The response's `card` is a fenced `wing-email-draft` block containing JSON
`{"id":"d_<32 lowercase hex digits>","version":1}`. Wing fetches exactly that
version from `GET /api/plugins/wing/drafts/v1/{id}/versions/{version}?profile=work`.
The endpoint is authenticated by Hermes and uses its selected profile; missing
versions return 404. Old cards keep their old text after a revision. The full
payload is handed off without truncation, though each email app controls its own
compose behavior. Plain email prose and other clients do not become native cards;
the model must use the tool and include its returned reference.

## Voice context and responsibilities

Hermes supplies both the profile prompt and its normal voice policy. The plugin
transports the profile prompt as a developer `initial_items` message for Codex
subscription voice. It specifies
delegation to Hermes. A startup handoff tells the live speaker to adopt the
profile’s identity from its first reply, including a voice name recorded in native
memory. The handoff adds no identity facts and preserves the complete native
prompt. Recent chat is separate input data. Prompts are not silently truncated.
Provider context limits can reject startup.

The fresh-profile inspector uses profile defaults, with a separate temporary
session identity. It initializes configured memory extensions; it does not run a
model turn or save chat messages. Session-specific overrides and query-time memory
prefetch are not reproduced. Ask Hermes through delegation when deeper or current
memory is required. Existing snapshots may reflect an earlier turn's settings.
For fresh inspection, the request's `cwd` must resolve to the profile's configured
`terminal.cwd`; a different directory returns 403 before the inspector starts.
Only the resolved configured path is sent to the inspector. Missing or invalid
directory configuration returns 424. This restriction controls fresh filesystem
discovery; it does not attest the contents of a client-supplied prompt snapshot.

Tasks, tools, memory retrieval, approvals and history remain Hermes-owned. The
plugin does not read SOUL or memory files, implement general tool execution, start a Codex
subprocess, hold a credential store, or maintain a second conversation database.
It contains no desktop UI or dependency on another voice plugin.

## Voice HTTP contract

Hermes authenticates requests and binds the `profile` query to its request scope.
The plugin does not accept a profile, model, URL, headers or credentials in the
request body.

| Request | Meaning |
| --- | --- |
| `GET /api/plugins/wing/voice/v2/status?profile=work&mode=subscription` | Transport availability, supported voices and integration capabilities; no provider call |
| `POST /api/plugins/wing/voice/v2/session?profile=work` | Body `{"offer":"<SDP>","mode":"subscription","system_prompt":null,"cwd":"/workspace","history":"quoted recent chat","voice":"cove"}`; exchanges SDP once using Hermes's signed-in provider API |

Successful negotiation returns `ok`, `contractVersion: 2`, `answer` (SDP),
the selected mode and protocol (`codex-live-v3`), `handoff: client`, model and voice.
Errors preserve authorization/rate-limit status without exposing provider bodies.
A status success does not prove sign-in or account voice access. No provider
request is retried automatically. Route/version changes are clean changes; the
old `talk-desktop` endpoints are not aliases.

API-mode status returns `available: false`, an empty voice list and a reason linked
to [Hermes #151774](https://github.com/NousResearch/hermes-agent/issues/151774).
The advertised `modes` list contains only `subscription`. API session requests
return 503 before preparing context or accessing host/provider services. API mode
will ship once Hermes's host-owned exchange supports per-call instructions and voice.

## Data and trust

The plugin sends SDP, the native profile prompt, recent chat and voice policy to
the selected provider endpoint. Profile context can include saved personal memory. Hermes attaches the credential;
plugin responses contain no credential or account headers. Audio travels over
Wing's negotiated WebRTC connection. Email draft fields are stored in the active profile’s `plugin-data/wing/email-drafts.db`.
They remain until that profile data is removed; plugin updates do not erase them.
The authenticated profile can read any draft ID in its store. Drafts are not tied
to a mail account or restricted to one conversation. This plugin stores no audio, transcripts,
credentials or usage records. It must run inside the authenticated Hermes host,
not as a separately exposed unauthenticated FastAPI service.

Subscription negotiation uses a provider-specific contract that can change.
The plugin does not resolve raw API keys or perform an API-key SDP exchange.
Tests with mocked network responses
do not establish subscription access or audible quality on a phone.
