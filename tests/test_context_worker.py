"""Exercise lifecycle at the native prompt-builder seam, never a copied builder."""

import importlib.util
import sys
from types import ModuleType, SimpleNamespace

import pytest
from conftest import ROOT


@pytest.mark.parametrize("fail", [False, True])
def test_inspector_uses_native_builder_and_always_releases_scope(monkeypatch, fail):
    calls = []
    agent = SimpleNamespace(close=lambda: calls.append("close"))
    cwd = ModuleType("agent.runtime_cwd")
    cwd.set_session_cwd = lambda path: calls.append(("cwd", path)) or "scope"
    cwd.reset_session_cwd = lambda token: calls.append(("reset", token))
    inspector = ModuleType("hermes_cli.prompt_size")
    inspector._build_inspection_agent = lambda platform: calls.append(("agent", platform)) or agent
    builder = ModuleType("agent.system_prompt")

    def build(actual):
        assert actual is agent
        calls.append("build")
        if fail:
            raise RuntimeError("build failed")
        return "native SOUL / memory / skills / project"

    builder.build_system_prompt = build
    for module in (cwd, inspector, builder):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    spec = importlib.util.spec_from_file_location(
        "wing_context_worker", ROOT / "dashboard/context_worker.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    if fail:
        with pytest.raises(RuntimeError):
            worker.inspect("/selected-project")
    else:
        assert worker.inspect("/selected-project") == "native SOUL / memory / skills / project"
    assert calls == [
        ("cwd", "/selected-project"),
        ("agent", "desktop"),
        "build",
        "close",
        ("reset", "scope"),
    ]
