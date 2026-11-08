"""Publish the validated checkout once per version, then return its immutable SHA."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path

from catalog import PLUGIN_REPO, ROOT, GitHub, entry_yaml, render
from package import build


def publish(github, sha, root=ROOT, upload=None):
    entry = render(sha, root)
    tag = f"v{entry['version']}"
    path = f"repos/{PLUGIN_REPO}"
    release = github.request("GET", f"{path}/releases/tags/{tag}", missing=True)
    ref = github.request("GET", f"{path}/commits/{tag}", missing=True)
    if release and release["draft"] and release["target_commitish"] != sha:
        raise RuntimeError("Draft release belongs to another source commit; bump the version")
    if release and not release["draft"] and ref is None:
        raise RuntimeError("Published release has no resolvable tag; repair its provenance")
    if ref and ref["sha"] != sha:
        if release and not release["draft"]:
            # A later main push at the same version does not move the release pin.
            return ref["sha"]
        raise RuntimeError("Tag belongs to another source commit; bump plugin.yaml version")
    if release and not release["draft"]:
        return sha
    archive = build(root)
    catalog = archive.parent / "wing-catalog.yaml"
    catalog.write_text(entry_yaml(entry))
    if release is None:
        release = github.request(
            "POST",
            f"{path}/releases",
            {
                "tag_name": tag,
                "target_commitish": sha,
                "name": f"Wing plugin {entry['version']}",
                "draft": True,
                "body": "Structured email drafts and profile-context voice for Wing Android.\n\n"
                "Install directly with `hermes plugins install https://github.com/tarkilhk/wing-hermes-plugin`.\n\n"
                "See README for capabilities, requirements and credits. Catalog listing is reviewed separately.\n\n"
                f"Source: `{sha}`. The ZIP checksum and exact catalog entry are attached.",
            },
        )
    assets = [archive, archive.with_suffix(".zip.sha256"), catalog]
    if upload:
        upload(tag, assets)
    else:
        subprocess.run(
            ["gh", "release", "upload", tag, "--repo", PLUGIN_REPO, "--clobber", *map(str, assets)],
            check=True,
        )
    github.request("PATCH", f"{path}/releases/{release['id']}", {"draft": False})
    return sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sha", required=True)
    args = parser.parse_args()
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    if args.sha != actual:
        raise ValueError("Release source must be the checked-out commit")
    result = publish(GitHub(), args.sha)
    if output := os.environ.get("GITHUB_OUTPUT"):
        with Path(output).open("a") as file:
            file.write(f"sha={result}\n")
    print(result)


if __name__ == "__main__":
    main()
