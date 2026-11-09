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


def test_real_worker_process_keeps_json_clean_and_profile_scoped(tmp_path):
    import json
    import os
    import subprocess

    # Real process/pipe boundary with tiny native-shaped packages. A callback
    # prints during construction and cleanup, as real Hermes extensions do.
    for package in ("agent", "hermes_cli"):
        (tmp_path / package).mkdir()
        (tmp_path / package / "__init__.py").write_text("")
    (tmp_path / "agent/runtime_cwd.py").write_text(
        "cwd = None\ndef set_session_cwd(value):\n global cwd\n cwd=value\n return value\n"
        "def reset_session_cwd(token): pass\n"
    )
    (tmp_path / "hermes_cli/prompt_size.py").write_text(
        'class Agent:\n def close(self): print("cleanup diagnostic")\n'
        "def _build_inspection_agent(platform):\n"
        ' assert platform == "desktop"\n print("extension diagnostic")\n return Agent()\n'
    )
    (tmp_path / "agent/system_prompt.py").write_text(
        "import os\nfrom agent import runtime_cwd\n"
        "def build_system_prompt(agent):\n"
        ' return os.environ["HERMES_HOME"] + ":" + runtime_cwd.cwd\n'
    )
    for profile in ("alice", "bob"):
        result = subprocess.run(
            [sys.executable, str(ROOT / "dashboard/context_worker.py")],
            input=json.dumps({"cwd": "/project-" + profile}),
            text=True,
            capture_output=True,
            check=True,
            timeout=10,
            env={**os.environ, "PYTHONPATH": str(tmp_path), "HERMES_HOME": "/profile-" + profile},
        )
        assert json.loads(result.stdout) == {
            "system_prompt": "/profile-" + profile + ":/project-" + profile
        }
        assert "extension diagnostic" in result.stderr
        assert "cleanup diagnostic" in result.stderr
    (tmp_path / "agent/system_prompt.py").write_text(
        'def build_system_prompt(agent):\n raise RuntimeError("PRIVATE secret profile content")\n'
    )
    result = subprocess.run(
        [sys.executable, str(ROOT / "dashboard/context_worker.py")],
        input=json.dumps({"cwd": "/project"}),
        text=True,
        capture_output=True,
        check=True,
        timeout=10,
        env={**os.environ, "PYTHONPATH": str(tmp_path)},
    )
    error = json.loads(result.stdout)["error"]
    assert error["kind"] == "RuntimeError"
    assert error["location"] == "build_system_prompt"
    assert "PRIVATE" not in result.stdout


def test_parent_preserves_host_runtime_import_paths_for_worker(api, tmp_path, monkeypatch):
    import os
    import subprocess

    from conftest import SDP

    # Hermes's managed runtime can add dependencies to sys.path without putting
    # them in PYTHONPATH. The terminal child-env helper deliberately scrubs that
    # environment; an inspector still needs the host's native dependencies.
    runtime = tmp_path / "managed-runtime"
    runtime.mkdir()
    (runtime / "wing_runtime_dependency.py").write_text('VALUE = "native dependency available"')
    monkeypatch.syspath_prepend(str(runtime))
    native = ModuleType("hermes_cli")
    native.__file__ = str(tmp_path / "hermes_cli/__init__.py")
    config = ModuleType("hermes_cli.config")
    config.load_config = lambda: {"terminal": {"cwd": str(tmp_path)}}
    personality = ModuleType("hermes_cli.personality")
    personality.resolve_ephemeral_system_prompt = lambda _: ""
    constants = ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: tmp_path / "profile"
    local = ModuleType("tools.environments.local")
    local.served_profile_child_env = lambda **_: {
        k: v for k, v in os.environ.items() if k != "PYTHONPATH"
    }
    for module in (native, config, personality, constants, local):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    real_run = subprocess.run

    def worker_probe(argv, **kwargs):
        # Retain the actual interpreter/env/pipe launch, probe the dependency
        # before substituting the expensive inspector's prompt result.
        result = real_run(
            [
                argv[0],
                "-c",
                'import json; import wing_runtime_dependency as d; print(json.dumps({"system_prompt": d.VALUE}))',
            ],
            **kwargs,
        )
        return result

    monkeypatch.setattr(api.subprocess, "run", worker_probe)
    body = api.SessionOffer(
        voice="cove", offer=SDP, mode="subscription", system_prompt=None, cwd=str(tmp_path)
    )
    assert api.profile_prompt(body) == "native dependency available"


@pytest.mark.parametrize("profile", ["alice", "bob"])
@pytest.mark.parametrize("requested", ["configured", "alias", "other-profile", "child", "escape"])
def test_fresh_context_only_inspects_configured_profile_directory(
    api, tmp_path, monkeypatch, profile, requested
):
    import json

    from conftest import SDP

    for name in ("alice", "bob"):
        (tmp_path / name).mkdir()
    configured = tmp_path / profile
    (configured / "child").mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(configured, target_is_directory=True)
    outside = tmp_path / ("bob" if profile == "alice" else "alice")
    (configured / "escape").symlink_to(outside, target_is_directory=True)
    paths = {
        "configured": configured,
        "alias": alias,
        "other-profile": outside,
        "child": configured / "child",
        "escape": configured / "escape",
    }
    native = ModuleType("hermes_cli")
    native.__file__ = str(tmp_path / "hermes_cli/__init__.py")
    config = ModuleType("hermes_cli.config")
    config.load_config = lambda: {"terminal": {"cwd": str(configured)}}
    personality = ModuleType("hermes_cli.personality")
    personality.resolve_ephemeral_system_prompt = lambda _: ""
    constants = ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: configured
    local = ModuleType("tools.environments.local")
    local.served_profile_child_env = lambda **_: {}
    for module in (native, config, personality, constants, local):
        monkeypatch.setitem(sys.modules, module.__name__, module)
    calls = []

    def inspect(argv, **kwargs):
        calls.append(json.loads(kwargs["input"]))
        return SimpleNamespace(stdout=json.dumps({"system_prompt": "Native context"}))

    monkeypatch.setattr(api.subprocess, "run", inspect)
    body = api.SessionOffer(
        voice="cove", offer=SDP, mode="subscription", system_prompt=None, cwd=str(paths[requested])
    )
    if requested in {"configured", "alias"}:
        assert api.profile_prompt(body) == "Native context"
        assert calls == [{"cwd": str(configured)}]
    else:
        with pytest.raises(api.HTTPException) as error:
            api.profile_prompt(body)
        assert error.value.status_code == 403
        assert str(outside) not in str(error.value.detail)
        assert calls == []

    # A supplied snapshot triggers no new filesystem inspection.
    body.system_prompt = "Saved native context"
    calls.clear()
    assert api.profile_prompt(body) == "Saved native context"
    assert calls == []

    # Missing profile configuration never authorizes the request's cwd.
    config.load_config = lambda: {}
    body.system_prompt = None
    with pytest.raises(api.HTTPException) as error:
        api.profile_prompt(body)
    assert error.value.status_code == 424
    assert calls == []
