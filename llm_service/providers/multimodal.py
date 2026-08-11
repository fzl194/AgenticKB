"""Helpers for OpenAI-style multimodal (vision) chat messages.

Canonical caller format (OpenAI-compatible)::

    {
      "role": "user",
      "content": [
        {"type": "text", "text": "Describe this figure"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}
      ]
    }

Anthropic providers convert these parts to ``image`` / ``source`` blocks.
"""
from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any


def image_file_to_data_url(path: str | Path) -> str:
    """Read an image file and return a ``data:<mime>;base64,...`` URL."""
    p = Path(path)
    data = p.read_bytes()
    mime, _ = mimetypes.guess_type(p.name)
    if not mime or not mime.startswith("image/"):
        suffix = p.suffix.lower().lstrip(".")
        mime = {
            "jpg": "image/jpeg",
            "jpeg": "image/jpeg",
            "png": "image/png",
            "gif": "image/gif",
            "webp": "image/webp",
            "bmp": "image/bmp",
        }.get(suffix, "image/png")
    return image_bytes_to_data_url(data, mime)


def image_bytes_to_data_url(data: bytes, mime: str = "image/png") -> str:
    """Encode raw image bytes as a data URL."""
    b64 = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{b64}"


def build_vision_user_message(
    text: str,
    *,
    image_path: str | Path | None = None,
    image_bytes: bytes | None = None,
    image_mime: str = "image/png",
    image_url: str | None = None,
) -> dict[str, Any]:
    """Build an OpenAI-style multimodal user message.

    Provide exactly one of ``image_path``, ``image_bytes``, or ``image_url``.
    """
    sources = [image_path is not None, image_bytes is not None, image_url is not None]
    if sum(1 for s in sources if s) != 1:
        raise ValueError("Provide exactly one of image_path, image_bytes, or image_url")

    if image_path is not None:
        url = image_file_to_data_url(image_path)
    elif image_bytes is not None:
        url = image_bytes_to_data_url(image_bytes, image_mime)
    else:
        url = str(image_url)

    return {
        "role": "user",
        "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": url}},
        ],
    }


def openai_content_to_anthropic(content: Any) -> Any:
    """Convert OpenAI message content to Anthropic Messages content.

    - ``str`` → unchanged
    - list of parts → Anthropic text/image blocks
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)

    blocks: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            blocks.append({"type": "text", "text": part.get("text", "")})
        elif ptype == "image_url":
            converted = _openai_image_url_to_anthropic(part)
            if converted is not None:
                blocks.append(converted)
        elif ptype in ("image", "tool_use", "tool_result"):
            # Already Anthropic-shaped (or pass-through)
            blocks.append(part)
        elif "text" in part and "type" not in part:
            blocks.append({"type": "text", "text": str(part.get("text", ""))})
    return blocks


def _openai_image_url_to_anthropic(part: dict[str, Any]) -> dict[str, Any] | None:
    image_url = part.get("image_url") or {}
    if isinstance(image_url, str):
        url = image_url
    else:
        url = str(image_url.get("url") or "")
    if not url:
        return None

    if url.startswith("data:"):
        header, _, b64 = url.partition(",")
        media_type = "image/png"
        if header.startswith("data:") and ";" in header:
            media_type = header[5:].split(";", 1)[0] or media_type
        return {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": b64,
            },
        }

    return {
        "type": "image",
        "source": {
            "type": "url",
            "url": url,
        },
    }


def content_as_plain_text(content: Any) -> str:
    """Best-effort flatten content to a plain string (for system prompts)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(p for p in parts if p)
    return str(content)
