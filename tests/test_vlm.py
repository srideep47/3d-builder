"""Vision provider tests — against mock OpenAI-compatible AND mock Gemini
v1beta servers. No GPU, no network, no live key: verifies both wire
formats, the VisionProvider ABC sharing, provider selection, and the
fail-soft behaviour when nothing is configured.

The mocked Gemini response mirrors the REAL shape recorded from the one
live smoke call (PROGRESS.md T5): candidates[0].content.parts[*].text,
finishReason, usageMetadata{thoughtsTokenCount, promptTokensDetails},
modelVersion, responseId.
"""

from __future__ import annotations

import base64
import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from PIL import Image

from src.agent.loop import AgentLoop
from src.ai.vlm import (GEMINI_API_KEY_ENV, GeminiVisionProvider,
                        LocalVLMClient, OpenAICompatibleVisionProvider,
                        VisionProvider, get_local_vlm, get_vision_provider)

mock_vlm_app = FastAPI()

DESCRIPTION_TEXT = (
    "1. Overall: a small decorative sculpture on a cylindrical pedestal.\n"
    "2. Parts: pedestal (cylinder), blob (freeform organic solid).\n"
    "3. Proportions: blob is roughly 1.5x wider than the pedestal top.\n"
    "4. Materials: white marble.\n"
    "5. Distinctive: the blob is smooth and asymmetric."
)

VERDICT_JSON = (
    '{"matches_reference": true, "score": 8, '
    '"issues": ["blob slightly less round"], "summary": "close match"}'
)


@mock_vlm_app.get("/v1/models")
def list_models():
    return {"data": [{"id": "fake-vlm"}]}


@mock_vlm_app.post("/v1/chat/completions")
async def chat(payload: dict):
    messages = payload.get("messages") or []
    user = messages[-1] if messages else {}
    content = user.get("content", "")
    text = ""
    if isinstance(content, list):
        text = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
    elif isinstance(content, str):
        text = content
    body = VERDICT_JSON if "Visual Tester" in text else DESCRIPTION_TEXT
    return {"choices": [{"message": {"role": "assistant", "content": body}}]}


@pytest.fixture(scope="module")
def mock_vlm_url():
    import uvicorn

    config = uvicorn.Config(mock_vlm_app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("mock VLM server did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/v1"


def _make_image(tmp: Path) -> Path:
    img = Image.new("RGB", (32, 32), (90, 90, 130))
    p = tmp / "ref.png"
    img.save(p)
    return p


def test_client_not_configured():
    # config/ai.yaml ships with vision.vlm.base_url: null
    client = get_local_vlm()
    assert client is None or not client.is_configured()


def test_client_describe_and_verdict(mock_vlm_url, tmp_path):
    client = LocalVLMClient(base_url=mock_vlm_url, model="fake-vlm")
    assert client.is_configured()
    assert client.is_available() is True

    img = _make_image(tmp_path)
    description = client.describe_reference_images([img])
    assert "pedestal" in description

    verdict = client.visual_verdict(
        {"front": str(img), "iso": str(img)}, [img], model_summary="test: 2 parts"
    )
    assert verdict["available"] is True and verdict["parsed"] is True
    assert verdict["matches_reference"] is True
    assert verdict["score"] == 8
    assert verdict["issues"] == ["blob slightly less round"]


def test_client_dead_endpoint():
    client = LocalVLMClient(base_url="http://127.0.0.1:9/v1", model="fake-vlm")
    assert client.is_available() is False


def test_visual_gate_wiring(tmp_path):
    loop = AgentLoop.__new__(AgentLoop)
    loop._vlm = None
    loop._vlm_checked = False

    # nothing configured → no verdict, no event
    assert loop._run_visual_gate({}, [tmp_path], None, None) is None

    class FakeVLM:
        def is_available(self):
            return True

        def visual_verdict(self, renders, refs, model_summary=""):
            return {"available": True, "parsed": True, "matches_reference": False,
                    "score": 4, "issues": ["wrong proportions"], "summary": "meh"}

    loop._vlm = FakeVLM()
    loop._vlm_checked = True
    img = _make_image(tmp_path)
    events: list[dict] = []

    def emit(event, **data):
        events.append({"event": event, **data})

    verdict = loop._run_visual_gate({"front": str(img)}, [img], None, emit)
    assert verdict["score"] == 4
    assert events[0]["event"] == "visual_gate"
    assert events[0]["matches_reference"] is False

    # no reference images → no verdict
    assert loop._run_visual_gate({"front": str(img)}, [], None, None) is None


# ── Gemini v1beta provider (mocked; shape from the one live smoke call) ──────


mock_gemini_app = FastAPI()
GEMINI_CALLS: dict = {"model": None, "headers": None, "body": None}


@mock_gemini_app.get("/v1beta/models")
def gemini_list_models():
    return {"models": [{"name": "models/gemini-3.6-flash"}]}


@mock_gemini_app.post("/v1beta/models/{model}:generateContent")
async def gemini_generate(model: str, request: Request):
    GEMINI_CALLS["model"] = model
    GEMINI_CALLS["headers"] = dict(request.headers)
    GEMINI_CALLS["body"] = await request.json()
    parts = GEMINI_CALLS["body"]["contents"][0]["parts"]
    text = " ".join(p.get("text", "") for p in parts if "text" in p)
    reply = VERDICT_JSON if "Visual Tester" in text else DESCRIPTION_TEXT
    return {
        "candidates": [{
            "content": {"role": "model", "parts": [
                {"text": reply, "thoughtSignature": "sig"},
            ]},
            "finishReason": "STOP",
            "index": 0,
        }],
        "usageMetadata": {
            "promptTokenCount": 42, "candidatesTokenCount": 7,
            "totalTokenCount": 49, "thoughtsTokenCount": 3,
            "promptTokensDetails": [{"modality": "IMAGE", "tokenCount": 30}],
            "serviceTier": "standard",
        },
        "modelVersion": model,
        "responseId": "test",
    }


@pytest.fixture(scope="module")
def mock_gemini_url():
    import uvicorn

    config = uvicorn.Config(mock_gemini_app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while time.time() < deadline and not server.started:
        time.sleep(0.05)
    if not server.started:
        pytest.fail("mock Gemini server did not start")
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}"


@pytest.fixture(autouse=True)
def _no_leaked_keys(monkeypatch):
    """Tests own the key environment — the real values must never leak in
    (the provider reads the env at construction)."""
    monkeypatch.delenv("THREED_VLM_API_KEY", raising=False)
    monkeypatch.delenv(GEMINI_API_KEY_ENV, raising=False)


def test_both_providers_implement_the_abc():
    assert issubclass(OpenAICompatibleVisionProvider, VisionProvider)
    assert issubclass(GeminiVisionProvider, VisionProvider)
    assert LocalVLMClient is OpenAICompatibleVisionProvider


def test_gemini_wire_format(mock_gemini_url, tmp_path, monkeypatch):
    """The v1beta contract: :generateContent endpoint, x-goog-api-key header
    (never Bearer), contents/parts body with inline_data images,
    systemInstruction, generationConfig — and the key comes from the env."""
    monkeypatch.setenv("THREED_VLM_API_KEY", "test-key-123")
    img = _make_image(tmp_path)
    provider = GeminiVisionProvider(model="gemini-3.6-flash",
                                    base_url=mock_gemini_url)
    assert provider.is_configured()
    assert provider.is_available() is True

    out = provider.chat_vision("describe this", [img],
                               system="be terse", max_tokens=777)
    assert "pedestal" in out  # parsed from candidates[0].content.parts[].text
    assert GEMINI_CALLS["model"] == "gemini-3.6-flash"

    headers = GEMINI_CALLS["headers"]
    assert headers.get("x-goog-api-key") == "test-key-123"
    assert "authorization" not in headers  # v1beta is NOT Bearer-shaped

    body = GEMINI_CALLS["body"]
    assert set(body) >= {"contents", "generationConfig"}
    (content,) = body["contents"]
    assert content["role"] == "user"
    text_part, image_part = content["parts"][0], content["parts"][1]
    assert text_part["text"] == "describe this"
    inline = image_part["inline_data"]
    assert inline["mime_type"] == "image/png"
    assert base64.b64decode(inline["data"]) == img.read_bytes()
    assert body["generationConfig"]["maxOutputTokens"] == 777
    assert body["systemInstruction"]["parts"][0]["text"] == "be terse"


def test_gemini_key_falls_back_to_gemini_env(mock_gemini_url, monkeypatch):
    """GEMINI_API_KEY holds the same value — used when THREED_VLM_API_KEY
    is absent."""
    monkeypatch.setenv(GEMINI_API_KEY_ENV, "fallback-key")
    provider = GeminiVisionProvider(model="gemini-3.6-flash",
                                    base_url=mock_gemini_url)
    assert provider.is_configured()
    assert provider.api_key == "fallback-key"


def test_gemini_unconfigured_without_any_key():
    provider = GeminiVisionProvider(model="gemini-3.6-flash")
    assert provider.api_key is None
    assert provider.is_configured() is False
    assert provider.is_available() is False


def test_gemini_rejects_latest_alias():
    """Floating -latest aliases must never pass — pin a version."""
    with pytest.raises(ValueError, match="pin"):
        GeminiVisionProvider(model="gemini-flash-latest")


def test_gemini_shared_integration_points(mock_gemini_url, tmp_path, monkeypatch):
    """describe_reference_images / visual_verdict are shared through the
    ABC and work unchanged over the Gemini wire."""
    monkeypatch.setenv("THREED_VLM_API_KEY", "test-key-123")
    img = _make_image(tmp_path)
    provider = GeminiVisionProvider(model="gemini-3.6-flash",
                                    base_url=mock_gemini_url)

    assert "pedestal" in provider.describe_reference_images([img])
    verdict = provider.visual_verdict(
        {"front": str(img), "iso": str(img)}, [img], model_summary="t: 2 parts")
    assert verdict["available"] is True and verdict["parsed"] is True
    assert verdict["matches_reference"] is True
    assert verdict["score"] == 8
    assert verdict["issues"] == ["blob slightly less round"]


def test_gemini_dead_endpoint_fails_soft(monkeypatch):
    monkeypatch.setenv("THREED_VLM_API_KEY", "test-key-123")
    provider = GeminiVisionProvider(model="gemini-3.6-flash",
                                    base_url="http://127.0.0.1:9")
    assert provider.is_available() is False
    verdict = provider.visual_verdict({"front": "x.png"}, ["r.png"])
    assert verdict == {"available": False,
                       "reason": "vision provider not available"}


def test_factory_selects_provider(monkeypatch):
    def _cfg(vlm):
        monkeypatch.setattr(
            VisionProvider, "_load_vision_config",
            staticmethod(lambda: {"vlm": vlm}))

    _cfg({"provider": "gemini", "model": "gemini-3.6-flash"})
    monkeypatch.setenv("THREED_VLM_API_KEY", "k")
    assert isinstance(get_vision_provider(), GeminiVisionProvider)

    _cfg({"provider": "local", "base_url": "http://x/v1", "model": "qwen"})
    assert isinstance(get_vision_provider(), OpenAICompatibleVisionProvider)

    # fail-soft: a broken config (e.g. -latest) degrades to no vision
    _cfg({"provider": "gemini", "model": "gemini-flash-latest"})
    assert get_vision_provider() is None

    # unconfigured → None, never raises
    _cfg({"provider": "local", "base_url": None, "model": None})
    assert get_vision_provider() is None

    # the historical alias still works
    assert get_local_vlm() is None or isinstance(get_local_vlm(), VisionProvider)
