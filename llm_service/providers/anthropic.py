"""Anthropic Messages API provider.

Implements ProviderProtocol for direct Anthropic API calls.
Uses the /v1/messages endpoint with x-api-key authentication.
"""
from __future__ import annotations

import json

import httpx

from llm_service.providers.base import ProviderError, ProviderResponse

# Injected when response_format=json_object to guide models that
# don't support native tool_use.
_JSON_INSTRUCTION = (
    "\n\nYou MUST respond with valid JSON only. "
    "Do not include any text outside the JSON object."
)


class AnthropicProvider:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com/v1/messages",
        api_version: str = "2023-06-01",
        headers: dict | None = None,
        timeout: int = 60,
        bypass_proxy: bool = False,
    ):
        self._url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._api_version = api_version
        self._extra_headers = headers or {}
        self._timeout = timeout
        transport = httpx.AsyncHTTPTransport() if bypass_proxy else None
        self._client = httpx.AsyncClient(transport=transport, timeout=timeout, trust_env=not bypass_proxy)

    async def close(self) -> None:
        await self._client.aclose()

    @property
    def provider_name(self) -> str:
        return "anthropic"

    @property
    def default_model(self) -> str:
        return self._model

    def _convert_messages(self, messages: list[dict]) -> tuple[str | None, list[dict]]:
        """Convert OpenAI-style messages to Anthropic format.

        Supports multimodal user content (``image_url`` parts → Anthropic
        ``image`` blocks). Returns (system_prompt, anthropic_messages).
        """
        from llm_service.providers.multimodal import (
            content_as_plain_text,
            openai_content_to_anthropic,
        )

        system = None
        converted: list[dict] = []
        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            if role == "system":
                # Anthropic system is a plain string (or text blocks via API
                # variants); flatten multimodal system content to text.
                text = content_as_plain_text(content)
                system = text if system is None else f"{system}\n{text}"
            elif role in ("user", "assistant"):
                converted.append({
                    "role": role,
                    "content": openai_content_to_anthropic(content),
                })
        return system, converted

    @staticmethod
    def _extract_json(text: str) -> str:
        """Try to extract a JSON object/array from text.

        Returns the extracted JSON string, or wraps the text as
        ``{"text": "..."}`` if no JSON structure is found.
        """
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            return stripped

        # Look for first { or [
        start = stripped.find("{")
        if start == -1:
            start = stripped.find("[")
        if start >= 0:
            open_ch = stripped[start]
            close_ch = "}" if open_ch == "{" else "]"
            depth = 0
            for i in range(start, len(stripped)):
                if stripped[i] == open_ch:
                    depth += 1
                elif stripped[i] == close_ch:
                    depth -= 1
                    if depth == 0:
                        return stripped[start : i + 1]

        # No JSON found — wrap in envelope
        return json.dumps({"text": stripped}, ensure_ascii=False)

    async def complete(
        self,
        messages: list[dict],
        params: dict,
        *,
        response_format: dict | None = None,
    ) -> ProviderResponse:
        want_json = (
            response_format is not None
            and response_format.get("type") == "json_object"
        )
        # Optional: caller can plumb a real JSON Schema via response_format["schema"]
        # to strengthen tool_use constraints. Default empty schema just triggers tool_use.
        json_schema = response_format.get("schema") if want_json else None

        system, anthropic_messages = self._convert_messages(messages)

        # Inject JSON instruction into system prompt so models that
        # don't support tool_use still try to return JSON.
        if want_json:
            if system is None:
                system = _JSON_INSTRUCTION.strip()
            else:
                system += _JSON_INSTRUCTION

        headers = {
            "x-api-key": self._api_key,
            "anthropic-version": self._api_version,
            "Content-Type": "application/json",
            **self._extra_headers,
        }

        body: dict = {
            "model": self._model,
            "messages": anthropic_messages,
            **params,
        }
        if system is not None:
            body["system"] = system

        # Also attempt tool_use for models that support it
        if want_json:
            input_schema = json_schema if json_schema else {
                "type": "object",
                "properties": {},
            }
            body.setdefault("tools", []).append({
                "name": "structured_output",
                "description": "Output structured JSON",
                "input_schema": input_schema,
            })
            body["tool_choice"] = {"type": "tool", "name": "structured_output"}

        try:
            resp = await self._client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException as e:
            raise ProviderError("timeout", str(e)) from e
        except httpx.ConnectError as e:
            raise ProviderError("connection_error", str(e)) from e

        if resp.status_code == 429:
            raise ProviderError("rate_limited", resp.text)
        if resp.status_code >= 500:
            raise ProviderError("server_error", f"HTTP {resp.status_code}: {resp.text}")
        if resp.status_code >= 400:
            raise ProviderError("client_error", f"HTTP {resp.status_code}: {resp.text}")

        try:
            data = resp.json()
        except Exception as e:
            raise ProviderError(
                "invalid_response", f"Non-JSON response from provider: {e}"
            ) from e

        # Extract text content from Anthropic response blocks
        content_blocks = data.get("content", [])
        output_parts: list[str] = []
        for block in content_blocks:
            if block.get("type") == "text":
                output_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use" and block.get("name") == "structured_output":
                output_parts.append(json.dumps(block.get("input", {}), ensure_ascii=False))
        output_text = "\n".join(output_parts)

        # If json_object was requested, ensure output is valid JSON
        if want_json and output_text:
            output_text = self._extract_json(output_text)

        usage = data.get("usage", {})
        return ProviderResponse(
            output_text=output_text,
            prompt_tokens=usage.get("input_tokens"),
            completion_tokens=usage.get("output_tokens"),
            total_tokens=(usage.get("input_tokens", 0) or 0) + (usage.get("output_tokens", 0) or 0),
            raw_response=data,
        )
