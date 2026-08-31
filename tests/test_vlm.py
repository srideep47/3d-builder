"""Local VLM (Qwen2.5-VL plug point) tests — against a mock OpenAI-compatible
server. No GPU, no network: verifies the client, the advisory visual gate
wiring, and the fail-soft behaviour when nothing is configured.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from PIL import Image

from src.agent.loop import AgentLoop
from src.ai.vlm import LocalVLMClient, get_local_vlm

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
