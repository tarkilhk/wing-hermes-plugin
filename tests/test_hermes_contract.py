"""Pinned-host contract check, with synthetic credentials and no network.

Set HERMES_SOURCE_ROOT to the inspected Hermes checkout. CI always runs this.
The real loader, manifest reader, signer and voice policy builder are exercised;
account resolution, profile config and unrelated plugin OAuth discovery are fixtures.
"""

import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest
from conftest import PROFILE, ROOT, SDP


@pytest.fixture
def installed_plugin(tmp_path, monkeypatch):
    source = os.environ.get("HERMES_SOURCE_ROOT")
    if not source:
        pytest.skip("Set HERMES_SOURCE_ROOT to run the native Hermes contract check")
    assert (Path(source) / "hermes_cli/plugin_provider_requests.py").is_file()
    monkeypatch.syspath_prepend(source)
    from hermes_cli import plugin_provider_requests as provider
    from hermes_cli.plugin_isolation import import_user_code
    from tools import voice_live

    installed = tmp_path / "plugins/wing"
    shutil.copytree(ROOT / "dashboard", installed / "dashboard")
    shutil.copy(ROOT / "plugin.yaml", installed / "plugin.yaml")
    monkeypatch.setattr(provider, "_plugin_roots", lambda: {installed.parent})
    oauth = ModuleType("hermes_cli.plugin_oauth")
    oauth._plugin_oauth_spec = lambda *_: None
    monkeypatch.setitem(sys.modules, oauth.__name__, oauth)
    config = ModuleType("hermes_cli.config")
    config.load_config = lambda: {
        "voice": {"gpt_live": {"instructions": "Native profile policy: " + PROFILE.get()}}
    }
    monkeypatch.setitem(sys.modules, config.__name__, config)
    monkeypatch.setitem(
        provider._PROVIDERS,
        "openai-codex",
        (
            frozenset({("https", "chatgpt.com", 443)}),
            lambda _: ({"Authorization": "Bearer synthetic-test-token"}, "https://chatgpt.com"),
        ),
    )
    sent = []

    def receive(request):
        sent.append(request)
        return httpx.Response(201, content=SDP.encode())

    real_client = httpx.Client
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: real_client(transport=httpx.MockTransport(receive), **kwargs),
    )
    personality = ModuleType("hermes_cli.personality")
    personality.resolve_ephemeral_system_prompt = lambda _: ""
    monkeypatch.setitem(sys.modules, personality.__name__, personality)
    api = import_user_code(installed / "dashboard/plugin_api.py", "wing_native_contract")
    assert api is not None
    yield api, installed, sent, voice_live
    sys.modules.pop(api.__name__, None)


def test_installed_plugin_uses_native_signer_and_voice_policy(installed_plugin):
    api, _, sent, voice_live = installed_plugin
    token = PROFILE.set("alice")
    try:
        result = asyncio.run(
            api.session(
                api.SessionOffer(
                    offer=SDP,
                    mode="subscription",
                    system_prompt="Native saved context",
                    cwd="/workspace",
                )
            )
        )
    finally:
        PROFILE.reset(token)
    assert result["answer"] == SDP
    assert len(sent) == 1
    request = sent[0]
    assert str(request.url) == api.ENDPOINT
    assert request.headers["authorization"] == "Bearer synthetic-test-token"
    instructions = json.loads(request.content)["session"]["instructions"]
    assert voice_live.LIVE_PERSONA + "\n\nNative profile policy: alice" in instructions
    assert "synthetic-test-token" not in json.dumps(result)


def test_native_host_refuses_undeclared_sign_in(installed_plugin):
    api, installed, sent, _ = installed_plugin
    (installed / "plugin.yaml").write_text("name: wing\nversion: '0.1.0'\n")
    with pytest.raises(api.HTTPException) as error:
        asyncio.run(
            api.session(
                api.SessionOffer(
                    offer=SDP,
                    mode="subscription",
                    system_prompt="Native saved context",
                    cwd="/workspace",
                )
            )
        )
    assert error.value.status_code == 502
    assert sent == []
