# Development and publication

For features and installation, see the [README](../README.md).
Run the commands below from the plugin repository root.

## Development and release

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest
.venv/bin/python -m ruff check dashboard tests scripts __init__.py drafts.py
.venv/bin/python -m ruff format --check dashboard tests scripts __init__.py drafts.py
.venv/bin/python scripts/package.py
```

The package command creates a deterministic `dist/wing-<version>.zip` containing only
the installable plugin, documentation and license notices, plus a SHA-256 file.
CI runs the same checks and builds the archive. The package is not a PyPI wheel.
CI also checks the real Hermes plugin loader, manifest reader, signed-request
helper and voice-instruction builder at commit
`8d7061f1a74980a7549356a4e5e57b603475ddf0`. To run that check locally, set
`HERMES_SOURCE_ROOT` to a checkout of that commit before running pytest. Credentials,
profile configuration and the network are fixtures; the check neither signs in
nor contacts a provider. Without that variable, the native-contract tests skip.

## Live voice acceptance

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
unaffiliated public repository, use a classic PAT with `public_repo` and `workflow`
scopes. `public_repo` authorizes the public-fork changes and upstream PR. This
workflow creates its submission branch from current Hermes main; when that commit
introduces GitHub Actions files absent from the fork, GitHub also requires the
`workflow` scope to create the reference. This is permission for the fork branch,
not for publishing Wing releases; releases use the repository's built-in token.
Private-repository access (`repo`) is not requested. Set the token through GitHub's
Actions secrets UI or
`gh secret set CATALOG_TOKEN --repo tarkilhk/wing-hermes-plugin`; never commit it.
Without it, release publication still works and CI reports the missing catalog
credential. After adding it, re-run the workflow to submit the published version.

Catalog validation currently warns about the existing voice integration's imports
of Hermes prompt-inspection, runtime-cwd, personality and voice helpers, including
`hermes_cli.prompt_size._build_inspection_agent`. They can change upstream. The
plugin calls them in a short-lived inspector or profile-bound request; it does
not replace core functions. These warnings are disclosed for catalog review.
