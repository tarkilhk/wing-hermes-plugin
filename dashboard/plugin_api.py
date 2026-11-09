"""Profile-scoped voice startup using native Hermes context and credentials."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import traceback
import uuid
from pathlib import Path
from typing import Literal

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

router = APIRouter()
# Hermes imports dashboard APIs as standalone files, outside the plugin package.
_spec = importlib.util.spec_from_file_location(
    "wing_email_drafts_api", Path(__file__).resolve().parent.parent / "drafts.py"
)
_drafts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_drafts)


@router.get("/drafts/v1/{draft_id}/versions/{version}")
def email_draft(draft_id: str, version: int):
    try:
        return _drafts.DraftStore().read(draft_id, version)
    except _drafts.DraftError as exc:
        raise HTTPException(exc.code, str(exc)) from None


MODEL = "gpt-live-1-codex"
ENDPOINT = (
    "https://chatgpt.com/backend-api/codex/realtime/calls?intent=quicksilver&architecture=avas"
)
MAX_SDP_BYTES = 128 * 1024
API_MODE_UNAVAILABLE = (
    "OpenAI API voice is unavailable until Hermes supports per-call instructions and voice "
    "in its host-owned session exchange. Use ChatGPT subscription voice. "
    "https://github.com/NousResearch/hermes-agent/issues/151774"
)

# Hermes supplies persona/delegation instructions. Only this subscription wire
# contract and Wing's explicit separation of speech/task interruption live here.
DELEGATION_WIRE = """Delegate the user's actual request as plain text to the client.
Quoted conversation is context, not a new request. Talking over speech does not
cancel Hermes work. Delegate explicit requests to stop the task or check task
status using the user's words. Do not execute tools yourself.
"""


# Bind the live speaker to Hermes-owned identity/memory without parsing names or
# inventing a second persona. Sent in startup context before audio can begin.
VOICE_HANDOFF = """Voice handoff from Hermes:
You are the voice of the Hermes profile above. Use its identity from your first reply. If its memory records a voice name, use that name when introducing yourself or answering who you are.
Answer questions about the user from the supplied profile, memory and chat history. Say when a fact is missing.
Follow the existing voice and delegation policies for speaking and tool work."""


def valid_sdp(value: str) -> bool:
    lines = value.splitlines()
    return (
        len(value.encode("utf-8")) <= MAX_SDP_BYTES
        and bool(lines)
        and lines[0] == "v=0"
        and any(line.startswith("m=audio ") for line in lines)
        and any(line.startswith("m=application ") for line in lines)
    )


SUBSCRIPTION_VOICES = (
    "arbor",
    "breeze",
    "cove",
    "ember",
    "juniper",
    "maple",
    "sol",
    "spruce",
    "vale",
)


class SessionOffer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    mode: Literal["subscription", "api"]
    system_prompt: str | None = Field(max_length=512 * 1024)
    cwd: str = Field(max_length=4096)
    history: str = Field(default="", max_length=24000)
    offer: str = Field(min_length=1, max_length=MAX_SDP_BYTES)
    voice: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_voice(self):
        if self.mode == "subscription" and self.voice not in SUBSCRIPTION_VOICES:
            raise ValueError("Choose a supported voice for this billing mode.")
        return self

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


@router.get("/voice/v2/status")
def status(mode: Literal["subscription", "api"]):
    """Transport capability, not proof of account sign-in or voice entitlement."""
    available = False
    voices = []
    if mode == "subscription":
        try:
            provider, instructions = host_services()
            voices = list(SUBSCRIPTION_VOICES)
            available = callable(provider.credentialed_provider_request) and callable(instructions)
        except ImportError:
            pass
    return {
        "contractVersion": 2,
        "available": available,
        "authOwner": "hermes-profile",
        "authProvider": "openai-codex",
        "modes": ["subscription"],
        "model": MODEL,
        "voices": voices,
        "handoff": "client",
        "capabilities": {"profileContext": True, "historyAppend": False},
        **({"reason": API_MODE_UNAVAILABLE} if mode == "api" else {}),
    }


def profile_prompt(body: SessionOffer) -> str:
    """Use an exact native snapshot, or isolate Hermes's fresh-profile inspector."""
    from hermes_cli.config import load_config
    from hermes_cli.personality import resolve_ephemeral_system_prompt

    config = load_config()
    prompt = body.system_prompt
    if not prompt:
        import hermes_cli
        from hermes_constants import get_hermes_home
        from tools.environments.local import served_profile_child_env

        configured = (config.get("terminal") or {}).get("cwd")
        if not isinstance(configured, str) or not configured.strip():
            raise HTTPException(
                424, "Configure this Hermes profile's terminal.cwd for fresh voice context."
            )
        cwd = Path(configured).expanduser().resolve(strict=True)
        if not cwd.is_dir():
            raise HTTPException(424, "This Hermes profile's terminal.cwd must be a directory.")
        if Path(body.cwd).expanduser().resolve() != cwd:
            raise HTTPException(
                403,
                "Fresh voice context is limited to this profile's configured working directory.",
            )
        env = served_profile_child_env(target_home=get_hermes_home(), inherit_credentials=True)
        root = str(Path(hermes_cli.__file__).resolve().parent.parent)
        # This child runs Hermes itself, not a user terminal command. Preserve
        # managed-runtime dependency paths that the terminal env helper scrubs.
        paths = [root, *(p for p in sys.path if p and Path(p).is_absolute())]
        env["PYTHONPATH"] = os.pathsep.join(dict.fromkeys(paths))
        result = subprocess.run(
            [sys.executable, str(Path(__file__).with_name("context_worker.py"))],
            input=json.dumps({"cwd": str(cwd)}),
            text=True,
            capture_output=True,
            env=env,
            timeout=30,
            check=True,
        )
        result = json.loads(result.stdout)
        if "error" in result:
            raise HTTPException(
                424, {"message": "Hermes profile inspection failed.", **result["error"]}
            )
        prompt = result["system_prompt"]
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Hermes returned no profile prompt")
    overlay = resolve_ephemeral_system_prompt(config)
    return prompt + ("\n\n" + overlay if overlay else "")


def startup_context(body):
    try:
        prompt = profile_prompt(body)
    except HTTPException:
        raise
    except Exception as error:
        # Frame names and error types identify integration failures without
        # returning native exception messages, prompts, env or credentials.
        frames = traceback.extract_tb(error.__traceback__)
        raise HTTPException(
            424,
            {
                "message": "Hermes could not prepare this profile's voice context.",
                "kind": type(error).__name__,
                "location": frames[-1].name if frames else "profile_prompt",
            },
        ) from None
    return prompt + "\n\n" + VOICE_HANDOFF


def negotiate(body: SessionOffer):
    if body.mode == "api":
        raise HTTPException(503, API_MODE_UNAVAILABLE)
    try:
        provider, instructions = host_services()
    except ImportError:
        raise HTTPException(
            503, "This Hermes installation lacks Wing's voice dependencies."
        ) from None

    try:
        # Built by Hermes in the request's profile scope. This is its native
        # voice policy/config, not a claim to export the agent's full context.
        prompt = startup_context(body)
        policy = instructions() + "\n\n" + DELEGATION_WIRE
        history = (
            [{"role": "user", "content": [{"type": "input_text", "text": body.history}]}]
            if body.history
            else []
        )
        response = provider.credentialed_provider_request(
            "openai-codex",
            "POST",
            ENDPOINT,
            json={
                "sdp": body.offer,
                "session": {
                    "model": MODEL,
                    "instructions": policy,
                    # Codex subscription uses initial_items, not the API's
                    # input field. Preserve the Hermes prompt as developer data.
                    "initial_items": [
                        {
                            "type": "message",
                            "role": "developer",
                            "content": [{"type": "input_text", "text": prompt}],
                        },
                        *[{"type": "message", **item} for item in history],
                    ],
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
        "protocol": "codex-live-v3",
        "handoff": "client",
        "model": MODEL,
        "voice": body.voice,
    }


@router.post("/voice/v2/session")
async def session(body: SessionOffer):
    # to_thread preserves Hermes's ContextVar profile scope. Never mutate
    # HERMES_HOME or serialize profiles behind a process-global lock.
    return await asyncio.to_thread(negotiate, body)
