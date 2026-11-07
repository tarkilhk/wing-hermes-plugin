# Wing plugin for Hermes

The `wing` plugin connects Wing's mobile voice session to ChatGPT subscription
voice using the sign-in already owned by the selected Hermes profile. Hermes
signs the request and manages credential refresh. Wing keeps the microphone,
speaker and WebRTC connection on the phone.

**Version 0.1.1 is a subscription transport bridge.** Full Hermes-built profile
context at voice startup and saving the complete spoken exchange into shared
history are not implemented. They depend on native Hermes capabilities, including
[external-turn recording #121045](https://github.com/NousResearch/hermes-agent/issues/121045).
The plugin advertises both capabilities as false. Delegated tasks still use the
normal Hermes conversation through Wing.

## Requirements and installation

- Current upstream Hermes, at least 0.23.0, with its dashboard/gateway available
  to Wing and the dashboard Python dependencies installed.
- An OpenAI Codex sign-in in the Hermes profile used by the chat, with access to
  subscription voice. Installing this plugin does not establish entitlement.
- A Wing build using the `wing` plugin contract. Older development builds using
  `talk-desktop` must be updated together with this plugin.

Install the published repository using Hermes's plugin installer:

```sh
hermes plugins install https://github.com/tarkilhk/wing-hermes-plugin
hermes plugins enable wing
```

Enable it in every profile where you use subscription voice. For a named profile,
use Hermes's profile selector, for example `hermes --profile work plugins enable wing`.
Install/serve it through the profile's supported plugin setup; enablement in the
default profile alone does not enable another profile. Sign-in is managed through
Hermes's provider settings. Restart the serving Hermes process if required by
your installation's plugin-loading workflow.

In Wing, select **ChatGPT subscription** for live voice and start from the chat
you want to use. Selecting **OpenAI API** instead uses Hermes's stock
`/api/audio/voice-live/*` routes and does not require this plugin. API billing is
separate; the plugin never switches between these modes.

## Responsibilities

Hermes supplies the voice instructions from its normal `tools.voice_live`
configuration. The plugin adds only subscription delegation formatting and the
rule that interrupting speech does not stop agent work. These voice instructions
are not the full profile/session context promised by the pending integration.

Tasks, tools, memory retrieval, approvals and history remain Hermes-owned. The
plugin does not read SOUL or memory files, implement tool execution, start a Codex
subprocess, hold a credential store, or maintain a second conversation database.
It contains no desktop UI or dependency on another voice plugin.

## HTTP contract

Hermes authenticates requests and binds the `profile` query to its request scope.
The plugin does not accept a profile, model, URL, headers or credentials in the
request body.

| Request | Meaning |
| --- | --- |
| `GET /api/plugins/wing/voice/v1/status?profile=work` | Transport availability, supported voices and integration capabilities; no provider call |
| `POST /api/plugins/wing/voice/v1/session?profile=work` | Body `{"offer":"<SDP>","voice":"cove"}`; exchanges SDP once using Hermes's signed-in provider API |

Successful negotiation returns `ok`, `contractVersion: 1`, `answer` (SDP),
`mode: subscription`, `protocol: codex-live-v3`, `handoff: client`, model and voice.
Errors preserve authorization/rate-limit status without exposing provider bodies.
A status success does not prove sign-in or account voice access. No provider
request is retried automatically. Route/version changes are clean changes; the
old `talk-desktop` endpoints are not aliases.

## Data and trust

The plugin sends SDP, Hermes's configured voice instructions and transport
metadata to ChatGPT's Codex voice endpoint. Hermes attaches the credential;
plugin responses contain no credential or account headers. Audio travels over
Wing's negotiated WebRTC connection. This plugin stores no audio, transcripts,
credentials or usage records. It must run inside the authenticated Hermes host,
not as a separately exposed unauthenticated FastAPI service.

Subscription negotiation uses a provider-specific contract that can change.
API mode uses the stock Hermes integration. Tests with mocked network responses
do not establish subscription access or audible quality on a phone.

## Development and release

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python scripts/package.py
```

The package command creates a deterministic `dist/wing-0.1.1.zip` containing only
the installable plugin, documentation and license notices, plus a SHA-256 file.
CI runs the same checks and builds the archive. The package is not a PyPI wheel.
CI also checks the real Hermes plugin loader, manifest reader, signed-request
helper and voice-instruction builder at commit
`981791d9d41fd2efde27f563873ce8c48ef1dac6`. To run that check locally, set
`HERMES_SOURCE_ROOT` to a checkout of that commit before running pytest. Credentials,
profile configuration and the network are fixtures; the check neither signs in
nor contacts a provider. Without that variable, the two native-contract tests skip.

## Credits

Based on [Synero's hermes-live-voice](https://github.com/Synero/hermes-live-voice)
(MIT), which provided the original subscription voice integration reference.
See [NOTICE](NOTICE.md) and [LICENSE](LICENSE) for attribution and licensing.
