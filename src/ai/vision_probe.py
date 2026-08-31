"""Vision probe — tests endpoint for multimodal capability."""

from __future__ import annotations

import httpx

TINY_PNG_B64 = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="


def probe_vision_support(base_url: str, model_id: str, api_key: str = "test", timeout: float = 10.0) -> bool:
    """Sends a tiny 1x1 test image to determine if the endpoint supports multimodal vision."""
    url = f"{base_url.rstrip('/')}/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "ping"},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{TINY_PNG_B64}"}},
                ],
            }
        ],
        "max_tokens": 5,
    }

    try:
        resp = httpx.post(url, json=payload, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return True
        return False
    except Exception:
        return False
