"""LLM provider abstraction.

The rest of the AI service talks to `AIProvider` only - a small interface
with one method, `complete()`. Swapping providers, or adding a new one, means
writing one adapter class here; nothing above this module knows which vendor
is behind it or how their wire format is shaped.

Both provided adapters (`AnthropicProvider`, `OpenAIProvider`) speak plain
HTTP via `httpx`, not a vendor SDK - one dependency less to pin, and it keeps
the translation between LEDGR's internal message/tool shape and each
provider's wire format fully visible in one place instead of behind a client
library's own abstractions.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from app.core.config import Settings, settings
from app.core.errors import AIUnavailable

logger = logging.getLogger("ledgr.ai")

Role = Literal["user", "assistant", "tool"]


@dataclass(frozen=True)
class ToolSpec:
    """One read-only capability offered to the model, described as JSON Schema."""

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Gemini 3 requires this signature to be returned on the next tool turn.
    thought_signature: str | None = None


@dataclass(frozen=True)
class AIMessage:
    """One turn of the conversation, in LEDGR's own provider-agnostic shape."""

    role: Role
    content: str | None = None
    # Present on an "assistant" message that requested tool calls.
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Present on a "tool" message: which call this is the result of.
    tool_call_id: str | None = None
    tool_name: str | None = None


@dataclass(frozen=True)
class Completion:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)

    @property
    def wants_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class AIProvider(Protocol):
    """Anything that can turn a conversation into the next assistant turn."""

    def complete(
        self,
        *,
        system: str,
        messages: list[AIMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> Completion: ...


class ProviderError(AIUnavailable):
    """The provider was configured but the call itself failed."""


def _raise_for_transport(exc: Exception, provider: str) -> None:
    logger.warning("ai provider call failed", extra={"provider": provider, "error": str(exc)})
    raise ProviderError(
        f"the {provider} API could not be reached or returned an error",
        details={"provider": provider},
    ) from exc


class AnthropicProvider:
    """Anthropic Messages API (`/v1/messages`), including tool use."""

    API_VERSION = "2023-06-01"

    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: float) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url or "https://api.anthropic.com"
        self._timeout = timeout

    def complete(
        self,
        *,
        system: str,
        messages: list[AIMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> Completion:
        body: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": self._to_wire_messages(messages),
        }
        if tools:
            body["tools"] = [
                {"name": t.name, "description": t.description, "input_schema": t.parameters}
                for t in tools
            ]

        try:
            response = httpx.post(
                f"{self._base_url}/v1/messages",
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": self.API_VERSION,
                    "content-type": "application/json",
                },
                json=body,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _raise_for_transport(exc, "anthropic")

        payload = response.json()
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        for block in payload.get("content", []):
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(id=block["id"], name=block["name"], arguments=block.get("input", {}))
                )
        return Completion(text="".join(text_parts) or None, tool_calls=tool_calls)

    @staticmethod
    def _to_wire_messages(messages: list[AIMessage]) -> list[dict[str, Any]]:
        """Anthropic requires strict user/assistant alternation.

        When one assistant turn requests more than one tool, this loop
        appends one `AIMessage(role="tool", ...)` per call - converting each
        independently would emit several consecutive "user" wire messages,
        which the API rejects. Consecutive tool results are merged into a
        single user turn carrying multiple `tool_result` blocks instead.
        """
        wire: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": message.tool_call_id,
                    "content": message.content or "",
                }
                if wire and wire[-1].get("_is_tool_result_turn"):
                    wire[-1]["content"].append(block)
                else:
                    wire.append({"role": "user", "content": [block], "_is_tool_result_turn": True})
                continue

            if message.tool_calls:
                content: list[dict[str, Any]] = []
                if message.content:
                    content.append({"type": "text", "text": message.content})
                for call in message.tool_calls:
                    content.append(
                        {
                            "type": "tool_use",
                            "id": call.id,
                            "name": call.name,
                            "input": call.arguments,
                        }
                    )
                wire.append({"role": "assistant", "content": content})
                continue

            wire.append({"role": message.role, "content": message.content or ""})

        for entry in wire:
            entry.pop("_is_tool_result_turn", None)
        return wire


class OpenAIProvider:
    """OpenAI-compatible Chat Completions API (`/v1/chat/completions`).

    "Compatible" is deliberate: pointing `ai_base_url` at any Chat
    Completions-shaped endpoint (a local model gateway, an Azure OpenAI
    deployment, etc.) works without a code change.
    """

    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: float) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url or "https://api.openai.com"
        self._timeout = timeout

    def complete(
        self,
        *,
        system: str,
        messages: list[AIMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> Completion:
        wire_messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        wire_messages.extend(self._to_wire_message(m) for m in messages)

        body: dict[str, Any] = {
            "model": self._model,
            "messages": wire_messages,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]

        try:
            response = httpx.post(
                f"{self._base_url}/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "content-type": "application/json",
                },
                json=body,
                timeout=self._timeout,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _raise_for_transport(exc, "openai")

        payload = response.json()
        message = payload["choices"][0]["message"]
        tool_calls: list[ToolCall] = []
        for call in message.get("tool_calls") or []:
            try:
                arguments = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                arguments = {}
            tool_calls.append(
                ToolCall(id=call["id"], name=call["function"]["name"], arguments=arguments)
            )
        return Completion(text=message.get("content"), tool_calls=tool_calls)

    @staticmethod
    def _to_wire_message(message: AIMessage) -> dict[str, Any]:
        if message.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": message.tool_call_id,
                "content": message.content or "",
            }
        if message.tool_calls:
            return {
                "role": "assistant",
                "content": message.content,
                "tool_calls": [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in message.tool_calls
                ],
            }
        return {"role": message.role, "content": message.content or ""}


class GeminiProvider:
    """Google Gemini generateContent API."""

    def __init__(self, *, api_key: str, model: str, base_url: str, timeout: float) -> None:
        self._api_key = api_key
        self._model = model
        self._base_url = base_url or "https://generativelanguage.googleapis.com"
        self._timeout = timeout

    def complete(
        self,
        *,
        system: str,
        messages: list[AIMessage],
        tools: list[ToolSpec] | None = None,
        max_tokens: int = 1024,
    ) -> Completion:
        contents = []

        for message in messages:
            if message.role == "user":
                contents.append({
                    "role": "user",
                    "parts": [{"text": message.content or ""}],
                })

            elif message.role == "assistant":
                parts = []

                if message.content:
                    parts.append({"text": message.content})

                for call in message.tool_calls:
                    function_call = {
                        "name": call.name,
                        "args": call.arguments,
                    }

                    part = {"functionCall": function_call}

                    # Gemini 3 requires the thought signature to stay
                    # attached to the exact functionCall Part.
                    if call.thought_signature:
                        part["thoughtSignature"] = call.thought_signature

                    parts.append(part)

                contents.append({
                    "role": "model",
                    "parts": parts or [{"text": ""}],
                })

            elif message.role == "tool":
                contents.append({
                    "role": "user",
                    "parts": [{
                        "functionResponse": {
                            "name": message.tool_name,
                            "id": message.tool_call_id,
                            "response": {
                                "result": message.content or "",
                            },
                        }
                    }],
                })

        body: dict[str, Any] = {
            "system_instruction": {
                "parts": [{"text": system}],
            },
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": max_tokens,
            },
        }

        if tools:
            body["tools"] = [{
                "functionDeclarations": [
                    {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    }
                    for t in tools
                ]
            }]

        try:
            response = httpx.post(
                f"{self._base_url}/v1beta/models/{self._model}:generateContent",
                headers={
                    "x-goog-api-key": self._api_key,
                    "content-type": "application/json",
                },
                json=body,
                timeout=self._timeout,
            )

            print("\n===== GEMINI DEBUG =====")
            print("STATUS:", response.status_code)
            print("BODY:", response.text)
            print("========================\n")

            response.raise_for_status()

        except httpx.HTTPError as exc:
            _raise_for_transport(exc, "gemini")

        payload = response.json()
        parts = payload["candidates"][0]["content"]["parts"]

        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []

        for part in parts:
            if "text" in part:
                text_parts.append(part["text"])

            elif "functionCall" in part:
                call = part["functionCall"]

                tool_calls.append(
                    ToolCall(
                        id=call.get("id", call["name"]),
                        name=call["name"],
                        arguments=call.get("args", {}),
                        thought_signature=part.get("thoughtSignature"),
                    )
                )

        return Completion(
            text="".join(text_parts) or None,
            tool_calls=tool_calls,
        )


_PROVIDERS: dict[str, type] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "gemini": GeminiProvider,
}


def get_provider(config: Settings | None = None) -> AIProvider | None:
    """The configured provider, or `None` if AI is not configured.

    Every AI route calls this rather than reaching for `settings` directly, so
    tests can monkeypatch exactly one function to simulate "no key configured"
    or "provider misconfigured" without touching environment variables.
    """
    config = config or settings
    if not config.ai_configured:
        return None

    provider_cls = _PROVIDERS.get(config.ai_provider.lower())
    if provider_cls is None:
        logger.warning("ai provider misconfigured", extra={"provider": config.ai_provider})
        return None

    return provider_cls(
        api_key=config.ai_api_key,
        model=config.ai_model,
        base_url=config.ai_base_url,
        timeout=config.ai_request_timeout_seconds,
    )
