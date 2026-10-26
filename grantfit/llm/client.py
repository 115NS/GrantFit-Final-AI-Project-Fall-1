"""LLM clients: a live Portkey client (OpenAI-compatible Responses API) and an offline fake.

The live client never logs or returns the API key. Usage fields are copied exactly as the
provider returns them; missing fields stay None (never invented).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from grantfit.config import get_api_key, load_yaml


class LLMError(RuntimeError):
    """Sanitized LLM/gateway failure (message never contains secrets)."""


class MissingAPIKeyError(LLMError):
    pass


class ContentFilterError(LLMError):
    """The provider's content filter refused the request (e.g. Azure prompt shields)."""


def _is_content_filter(exc: Exception) -> bool:
    return "content_filter" in str(exc) or "content management policy" in str(exc)


@dataclass
class LLMResult:
    text: str
    model_requested: str
    model_served: str | None
    is_fake: bool
    output_mode: str                      # "json_schema_strict" | "json_prompt_fallback" | "fake"
    latency_ms: float
    input_tokens: int | None = None
    cached_input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_tokens: int | None = None
    provider: str | None = None
    gateway_trace_id: str | None = None
    gateway_cache_status: str | None = None   # Portkey x-portkey-cache-status (HIT = replayed response)
    notes: list[str] = field(default_factory=list)


class LLMClient(Protocol):
    model: str

    def complete_json(
        self, *, system: str, user: str, schema_name: str, schema: dict[str, Any]
    ) -> LLMResult: ...


# ---------------------------------------------------------------------------
# Strict JSON schema helper
# ---------------------------------------------------------------------------


def to_strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Make a Pydantic JSON schema acceptable to strict structured outputs:
    every object closed (additionalProperties: false) with all properties required."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            node = {k: walk(v) for k, v in node.items() if k not in ("title", "default")}
            if node.get("type") == "object" and "properties" in node:
                node["additionalProperties"] = False
                node["required"] = list(node["properties"].keys())
            return node
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


def _sanitize_error(exc: Exception, secret: str | None) -> str:
    status = getattr(exc, "status_code", None)
    msg = str(exc)
    if secret:
        msg = msg.replace(secret, "[REDACTED]")
    msg = msg[:500]
    return f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "") + f": {msg}"


# ---------------------------------------------------------------------------
# Live client
# ---------------------------------------------------------------------------


class PortkeyClient:
    """gpt-6-luna via the Portkey gateway using the OpenAI SDK's Responses API."""

    def __init__(self, model: str | None = None, settings: dict | None = None, fresh: bool = False):
        settings = settings or load_yaml("settings")["llm"]
        self.model = model or settings["model"]
        self._max_output_tokens = settings.get("max_output_tokens")
        key = get_api_key()
        if not key:
            raise MissingAPIKeyError(
                "PORTKEY_API_KEY not found. Copy .env.example to .env and set it "
                "(or run with --fake for offline mode)."
            )
        from openai import OpenAI  # imported lazily so offline tests don't need network config

        self._secret = key
        self._client = OpenAI(
            api_key=key,
            base_url=settings["base_url"],
            timeout=settings.get("timeout_seconds", 60),
            max_retries=1,
        )
        self._strict_supported = True
        # fresh=True asks Portkey to bypass its response cache so usage reflects a real model call.
        self._extra_headers = {"x-portkey-cache-force-refresh": "true"} if fresh else None

    def _call(self, system: str, user: str, text_format: dict | None) -> tuple[Any, Any, float]:
        kwargs: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": user,
        }
        if self._max_output_tokens:
            kwargs["max_output_tokens"] = self._max_output_tokens
        if text_format:
            kwargs["text"] = {"format": text_format}
        if self._extra_headers:
            kwargs["extra_headers"] = self._extra_headers
        t0 = time.perf_counter()
        raw = self._client.responses.with_raw_response.create(**kwargs)
        latency = (time.perf_counter() - t0) * 1000
        return raw.parse(), raw.headers, latency

    def complete_json(
        self, *, system: str, user: str, schema_name: str, schema: dict[str, Any]
    ) -> LLMResult:
        from openai import BadRequestError, OpenAIError

        notes: list[str] = []
        mode = "json_schema_strict"
        try:
            if self._strict_supported:
                fmt = {"type": "json_schema", "name": schema_name, "strict": True,
                       "schema": to_strict_schema(schema)}
                try:
                    resp, headers, latency = self._call(system, user, fmt)
                except BadRequestError as exc:
                    if _is_content_filter(exc):
                        # The provider refused the input; a schema fallback would be refused too.
                        raise ContentFilterError(_sanitize_error(exc, self._secret)) from None
                    # Gateway/model rejected strict schema: fall back to prompt-level JSON.
                    self._strict_supported = False
                    notes.append("strict schema rejected: " + _sanitize_error(exc, self._secret))
                    resp = None
            else:
                resp = None
            if resp is None:
                mode = "json_prompt_fallback"
                fallback_system = (
                    system + "\n\nReturn ONLY a single JSON object matching this JSON schema, "
                    "with no prose or code fences:\n" + json.dumps(schema)
                )
                resp, headers, latency = self._call(fallback_system, user, None)
        except OpenAIError as exc:
            raise LLMError(_sanitize_error(exc, self._secret)) from None

        usage = getattr(resp, "usage", None)
        in_details = getattr(usage, "input_tokens_details", None) if usage else None
        out_details = getattr(usage, "output_tokens_details", None) if usage else None
        if usage is None:
            notes.append("provider returned no usage fields")
        return LLMResult(
            text=resp.output_text or "",
            model_requested=self.model,
            model_served=getattr(resp, "model", None),
            is_fake=False,
            output_mode=mode,
            latency_ms=round(latency, 1),
            input_tokens=getattr(usage, "input_tokens", None),
            cached_input_tokens=getattr(in_details, "cached_tokens", None),
            output_tokens=getattr(usage, "output_tokens", None),
            reasoning_tokens=getattr(out_details, "reasoning_tokens", None),
            provider=headers.get("x-portkey-provider"),
            gateway_trace_id=headers.get("x-portkey-trace-id"),
            gateway_cache_status=headers.get("x-portkey-cache-status"),
            notes=notes,
        )


# ---------------------------------------------------------------------------
# Offline fake client (tests / --fake). Output is always labeled fake; usage stays None.
# ---------------------------------------------------------------------------


class FakeLLMClient:
    model = "fake-llm"

    def __init__(self, responses: list[str | dict | Exception] | Callable[[str, str], str | dict]):
        self._responses = responses
        self.calls: list[dict[str, str]] = []

    def complete_json(
        self, *, system: str, user: str, schema_name: str, schema: dict[str, Any]
    ) -> LLMResult:
        self.calls.append({"system": system, "user": user, "schema_name": schema_name})
        if callable(self._responses):
            item = self._responses(system, user)
        else:
            if not self._responses:
                raise LLMError("FakeLLMClient: no scripted responses left")
            item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise LLMError(f"FakeLLMClient scripted failure: {item}")
        text = item if isinstance(item, str) else json.dumps(item)
        return LLMResult(
            text=text, model_requested=self.model, model_served=self.model,
            is_fake=True, output_mode="fake", latency_ms=0.0,
        )
