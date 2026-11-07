"""Subscription SDP exchange; mounted and profile-scoped by Hermes.

No credential reads, profile discovery, context assembly, tool execution or
history writes belong here. API-key voice uses Hermes's stock audio routes.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

router = APIRouter(prefix="/voice/v1")
MODEL = "gpt-live-1-codex"
ENDPOINT = (
    "https://chatgpt.com/backend-api/codex/realtime/calls?intent=quicksilver&architecture=avas"
)
MAX_SDP_BYTES = 128 * 1024

# Hermes supplies persona/delegation instructions. Only this subscription wire
# contract and Wing's explicit separation of speech/task interruption live here.
DELEGATION_WIRE = """Client delegation format for this voice connection:
Emit a JSON object with action and text fields for each client delegation.
Actions: task (new work), steer (additional instructions for running work),
status (request progress), stop (an explicit request to stop running work).
Include the user's actual request in text. Quoted conversation is context,
not a new request. Talking over speech does not cancel Hermes work; only an
explicit request to stop the task does. Do not execute tools yourself.
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
def status():
    """Transport capability, not proof of account sign-in or voice entitlement."""
    try:
        provider, instructions = host_services()
        available = callable(provider.credentialed_provider_request) and callable(instructions)
    except ImportError:
        available = False
    return {
        "contractVersion": 1,
        "available": available,
        "authOwner": "hermes-profile",
        "authProvider": "openai-codex",
        "modes": ["subscription"],
        "model": MODEL,
        "voices": ["cove"],
        "handoff": "client",
        "capabilities": {"profileContext": False, "historyAppend": False},
    }


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
        policy = instructions()
        response = provider.credentialed_provider_request(
            "openai-codex",
            "POST",
            ENDPOINT,
            json={
                "sdp": body.offer,
                "session": {
                    "model": MODEL,
                    "instructions": policy + "\n\n" + DELEGATION_WIRE,
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
        code = response.status if response.status in (401, 403, 429) else 502
        raise HTTPException(
            code, "ChatGPT refused voice. Check this profile's sign-in and voice access."
        )
    try:
        answer = response.body.decode("utf-8")
        if not valid_sdp(answer):
            raise ValueError("Invalid SDP")
    except (UnicodeError, AttributeError, ValueError):
        raise HTTPException(502, "ChatGPT returned an invalid audio answer.") from None
    return {
        "ok": True,
        "contractVersion": 1,
        "answer": answer,
        "mode": "subscription",
        "protocol": "codex-live-v3",
        "handoff": "client",
        "model": MODEL,
        "voice": body.voice,
    }


@router.post("/session")
async def session(body: SessionOffer):
    # to_thread preserves Hermes's ContextVar profile scope. Never mutate
    # HERMES_HOME or serialize profiles behind a process-global lock.
    return await asyncio.to_thread(negotiate, body)
