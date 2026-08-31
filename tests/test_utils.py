"""Locator and JSON-extraction utility tests."""

from src.ai.aptos import extract_json_from_text, vision_user_content
from src.blender.locate import parse_blender_version


def test_parse_version_full():
    assert parse_blender_version("Blender 4.5.13") == ("Blender 4.5.13", 4, 5, 13)


def test_parse_version_no_patch():
    assert parse_blender_version("Blender 4.2") == ("Blender 4.2.0", 4, 2, 0)


def test_parse_version_garbage():
    assert parse_blender_version("not blender at all") is None


def test_extract_json_bare():
    assert extract_json_from_text('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    text = 'Here is the spec:\n```json\n{"parts": []}\n```\nDone.'
    assert extract_json_from_text(text) == {"parts": []}


def test_extract_json_prose_wrapped():
    text = 'The result is {"b": [1, 2]} as requested.'
    assert extract_json_from_text(text) == {"b": [1, 2]}


def test_extract_json_invalid():
    assert extract_json_from_text("no json here") is None


def test_vision_user_content(tmp_path):
    img = tmp_path / "ref.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    parts = vision_user_content("describe this", [img])
    assert parts[0] == {"type": "text", "text": "describe this"}
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")
