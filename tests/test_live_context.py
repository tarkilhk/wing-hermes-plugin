"""Opt-in real subscription recall test. Never runs with account access in CI.

WING_LIVE_CONTEXT_TEST points to private JSON with base_url, login_file, profile,
audio_file (8 kHz mono 16-bit WAV question), expected_identity, expected_terms,
and calls (1–5 independent fresh sessions). The question must not supply the identity.
This opens the explicitly configured number of bounded voice calls. It writes no Hermes conversation turns.
"""

import array
import asyncio
import json
import os
import re
import time
import wave
from pathlib import Path

import httpx
import pytest


@pytest.mark.skipif(
    not os.environ.get("WING_LIVE_CONTEXT_TEST"), reason="Explicit live account test only"
)
def test_subscription_recalls_native_profile_context():
    rtc = pytest.importorskip("aiortc")
    config = json.loads(Path(os.environ["WING_LIVE_CONTEXT_TEST"]).read_text())
    assert config["expected_terms"], "At least one profile fact is required"
    assert config["expected_identity"].strip(), "The profile voice identity is required"
    assert type(config["calls"]) is int and 1 <= config["calls"] <= 5
    credentials = json.loads(Path(config["login_file"]).read_text())

    class Speech(rtc.AudioStreamTrack):
        def __init__(self):
            super().__init__()
            with wave.open(config["audio_file"], "rb") as wav:
                assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (8000, 1, 2)
                self.audio = wav.readframes(wav.getnframes())
            self.started = None
            self.position = 0

        async def recv(self):
            frame = await super().recv()
            if self.started and time.monotonic() - self.started > 2:
                chunk = self.audio[self.position : self.position + frame.samples * 2]
                self.position += len(chunk)
                frame.planes[0].update(chunk.ljust(frame.planes[0].buffer_size, b"\0"))
            return frame

    async def exercise(attempt):
        pc = rtc.RTCPeerConnection()
        speech = Speech()
        pc.addTrack(speech)
        channel = pc.createDataChannel("oai-events")
        answers, errors, questions = [], [], []
        completed = asyncio.Event()
        frames = 0
        peak = 0

        @pc.on("track")
        def track(stream):
            async def receive():
                nonlocal frames, peak
                try:
                    while True:
                        frame = await stream.recv()
                        frames += 1
                        samples = array.array("h", bytes(frame.planes[0]))
                        peak = max(peak, max(map(abs, samples), default=0))
                except rtc.mediastreams.MediaStreamError:
                    pass

            asyncio.create_task(receive())

        @channel.on("message")
        def message(raw):
            event = json.loads(raw)
            if event.get("type") == "session.started":
                speech.started = time.monotonic()
            if event.get("type") in ("error", "session.error", "session.closed"):
                errors.append(event["type"])
                completed.set()
            turn = event.get("turn", {})
            if event.get("type") == "turn.done" and turn.get("role") == "user":
                questions.append(turn.get("transcript", ""))
            if event.get("type") == "turn.done" and turn.get("role") == "assistant":
                answers.append(turn.get("transcript", ""))
                completed.set()

        try:
            async with httpx.AsyncClient(base_url=config["base_url"], timeout=80) as client:
                login = await client.post(
                    "/auth/password-login",
                    json={key: credentials[key] for key in ("provider", "username", "password")},
                )
                assert login.status_code == 200, "Dashboard sign-in failed"
                await pc.setLocalDescription(await pc.createOffer())
                response = await client.post(
                    "/api/plugins/wing/voice/v2/session",
                    params={"profile": config["profile"]},
                    json={
                        "offer": pc.localDescription.sdp,
                        "voice": "cove",
                        "mode": "subscription",
                        "system_prompt": None,
                        "cwd": config.get("cwd", ""),
                        "history": "",
                    },
                )
                assert response.status_code == 200, f"Voice startup HTTP {response.status_code}"
                await pc.setRemoteDescription(
                    rtc.RTCSessionDescription(sdp=response.json()["answer"], type="answer")
                )
                await asyncio.wait_for(completed.wait(), timeout=40)
                assert not errors, "Provider rejected or closed the session"
                assert frames > 0 and peak > 0 and speech.position > 0, "No audible audio exchanged"
                assert len(answers) == 1, "Acceptance must inspect the first reply only"
                assert questions, "The provider must have heard the spoken question"
                assert not re.search(
                    r"\b" + re.escape(config["expected_identity"]) + r"\b",
                    " ".join(questions),
                    re.IGNORECASE,
                ), "The spoken question supplied the identity"
                assert identifies_in_first_reply(answers[0], config["expected_identity"]), (
                    "First spoken reply did not adopt the profile voice identity"
                )
                answer = answers[0].casefold()
                # Never print personal answers or expected profile terms in failure output.
                assert all(term.casefold() in answer for term in config["expected_terms"]), (
                    "Spoken answer did not recall the expected native profile facts"
                )
                print(
                    f"Call {attempt + 1}: first-reply identity and user facts verified; "
                    f"received {frames} audio frames with peak {peak}."
                )
        finally:
            if channel.readyState == "open":
                channel.send(json.dumps({"type": "session.close"}))
                await asyncio.sleep(0.2)
            await pc.close()

    for attempt in range(config["calls"]):
        asyncio.run(exercise(attempt))


def identifies_in_first_reply(answer, identity):
    normalized = answer.casefold().replace("’", "'")
    name = re.escape(identity.casefold())
    return not re.search(
        r"\b(?:i'm|i am|my name is|my name's|this is)\s+(?:chatgpt|gpt[- ]live)\b", normalized
    ) and bool(
        re.search(rf"\b(?:i'm|i am|my name is|my name's|this is)\s+{name}\b", normalized)
        or re.match(rf"\s*{name}\s+here\b", normalized)
    )


@pytest.mark.parametrize(
    "answer,expected",
    [
        ("I'm Ari, and I know you're Robert.", True),
        ("Hi, I’m Ari from Hermes.", True),
        ("My name is Ari.", True),
        ("Ari here. Hello Robert.", True),
        ("I'm ChatGPT. Your notes say Ari is the Hermes voice.", False),
        ("I'm ChatGPT, but your notes mention Ari.", False),
        ("I'm Ariana.", False),
        ("Hello. I'm Ari.", True),
        ("I'm ChatGPT. My name is Ari.", False),
    ],
)
def test_identity_acceptance_requires_consistent_self_identification(answer, expected):
    assert identifies_in_first_reply(answer, "Ari") is expected
