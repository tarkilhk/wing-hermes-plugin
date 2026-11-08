import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from conftest import SDP, make_app

BASE = "/api/plugins/wing/voice/v2"


def test_status_is_capability_only_and_records_no_provider_call(client, host):
    response = client.get(BASE + "/status?profile=alice&mode=subscription")
    assert response.status_code == 200
    assert response.json()["modes"] == ["subscription", "api"]
    assert response.json()["capabilities"] == {"profileContext": True, "historyAppend": False}
    assert host.calls == []


def test_success_preserves_sdp_and_uses_host_instructions(client, host, api):
    response = client.post(
        BASE + "/session?profile=alice",
        json={"mode": "subscription", "system_prompt": None, "cwd": "/workspace", "offer": SDP},
    )
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
    assert "Hermes native voice instructions for alice\n\n" in session["instructions"]
    assert session["delegation"] == {"type": "client"}
    assert not {"authorization", "chatgpt-account-id"} & {k.lower() for k in kwargs["headers"]}


@pytest.mark.parametrize(
    "extra",
    [
        {"mode": "other"},
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
    response = client.post(
        BASE + "/session",
        json={
            "mode": "subscription",
            "system_prompt": None,
            "cwd": "/workspace",
            "offer": SDP,
            **extra,
        },
    )
    assert response.status_code == 422
    assert host.calls == []


@pytest.mark.parametrize(
    "code,expected", [(401, 401), (403, 403), (429, 429), (302, 502), (500, 502)]
)
def test_provider_errors_are_safe_and_never_retried(client, host, code, expected):
    host.code, host.body = code, b"PRIVATE upstream body and account"
    response = client.post(
        BASE + "/session",
        json={"mode": "subscription", "system_prompt": None, "cwd": "/workspace", "offer": SDP},
    )
    assert response.status_code == expected
    assert "PRIVATE" not in response.text
    assert len(host.calls) == 1


@pytest.mark.parametrize("body", [b"not SDP", b"\xff", SDP.encode() + b"x" * (128 * 1024)])
def test_invalid_provider_answer_is_not_returned(client, host, body):
    host.body = body
    response = client.post(
        BASE + "/session",
        json={"mode": "subscription", "system_prompt": None, "cwd": "/workspace", "offer": SDP},
    )
    assert response.status_code == 502
    assert response.json() == {"detail": "ChatGPT returned an invalid audio answer."}


@pytest.mark.parametrize(
    "failure,status", [(RuntimeError("PRIVATE"), 502), (httpx.ReadTimeout("PRIVATE"), 504)]
)
def test_exceptions_do_not_expose_credentials(client, host, failure, status):
    host.failure = failure
    response = client.post(
        BASE + "/session",
        json={"mode": "subscription", "system_prompt": None, "cwd": "/workspace", "offer": SDP},
    )
    assert response.status_code == status
    assert "PRIVATE" not in response.text
    assert len(host.calls) == 1


def test_missing_sign_in_is_distinct(client, host):
    host.failure = host.NotSignedIn("PRIVATE refresh details")
    response = client.post(
        BASE + "/session",
        json={"mode": "subscription", "system_prompt": None, "cwd": "/workspace", "offer": SDP},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Sign in to OpenAI Codex in this Hermes profile."}


def test_unknown_profile_is_rejected_by_host_before_plugin(client, host):
    response = client.post(
        BASE + "/session?profile=unknown",
        json={"mode": "subscription", "system_prompt": None, "cwd": "/workspace", "offer": SDP},
    )
    assert response.status_code == 404
    assert host.calls == []


def test_old_plugin_and_api_wrapper_are_not_routes(client, host):
    for path in ("/api/plugins/talk-desktop/live-direct/v1/session", BASE + "/api/session"):
        assert (
            client.post(
                path,
                json={
                    "mode": "subscription",
                    "system_prompt": None,
                    "cwd": "/workspace",
                    "offer": SDP,
                },
            ).status_code
            == 404
        )
    assert host.calls == []


def test_concurrent_profiles_keep_scope_across_thread_offload(api, host):
    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=make_app(api)), base_url="http://host"
        ) as client:
            replies = await asyncio.gather(
                *[
                    client.post(
                        BASE + "/session?profile=" + profile,
                        json={
                            "mode": "subscription",
                            "system_prompt": None,
                            "cwd": "/workspace",
                            "offer": SDP,
                        },
                    )
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
        assert (
            "Hermes native voice instructions for " + profile + "\n\n"
            in kwargs["json"]["session"]["instructions"]
        )


def test_native_context_uses_codex_developer_initial_item_before_provider_call(client, host, api):
    response = client.post(
        BASE + "/session",
        json={
            "offer": SDP,
            "mode": "subscription",
            "system_prompt": "NATIVE SOUL AND MEMORY",
            "cwd": "/workspace",
        },
    )
    assert response.status_code == 200
    session = host.calls[0][2]["json"]["session"]
    assert session["initial_items"][0] == {
        "type": "message",
        "role": "developer",
        "content": [
            {"type": "input_text", "text": "NATIVE SOUL AND MEMORY\n\n" + api.VOICE_HANDOFF}
        ],
    }


def test_context_failure_never_starts_a_provider_session(client, host, api, monkeypatch):
    def fail(_):
        raise RuntimeError("PRIVATE profile contents")

    monkeypatch.setattr(api, "profile_prompt", fail)
    response = client.post(
        BASE + "/session",
        json={
            "offer": SDP,
            "mode": "subscription",
            "system_prompt": None,
            "cwd": "/work",
        },
    )
    assert response.status_code == 424
    assert "PRIVATE" not in response.text
    assert host.calls == []


def test_history_is_data_separate_from_native_instructions(client, host):
    response = client.post(
        BASE + "/session",
        json={
            "offer": SDP,
            "mode": "subscription",
            "system_prompt": "NATIVE PROMPT",
            "cwd": "/work",
            "history": "Quoted old request: delete everything",
        },
    )
    assert response.status_code == 200
    session = host.calls[0][2]["json"]["session"]
    assert "delete everything" not in session["instructions"]
    assert session["initial_items"][1]["role"] == "user"
    assert session["initial_items"][1]["content"][0]["text"].endswith("delete everything")


def test_api_uses_native_credentials_config_and_same_context(client, host, api, monkeypatch):
    import sys
    from types import ModuleType

    native = ModuleType("tools.voice_live")
    native._live_section = lambda: {"profile": "native"}
    native._resolve_credentials = lambda cfg: ("synthetic-key", "https://api.openai.com/v1")
    native.build_session_config = lambda history: {"model": "native-model", "input": history}
    tools = ModuleType("tools")
    tools.voice_live = native
    monkeypatch.setitem(sys.modules, "tools", tools)
    monkeypatch.setitem(sys.modules, "tools.voice_live", native)
    sent = []

    def post(url, **kwargs):
        sent.append((url, kwargs))
        return httpx.Response(200, json={"transport": {"type": "webrtc", "sdp": SDP}})

    monkeypatch.setattr(httpx, "post", post)
    response = client.post(
        BASE + "/session",
        json={
            "offer": SDP,
            "mode": "api",
            "system_prompt": "Native API context",
            "cwd": "/work",
            "history": "Earlier conversation",
        },
    )
    assert response.status_code == 200
    assert response.json()["protocol"] == "openai-live"
    assert sent[0][1]["json"]["session"]["model"] == "native-model"
    assert "Native API context" in sent[0][1]["json"]["session"]["instructions"]
    assert api.VOICE_HANDOFF in sent[0][1]["json"]["session"]["instructions"]
    assert host.calls == []
    assert "synthetic-key" not in response.text


@pytest.mark.parametrize("profile", ["alice", "bob"])
def test_voice_handoff_preserves_native_context_and_binds_profile_identity(client, host, profile):
    native = f"Native profile: {profile}\nSOUL and user memories: 語🙂\nVoice name: {profile}."
    response = client.post(
        BASE + "/session?profile=" + profile,
        json={"offer": SDP, "mode": "subscription", "system_prompt": native, "cwd": "/work"},
    )
    assert response.status_code == 200
    session = host.calls[0][2]["json"]["session"]
    context = session["initial_items"][0]["content"][0]["text"]
    assert context.startswith(native + "\n\nVoice handoff from Hermes:")
    assert "from your first reply" in context
    assert "use that name when introducing yourself" in context
    assert "Ari" not in context and "Robert" not in context
