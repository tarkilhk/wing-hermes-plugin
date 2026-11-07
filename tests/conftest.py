import contextvars
import importlib.util
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
SDP = (
    "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\nm=application 9 UDP/DTLS/SCTP webrtc-datachannel\r\n"
)
PROFILE = contextvars.ContextVar("test_profile", default="default")


def load_api(root=ROOT):
    name = "wing_test_" + uuid.uuid4().hex
    spec = importlib.util.spec_from_file_location(name, root / "dashboard/plugin_api.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def make_app(api):
    async def profile_scope(profile: str = "default"):
        if profile not in {"default", "alice", "bob"}:
            raise HTTPException(404, "Unknown profile")
        token = PROFILE.set(profile)
        try:
            yield
        finally:
            PROFILE.reset(token)

    app = FastAPI()
    app.include_router(
        api.router, prefix="/api/plugins/wing", dependencies=[Depends(profile_scope)]
    )
    return app


@pytest.fixture
def api():
    module = load_api()
    yield module
    sys.modules.pop(module.__name__, None)


@pytest.fixture
def host(api, monkeypatch):
    class NotSignedIn(RuntimeError):
        pass

    state = SimpleNamespace(calls=[], failure=None, code=201, body=SDP.encode())

    def request(*args, **kwargs):
        state.calls.append((PROFILE.get(), args, kwargs))
        if state.failure:
            raise state.failure
        return SimpleNamespace(status=state.code, body=state.body)

    provider = SimpleNamespace(
        credentialed_provider_request=request, ProviderNotSignedIn=NotSignedIn
    )
    state.NotSignedIn = NotSignedIn
    monkeypatch.setattr(
        api,
        "host_services",
        lambda: (provider, lambda: "Hermes native voice instructions for " + PROFILE.get()),
    )
    return state


@pytest.fixture
def client(api, host):
    with TestClient(make_app(api)) as client:
        yield client
