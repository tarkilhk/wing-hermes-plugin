import hashlib
import importlib.util
import json
import zipfile

import yaml
from conftest import ROOT, load_api


def test_release_is_deterministic_and_loads_without_desktop_dependencies(tmp_path):
    spec = importlib.util.spec_from_file_location("wing_package", ROOT / "scripts/package.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    archive = module.build(destination=tmp_path / "release")
    first = archive.read_bytes()
    assert module.build(destination=archive.parent).read_bytes() == first
    assert (
        archive.with_suffix(".zip.sha256").read_text().split()[0]
        == hashlib.sha256(first).hexdigest()
    )
    with zipfile.ZipFile(archive) as package:
        assert set(package.namelist()) == {"wing/" + name for name in module.FILES}
        package.extractall(tmp_path / "installed")
    installed = tmp_path / "installed/wing"
    manifest = yaml.safe_load((installed / "plugin.yaml").read_text())
    dashboard = json.loads((installed / "dashboard/manifest.json").read_text())
    assert manifest["name"] == dashboard["name"] == "wing"
    assert manifest["requires_auth"] == ["openai-codex"]
    api = load_api(installed)
    assert {route.path for route in api.router.routes} == {"/voice/v2/status", "/voice/v2/session"}
