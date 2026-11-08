"""Build the installable plugin without including checkout or development data."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    "plugin.yaml",
    "__init__.py",
    "drafts.py",
    "dashboard/manifest.json",
    "dashboard/plugin_api.py",
    "dashboard/context_worker.py",
    "README.md",
    "docs/PLUGIN_REFERENCE.md",
    "docs/DEVELOPMENT.md",
    "docs/email-draft-preview.png",
    "LICENSE",
    "NOTICE.md",
)


def build(root: Path = ROOT, destination: Path | None = None) -> Path:
    manifest = yaml.safe_load((root / "plugin.yaml").read_text())
    name, version = manifest["name"], manifest["version"]
    destination = destination or root / "dist"
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / f"{name}-{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for relative in FILES:
            item = zipfile.ZipInfo(f"{name}/{relative}", date_time=(2026, 1, 1, 0, 0, 0))
            item.compress_type = zipfile.ZIP_DEFLATED
            item.external_attr = 0o100644 << 16
            package.writestr(item, (root / relative).read_bytes())
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n")
    return archive


if __name__ == "__main__":
    print(build())
