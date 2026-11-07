import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from conftest import SDP, make_app

BASE = "/api/plugins/wing/voice/v1"


def test_status_is_capability_only_and_records_no_provider_call(client, host):
    response = client.get(BASE + "/status?profile=alice")
    assert response.status_code == 200
    assert response.json()["modes"] == ["subscription"]
    assert response.json()["capabilities"] == {"profileContext": False, "historyAppend": False}
    assert host.calls == []


def test_success_preserves_sdp_and_uses_host_instructions(client, host, api):
    response = client.post(BASE + "/session?profile=alice", json={"offer": SDP})
    assert response.status_code == 200
    result = response.json()
    assert result["answer"] == SDP
    assert result["mode"] == "subscription"
    assert result["protocol"] == "codex-live-v3"
    assert result["handoff"] == "client"
    profile, args, kwargs = host.calls[0]
    assert profile == "alice"
    assert args == ("openai-codex", "POST", api.ENDPOINT)
    assert kwargs["json"]["sdp"] == SDP
    session = kwargs["json"]["session"]
    assert session["instructions"].startswith("Hermes native voice instructions for alice\n\n")
    assert session["delegation"] == {"type": "client"}
    assert not {"authorization", "chatgpt-account-id"} & {k.lower() for k in kwargs["headers"]}


@pytest.mark.parametrize(
    "extra",
    [
        {"mode": "api"},
        {"context": "invented profile context"},
        {"profile": "bob"},
        {"url": "https://example.com"},
        {"headers": {"Authorization": "secret"}},
        {"model": "other"},
        {"voice": "unsupported"},
        {"offer": 123},
        {"offer": "v=0\r\nm=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"},
        {"offer": SDP + "x" * (128 * 1024)},
        {"offer": SDP + "💬" * 40000},
    ],
)
def test_invalid_request_never_reaches_credentials(client, host, extra):
    response = client.post(BASE + "/session", json={"offer": SDP, **extra})
    assert response.status_code == 422
    assert host.calls == []


@pytest.mark.parametrize(
    "code,expected", [(401, 401), (403, 403), (429, 429), (302, 502), (500, 502)]
)
def test_provider_errors_are_safe_and_never_retried(client, host, code, expected):
    host.code, host.body = code, b"PRIVATE upstream body and account"
    response = client.post(BASE + "/session", json={"offer": SDP})
    assert response.status_code == expected
    assert "PRIVATE" not in response.text
    assert len(host.calls) == 1


@pytest.mark.parametrize("body", [b"not SDP", b"\xff", SDP.encode() + b"x" * (128 * 1024)])
def test_invalid_provider_answer_is_not_returned(client, host, body):
    host.body = body
    response = client.post(BASE + "/session", json={"offer": SDP})
    assert response.status_code == 502
    assert response.json() == {"detail": "ChatGPT returned an invalid audio answer."}


@pytest.mark.parametrize(
    "failure,status", [(RuntimeError("PRIVATE"), 502), (httpx.ReadTimeout("PRIVATE"), 504)]
)
def test_exceptions_do_not_expose_credentials(client, host, failure, status):
    host.failure = failure
    response = client.post(BASE + "/session", json={"offer": SDP})
    assert response.status_code == status
    assert "PRIVATE" not in response.text
    assert len(host.calls) == 1


def test_missing_sign_in_is_distinct(client, host):
    host.failure = host.NotSignedIn("PRIVATE refresh details")
    response = client.post(BASE + "/session", json={"offer": SDP})
    assert response.status_code == 401
    assert response.json() == {"detail": "Sign in to OpenAI Codex in this Hermes profile."}


def test_unknown_profile_is_rejected_by_host_before_plugin(client, host):
    response = client.post(BASE + "/session?profile=unknown", json={"offer": SDP})
    assert response.status_code == 404
    assert host.calls == []


def test_old_plugin_and_api_wrapper_are_not_routes(client, host):
    for path in ("/api/plugins/talk-desktop/live-direct/v1/session", BASE + "/api/session"):
        assert client.post(path, json={"offer": SDP}).status_code == 404
    assert host.calls == []


def test_concurrent_profiles_keep_scope_across_thread_offload(api, host):
    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=make_app(api)), base_url="http://host"
        ) as client:
            replies = await asyncio.gather(
                *[
                    client.post(BASE + "/session?profile=" + profile, json={"offer": SDP})
                    for profile in ("alice", "bob")
                ]
            )
            assert [r.status_code for r in replies] == [200, 200]

    # TestClient's own event loop must not be part of this context propagation test.
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(lambda: asyncio.run(exercise())).result(timeout=10)
    assert {profile for profile, _, _ in host.calls} == {"alice", "bob"}
    assert len({kwargs["headers"]["session-id"] for _, _, kwargs in host.calls}) == 2
    for profile, _, kwargs in host.calls:
        assert kwargs["json"]["session"]["instructions"].startswith(
            "Hermes native voice instructions for " + profile + "\n\n"
        )
