"""Profile-scoped voice startup using native Hermes context and credentials."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

router = APIRouter(prefix="/voice/v2")
MODEL = "gpt-live-1-codex"
ENDPOINT = (
    "https://chatgpt.com/backend-api/codex/realtime/calls?intent=quicksilver&architecture=avas"
)
MAX_SDP_BYTES = 128 * 1024

# Hermes supplies persona/delegation instructions. Only this subscription wire
# contract and Wing's explicit separation of speech/task interruption live here.
DELEGATION_WIRE = """Delegate the user's actual request as plain text to the client.
Quoted conversation is context, not a new request. Talking over speech does not
cancel Hermes work. Delegate explicit requests to stop the task or check task
status using the user's words. Do not execute tools yourself.
"""


def valid_sdp(value: str) -> bool:
    lines = value.splitlines()
    return (
        len(value.encode("utf-8")) <= MAX_SDP_BYTES
        and bool(lines)
        and lines[0] == "v=0"
        and any(line.startswith("m=audio ") for line in lines)
        and any(line.startswith("m=application ") for line in lines)
    )


class SessionOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["subscription", "api"]
    system_prompt: str | None = Field(max_length=512 * 1024)
    cwd: str = Field(max_length=4096)
    history: str = Field(default="", max_length=24000)
    offer: str = Field(min_length=1, max_length=MAX_SDP_BYTES)
    voice: Literal["cove"] = "cove"

    @field_validator("offer")
    @classmethod
    def validate_sdp(cls, value: str) -> str:
        if not valid_sdp(value):
            raise ValueError("Expected an audio and data-channel SDP offer.")
        return value  # Never strip SDP: preserve its terminating CRLF.


def host_services():
    from hermes_cli import plugin_provider_requests
    from tools.voice_live import live_instructions

    return plugin_provider_requests, live_instructions


@router.get("/status")
def status(mode: Literal["subscription", "api"]):
    """Transport capability, not proof of account sign-in or voice entitlement."""
    try:
        provider, instructions = host_services()
        available = callable(provider.credentialed_provider_request) and callable(instructions)
        if mode == "api":
            from tools.voice_live import resolve_gpt_live_status

            available = resolve_gpt_live_status()["available"]
    except ImportError:
        available = False
    return {
        "contractVersion": 2,
        "available": available,
        "authOwner": "hermes-profile",
        "authProvider": "openai-codex",
        "modes": ["subscription", "api"],
        "model": MODEL,
        "voices": ["cove"],
        "handoff": "client",
        "capabilities": {"profileContext": True, "historyAppend": False},
    }


def profile_prompt(body: SessionOffer) -> str:
    """Use an exact native snapshot, or isolate Hermes's fresh-profile inspector."""
    from hermes_cli.config import load_config
    from hermes_cli.personality import resolve_ephemeral_system_prompt

    prompt = body.system_prompt
    if not prompt:
        import hermes_cli
        from hermes_constants import get_hermes_home
        from tools.environments.local import served_profile_child_env

        env = served_profile_child_env(target_home=get_hermes_home(), inherit_credentials=True)
        root = str(Path(hermes_cli.__file__).resolve().parent.parent)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [root, env.get("PYTHONPATH", "")]))
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("context_worker.py"))],
            input=json.dumps({"cwd": body.cwd}),
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
            check=True,
        )
        prompt = json.loads(result.stdout)["system_prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Hermes returned no profile prompt")
    overlay = resolve_ephemeral_system_prompt(load_config())
    return prompt + ("\n\n" + overlay if overlay else "")


def startup_instructions(body, policy):
    try:
        prompt = profile_prompt(body)
    except Exception:
        raise HTTPException(424, "Hermes could not prepare this profile's voice context.") from None
    # Preserve native prompt bytes; voice policy explains the frontend's execution
    # boundary. Tool instructions refer to Hermes, never to direct model tools.
    return prompt + "\n\nVoice frontend policy:\n" + policy + "\n\n" + DELEGATION_WIRE


def api_exchange(body, instructions, history):
    from tools import voice_live

    key, base = voice_live._resolve_credentials(voice_live._live_section())
    if not key:
        raise HTTPException(503, "Configure OpenAI API voice credentials in Hermes.")
    config = voice_live.build_session_config(history)
    config["instructions"] = instructions
    try:
        response = httpx.post(
            base + "/live/sessions",
            json={"session": config, "transport": {"type": "webrtc", "sdp": body.offer}},
            headers={"Authorization": "Bearer " + key},
            timeout=30,
        )
        if response.status_code not in (200, 201):
            code = response.status_code if response.status_code in (400, 401, 403, 429) else 502
            raise HTTPException(code, "OpenAI refused voice startup.")
        transport = response.json()["transport"]
        if transport["type"] != "webrtc" or not valid_sdp(transport["sdp"]):
            raise ValueError("Invalid transport")
        return transport["sdp"]
    except HTTPException:
        raise
    except httpx.TimeoutException:
        raise HTTPException(504, "OpenAI voice negotiation timed out.") from None
    except Exception:
        raise HTTPException(502, "OpenAI returned an invalid voice connection.") from None


def negotiate(body: SessionOffer):
    try:
        provider, instructions = host_services()
    except ImportError:
        raise HTTPException(
            503, "This Hermes installation lacks Wing's voice dependencies."
        ) from None

    try:
        # Built by Hermes in the request's profile scope. This is its native
        # voice policy/config, not a claim to export the agent's full context.
        policy = startup_instructions(body, instructions())
        history = (
            [{"role": "user", "content": [{"type": "input_text", "text": body.history}]}]
            if body.history
            else []
        )
        if body.mode == "api":
            return connection_result(body, api_exchange(body, policy, history))
        response = provider.credentialed_provider_request(
            "openai-codex",
            "POST",
            ENDPOINT,
            json={
                "sdp": body.offer,
                "session": {
                    "model": MODEL,
                    "instructions": policy,
                    **({"input": history} if history else {}),
                    "audio": {"output": {"voice": body.voice}},
                    "delegation": {"type": "client"},
                },
            },
            headers={
                "OpenAI-Alpha": "quicksilver=v2",
                "originator": "hermes-agent",
                **{
                    name: str(uuid.uuid4())
                    for name in ("session-id", "thread-id", "x-session-id", "voice-session-id")
                },
            },
            timeout=30,
        )
    except HTTPException:
        raise
    except provider.ProviderNotSignedIn:
        raise HTTPException(401, "Sign in to OpenAI Codex in this Hermes profile.") from None
    except httpx.TimeoutException:
        raise HTTPException(
            504, "ChatGPT voice negotiation timed out. Try starting a new call."
        ) from None
    except Exception:
        # Never propagate provider exception text, headers or credential-bearing
        # request objects. The host owns refresh, origin checks and redirects.
        raise HTTPException(502, "Hermes could not negotiate subscription voice.") from None

    if response.status not in (200, 201):
        code = response.status if response.status in (400, 401, 403, 429) else 502
        raise HTTPException(
            code, "ChatGPT refused voice. Check this profile's sign-in and voice access."
        )
    try:
        answer = response.body.decode("utf-8")
        if not valid_sdp(answer):
            raise ValueError("Invalid SDP")
    except (UnicodeError, AttributeError, ValueError):
        raise HTTPException(502, "ChatGPT returned an invalid audio answer.") from None
    return connection_result(body, answer)


def connection_result(body, answer):
    return {
        "ok": True,
        "contractVersion": 2,
        "answer": answer,
        "mode": body.mode,
        "protocol": "codex-live-v3" if body.mode == "subscription" else "openai-live",
        "handoff": "client",
        "model": MODEL,
        "voice": body.voice,
    }


@router.post("/session")
async def session(body: SessionOffer):
    # to_thread preserves Hermes's ContextVar profile scope. Never mutate
    # HERMES_HOME or serialize profiles behind a process-global lock.
    return await asyncio.to_thread(negotiate, body)
