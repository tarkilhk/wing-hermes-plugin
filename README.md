# Wing plugin for Hermes

The `wing` plugin creates structured email drafts for Wing and connects its mobile
voice session to subscription or API voice using the selected Hermes profile. Hermes owns sign-in, credential refresh,
API-key resolution and profile context. Wing keeps the microphone, speaker and
WebRTC connection on the phone.

Version 0.3.0 adds versioned email drafts. Voice startup uses Hermes-built profile
context. Existing chats use Hermes's cached or saved prompt; new chats use Hermes's native profile
inspector in a short-lived process. SOUL, built-in memory, skills and workspace
context remain assembled by Hermes. Deeper memory retrieval and all tool work
are delegated into the selected Hermes chat.

Saving the complete spoken exchange into shared history still awaits native
[external-turn recording #121045](https://github.com/NousResearch/hermes-agent/issues/121045).
Captions do not imply a saved conversation turn.

## Requirements and installation

- Current upstream Hermes, at least 0.23.0, with its dashboard/gateway available
  to Wing and the dashboard Python dependencies installed.
- For subscription mode, an OpenAI Codex sign-in in the chat's Hermes profile
  with voice access. For API mode, OpenAI voice API credentials configured in
  that profile.
- A Wing build using the `wing` plugin contract. Older development builds using
  `talk-desktop` must be updated together with this plugin.

Install the published repository using Hermes's plugin installer:

```sh
hermes plugins install https://github.com/tarkilhk/wing-hermes-plugin
hermes plugins enable wing
```

Enable it in every profile where you use email drafts or live voice. For a named profile,
use Hermes's profile selector, for example `hermes --profile work plugins enable wing`.
Install/serve it through the profile's supported plugin setup; enablement in the
default profile alone does not enable another profile. Sign-in is managed through
Hermes's provider settings. Restart the serving Hermes process if required by
your installation's plugin-loading workflow.

In Wing, select **ChatGPT subscription** or **OpenAI API** for live voice. Both
use the Wing plugin so the native profile prompt is available before speech.
API mode uses Hermes's existing voice configuration and API-key resolver. API
billing is separate; the plugin never switches modes automatically.

## Email drafts

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

## Responsibilities

Hermes supplies both the profile prompt and its normal voice policy. The plugin
transports the profile prompt as a developer `initial_items` message for Codex
subscription voice, or as session instructions for API voice. It specifies
delegation to Hermes. Recent chat is separate input data. Prompts are not silently truncated.
Provider context limits can reject startup.

The fresh-profile inspector uses profile defaults, with a separate temporary
session identity. It initializes configured memory extensions; it does not run a
model turn or save chat messages. Session-specific overrides and query-time memory
prefetch are not reproduced. Ask Hermes through delegation when deeper or current
memory is required. Existing snapshots may reflect an earlier turn's settings.

Tasks, tools, memory retrieval, approvals and history remain Hermes-owned. The
plugin does not read SOUL or memory files, implement general tool execution, start a Codex
subprocess, hold a credential store, or maintain a second conversation database.
It contains no desktop UI or dependency on another voice plugin.

## HTTP contract

Hermes authenticates requests and binds the `profile` query to its request scope.
The plugin does not accept a profile, model, URL, headers or credentials in the
request body.

| Request | Meaning |
| --- | --- |
| `GET /api/plugins/wing/voice/v2/status?profile=work&mode=subscription` | Transport availability, supported voices and integration capabilities; no provider call |
| `POST /api/plugins/wing/voice/v2/session?profile=work` | Body `{"offer":"<SDP>","mode":"subscription","system_prompt":null,"cwd":"/workspace","history":"quoted recent chat","voice":"cove"}`; exchanges SDP once using Hermes's signed-in provider API |

Successful negotiation returns `ok`, `contractVersion: 2`, `answer` (SDP),
the selected mode and protocol (`codex-live-v3` or `openai-live`), `handoff: client`, model and voice.
Errors preserve authorization/rate-limit status without exposing provider bodies.
A status success does not prove sign-in or account voice access. No provider
request is retried automatically. Route/version changes are clean changes; the
old `talk-desktop` endpoints are not aliases.

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
API mode reuses native Hermes configuration and credential resolution. Tests with mocked network responses
do not establish subscription access or audible quality on a phone.

## Development and release

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python -m ruff check dashboard tests scripts __init__.py drafts.py
.venv/bin/python -m ruff format --check dashboard tests scripts __init__.py drafts.py
.venv/bin/python scripts/package.py
```

The package command creates a deterministic `dist/wing-0.3.0.zip` containing only
the installable plugin, documentation and license notices, plus a SHA-256 file.
CI runs the same checks and builds the archive. The package is not a PyPI wheel.
CI also checks the real Hermes plugin loader, manifest reader, signed-request
helper and voice-instruction builder at commit
`8d7061f1a74980a7549356a4e5e57b603475ddf0`. To run that check locally, set
`HERMES_SOURCE_ROOT` to a checkout of that commit before running pytest. Credentials,
profile configuration and the network are fixtures; the check neither signs in
nor contacts a provider. Without that variable, the native-contract tests skip.

## Credits

Email drafts adapt the structured card/version design and standalone dashboard
loading pattern from [arsiktech/hermes-draft-cards](https://github.com/arsiktech/hermes-draft-cards)
(MIT, copyright 2026 arsiktech), reviewed at commit
`6e6ca659ece59b0ce1c28b3b188393491ee5a415`. Wing supplies its own immutable email-only
contract and native Flutter card; the original desktop approval/sending workflow
is not included. The original copyright and MIT permission notice are retained.


Based on [Synero's hermes-live-voice](https://github.com/Synero/hermes-live-voice)
(MIT), which provided the original subscription voice integration reference.
See [NOTICE](NOTICE.md) and [LICENSE](LICENSE) for attribution and licensing.

For an explicitly authorized live acceptance run, install `aiortc` in your test
environment and set `WING_LIVE_CONTEXT_TEST` to a private JSON file. Its fields are
`base_url`, `login_file`, `profile`, `audio_file`, `expected_terms`, and optional
`cwd`. The login file contains the dashboard's supported `provider`, `username`
and `password`. The audio file is an 8 kHz mono 16-bit WAV asking about facts
already in the selected profile; `expected_terms` lists those facts. Keep these
files outside Git. Run `pytest tests/test_live_context.py`.

This opens one bounded subscription call and checks actual spoken recall plus
audio exchange. A successful SDP negotiation alone does not pass. It neither
submits Hermes work nor writes chat history, and it closes the call afterward.

## Publication and updates

Every push/PR runs lint, tests and package checks. Main also runs Hermes's official
plugin validator against upstream main. On success, a new version in plugin.yaml
automatically creates an immutable GitHub tag/release with ZIP, SHA-256 and catalog
entry. Re-running CI can finish a draft release; published tags/assets never move.
Bump plugin.yaml for the next release. A same-version main push does not repin it.

The catalog job opens a version-specific PR from `tarkilhk/hermes-agent` to
`NousResearch/hermes-agent`. Hermes maintainers review each pin; publishing on
GitHub does not wait for their review. The same submission command is available
locally: `python scripts/catalog.py submit --sha <release-commit>`, from that
release's checkout with GitHub CLI authenticated. A closed submission requires
maintainer follow-up; CI does not reopen it.

Unattended catalog submissions require the repository Actions secret
`CATALOG_TOKEN`: a dedicated GitHub token allowed to write the owner's public fork
and open pull requests upstream. For a personal account contributing to an
unaffiliated public repository, GitHub currently requires a classic PAT with
`public_repo` scope. Set it through GitHub's Actions secrets UI or
`gh secret set CATALOG_TOKEN --repo tarkilhk/wing-hermes-plugin`; never commit it.
Without it, release publication still works and CI reports the missing catalog
credential. After adding it, re-run the workflow to submit the published version.

Catalog validation currently warns about the existing voice integration's imports
of Hermes prompt-inspection, runtime-cwd, personality and voice helpers, including
`hermes_cli.prompt_size._build_inspection_agent`. They can change upstream. The
plugin calls them in a short-lived inspector or profile-bound request; it does
not replace core functions. These warnings are disclosed for catalog review.

![Wing Android email card preview with fixture data](docs/email-draft-preview.png)

The preview uses fixture data in the matching Wing Android client. The plugin
itself has no Hermes Desktop renderer.
