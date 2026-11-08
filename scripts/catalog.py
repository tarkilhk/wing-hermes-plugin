"""Render and submit an immutable catalog entry; never loaded by Hermes runtime."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlencode

import yaml

ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = "NousResearch/hermes-agent"
FORK = "tarkilhk/hermes-agent"
PLUGIN_REPO = "tarkilhk/wing-hermes-plugin"


def error_details(output):
    """Keep useful API errors without logging full responses or submitted content."""
    try:
        response = json.loads(output)
    except (ValueError, TypeError):
        return ""
    if not isinstance(response, dict):
        return ""

    def clean(value):
        if not isinstance(value, str):
            return None
        for name in ("GH_TOKEN", "GITHUB_TOKEN"):
            if token := os.environ.get(name):
                value = value.replace(token, "[redacted]")
        return value[:512]

    details = {}
    if message := clean(response.get("message")):
        details["message"] = message
    errors = response.get("errors")
    if isinstance(errors, list):
        safe_errors = []
        for error in errors[:5]:
            if isinstance(error, dict):
                safe_errors.append(
                    {
                        key: cleaned
                        for key in ("resource", "field", "code", "message")
                        if (cleaned := clean(error.get(key))) is not None
                    }
                )
            elif isinstance(error, str):
                safe_errors.append(clean(error))
        if safe_errors:
            details["errors"] = safe_errors
    return json.dumps(details, ensure_ascii=True) if details else ""


class GitHub:
    def request(self, method, path, body=None, missing=False):
        command = ["gh", "api", "--method", method, path]
        if body is not None:
            command += ["--input", "-"]
        result = subprocess.run(
            command,
            input=json.dumps(body) if body is not None else None,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            if missing and "HTTP 404" in result.stderr:
                return None
            status = re.search(r"HTTP \d{3}", result.stderr)
            reason = status.group() if status else f"gh exit {result.returncode}"
            details = error_details(result.stdout)
            raise RuntimeError(f"GitHub {method} {path} failed ({reason}): {details}")
        return json.loads(result.stdout) if result.stdout.strip() else None


def render(sha, root):
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Catalog sha must be a full lowercase commit hash")
    manifest = yaml.safe_load((root / "plugin.yaml").read_text())
    entry = yaml.safe_load((root / "catalog.yaml").read_text())
    if entry["name"] != manifest["name"] or entry["repo"] != f"https://github.com/{PLUGIN_REPO}":
        raise ValueError("Catalog identity must match this plugin")
    if not re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]):
        raise ValueError("Release version must be major.minor.patch")
    entry.update(
        sha=sha,
        version=manifest["version"],
        requires_hermes=manifest["requires_hermes"],
        screenshots=[
            f"https://raw.githubusercontent.com/{PLUGIN_REPO}/{sha}/docs/email-draft-preview.png"
        ],
        capabilities={
            key: manifest.get(key, [])
            for key in (
                "provides_tools",
                "provides_hooks",
                "provides_middleware",
                "requires_env",
            )
        },
    )
    return entry


def entry_yaml(entry):
    # Quote every scalar, including version, so catalog schema and YAML agree.
    return yaml.safe_dump(entry, sort_keys=False, allow_unicode=True, default_style='"')


def description(entry):
    return f"""Add/update Wing {entry["version"]} at `{entry["sha"]}`.

{entry["description"]}

Maintained by the submitting repository owner. Uses `ctx.register_tool`,
profile-local `plugins.plugin_storage.plugin_db`, and authenticated dashboard
plugin routes. No Hermes core code or Desktop UI is modified at runtime.

The native Android card renderer is independent of the original desktop renderer;
this is the companion for a different client/transport, not a renamed desktop
plugin. The README, NOTICE and LICENSE retain both projects' MIT attribution.

Validation: the release workflow runs plugin tests against the inspected Hermes
revision and the official Hermes plugin-validate action against upstream main.
The validator's undocumented-import warnings in existing voice context helpers
are disclosed above and in the README; no core overrides are installed.

[Release and artifacts](https://github.com/{PLUGIN_REPO}/releases/tag/v{entry["version"]})

![Wing Android card preview with fixture data]({entry["screenshots"][0]})

The screenshot shows the matching Wing Android renderer with fixture data, not a
Hermes Desktop UI. Matching client availability is documented in known_issues.
"""


def submit(entry, github):
    owner = FORK.split("/")[0]
    branch = f"wing-plugin-v{entry['version']}"
    path = f"plugin-catalog/{entry['name']}.yaml"
    content = entry_yaml(entry)
    # Stop on merged or rejected submissions; never reopen someone else's decision.
    prs = github.request(
        "GET",
        f"repos/{UPSTREAM}/pulls?"
        + urlencode(
            {
                "state": "all",
                "head": f"{owner}:{branch}",
                "base": "main",
            }
        ),
    )
    if prs:
        pr = prs[0]
        if pr["state"] == "closed" and not pr.get("merged_at"):
            raise RuntimeError(
                f"Catalog submission was closed; review {pr['html_url']} before resubmitting"
            )
        existing = github.request("GET", f"repos/{FORK}/contents/{path}?ref={pr['head']['sha']}")
        if yaml.safe_load(base64.b64decode(existing["content"])) != entry:
            raise RuntimeError(
                "Existing submission has different content; use a new release version"
            )
        return pr["html_url"]
    upstream_entry = github.request(
        "GET", f"repos/{UPSTREAM}/contents/{path}?ref=main", missing=True
    )
    if upstream_entry and yaml.safe_load(base64.b64decode(upstream_entry["content"])) == entry:
        return upstream_entry["html_url"]
    head = github.request("GET", f"repos/{FORK}/git/ref/heads/{branch}", missing=True)
    if head is None:
        base = github.request("GET", f"repos/{UPSTREAM}/git/ref/heads/main")
        github.request(
            "POST",
            f"repos/{FORK}/git/refs",
            {
                "ref": f"refs/heads/{branch}",
                "sha": base["object"]["sha"],
            },
        )
    existing = github.request("GET", f"repos/{FORK}/contents/{path}?ref={branch}", missing=True)
    if not existing or yaml.safe_load(base64.b64decode(existing["content"])) != entry:
        # A partial prior run may have created the branch but not its file.
        # Never overwrite another version already submitted on this branch.
        if existing:
            previous = yaml.safe_load(base64.b64decode(existing["content"]))
            if previous.get("version") == entry["version"] and previous.get("sha") != entry["sha"]:
                raise RuntimeError("Refusing to repin an existing release version")
        body = {
            "message": f"catalog: Wing {entry['version']}",
            "branch": branch,
            "content": base64.b64encode(content.encode()).decode(),
        }
        if existing:
            body["sha"] = existing["sha"]
        github.request("PUT", f"repos/{FORK}/contents/{path}", body)
    pr = github.request(
        "POST",
        f"repos/{UPSTREAM}/pulls",
        {
            "title": f"plugin-catalog: Wing {entry['version']} Android companion",
            "head": f"{owner}:{branch}",
            "base": "main",
            "body": description(entry),
            "maintainer_can_modify": True,
        },
    )
    return pr["html_url"]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["render", "submit"])
    parser.add_argument("--sha", required=True)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    source = args.source.resolve()
    if args.action == "submit":
        actual = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=source, text=True
        ).strip()
        if actual != args.sha:
            raise ValueError("Submit from the exact release source checkout")
    entry = render(args.sha, source)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(entry_yaml(entry))
    if args.action == "submit":
        print(submit(entry, GitHub()))
    elif not args.output:
        print(entry_yaml(entry), end="")


if __name__ == "__main__":
    main()
