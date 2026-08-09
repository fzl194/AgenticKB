"""Unit tests for vision / multimodal message helpers and Anthropic conversion."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from llm_service.providers.multimodal import (
    build_vision_user_message,
    content_as_plain_text,
    image_bytes_to_data_url,
    openai_content_to_anthropic,
)


def test_image_bytes_to_data_url():
    data = b"\x89PNG\r\n"
    url = image_bytes_to_data_url(data, "image/png")
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == data


def test_build_vision_user_message_from_bytes():
    msg = build_vision_user_message(
        "Describe this",
        image_bytes=b"fake-png",
        image_mime="image/png",
    )
    assert msg["role"] == "user"
    assert msg["content"][0] == {"type": "text", "text": "Describe this"}
    assert msg["content"][1]["type"] == "image_url"
    assert msg["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_vision_user_message_from_path(tmp_path: Path):
    img = tmp_path / "fig.png"
    img.write_bytes(b"\x89PNG")
    msg = build_vision_user_message("caption please", image_path=img)
    assert msg["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_vision_user_message_requires_exactly_one_source():
    with pytest.raises(ValueError):
        build_vision_user_message("x")
    with pytest.raises(ValueError):
        build_vision_user_message(
            "x", image_bytes=b"a", image_url="http://example.com/a.png",
        )


def test_openai_content_to_anthropic_data_url():
    b64 = base64.b64encode(b"abc").decode("ascii")
    content = [
        {"type": "text", "text": "What is this?"},
        {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
        },
    ]
    converted = openai_content_to_anthropic(content)
    assert converted[0] == {"type": "text", "text": "What is this?"}
    assert converted[1]["type"] == "image"
    assert converted[1]["source"]["type"] == "base64"
    assert converted[1]["source"]["media_type"] == "image/jpeg"
    assert converted[1]["source"]["data"] == b64


def test_openai_content_to_anthropic_http_url():
    content = [
        {
            "type": "image_url",
            "image_url": {"url": "https://example.com/a.png"},
        },
        {"type": "text", "text": "ok"},
    ]
    converted = openai_content_to_anthropic(content)
    assert converted[0]["source"] == {
        "type": "url",
        "url": "https://example.com/a.png",
    }


def test_content_as_plain_text():
    assert content_as_plain_text("hi") == "hi"
    assert content_as_plain_text([
        {"type": "text", "text": "a"},
        {"type": "image_url", "image_url": {"url": "x"}},
        {"type": "text", "text": "b"},
    ]) == "a\nb"


@pytest.mark.asyncio
async def test_anthropic_converts_multimodal_user_message():
    from llm_service.providers.anthropic import AnthropicProvider

    provider = AnthropicProvider(api_key="k", model="m")
    b64 = base64.b64encode(b"img").decode("ascii")
    system, msgs = provider._convert_messages([
        {"role": "system", "content": "Be brief."},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe"},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                },
            ],
        },
    ])
    assert system == "Be brief."
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"][0]["type"] == "text"
    assert msgs[0]["content"][1]["type"] == "image"
    assert msgs[0]["content"][1]["source"]["data"] == b64
    await provider.close()


def test_resolve_model_config_selects_vision_entry():
    from llm_service.config import resolve_model_config

    cfg = {
        "provider": {
            "active_model": "deepseek-chat",
            "timeout": 30,
            "models": {
                "deepseek-chat": {
                    "model": "deepseek-chat",
                    "base_url": "https://a/chat",
                    "api_key": "k1",
                },
                "glm-4.5v": {
                    "model": "glm-4.5v",
                    "base_url": "https://b/chat",
                    "api_key": "k2",
                    "timeout": 120,
                },
            },
        }
    }
    active = resolve_model_config(cfg)
    assert active["model"] == "deepseek-chat"
    vision = resolve_model_config(cfg, model_key="glm-4.5v")
    assert vision["model"] == "glm-4.5v"
    assert vision["base_url"] == "https://b/chat"
    assert vision["timeout"] == 120
