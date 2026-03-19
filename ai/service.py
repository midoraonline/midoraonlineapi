"""
AI service layer for Midora Online.

All public functions are async so they don't block FastAPI's event loop.
Gemini calls use client.aio (async) and blocking Supabase tool calls are
dispatched via asyncio.to_thread so the event loop is never stalled.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from core.config import get_settings
from ai.tools import ingest_shop_ai_context, pull_shop_catalog

# ---------------------------------------------------------------------------
# Catalog TTL cache
# ---------------------------------------------------------------------------
# Avoids a Supabase round-trip on every single message for the same shop.
# Each entry: (catalog_text, fetched_at_timestamp)
_CATALOG_CACHE: dict[str, tuple[str, float]] = {}
_CATALOG_TTL_SECONDS = 300  # 5 minutes


def _get_cached_catalog(shop_id: str) -> str | None:
    entry = _CATALOG_CACHE.get(shop_id)
    if entry and (time.monotonic() - entry[1]) < _CATALOG_TTL_SECONDS:
        return entry[0]
    return None


def _set_cached_catalog(shop_id: str, text: str) -> None:
    _CATALOG_CACHE[shop_id] = (text, time.monotonic())

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions
# ---------------------------------------------------------------------------

class AIRateLimitError(Exception):
    """Raised when the Gemini API returns a 429 RESOURCE_EXHAUSTED response."""

    def __init__(self, retry_after: int = 60) -> None:
        self.retry_after = retry_after
        super().__init__(f"AI rate-limited; retry after {retry_after}s")


class AIUnavailableError(Exception):
    """Raised when the AI client is not configured."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get_genai_client():
    """Return a google-genai Client, or None if not configured."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return None
    try:
        from google import genai  # type: ignore
        return genai.Client(api_key=settings.gemini_api_key)
    except Exception:
        return None


def _get_model() -> str:
    """Return the configured Gemini model ID (overridable via GEMINI_MODEL env var)."""
    return get_settings().gemini_model


def _parse_retry_after(exc: Exception) -> int:
    """Extract the retryDelay seconds from a Gemini 429 error string."""
    match = re.search(r"retryDelay['\"]?\s*:\s*['\"]?(\d+)", str(exc))
    if match:
        return int(match.group(1))
    return 60


def _raise_if_rate_limited(exc: Exception) -> None:
    """Re-raise exc as AIRateLimitError when it is a 429, otherwise do nothing."""
    err = str(exc)
    if "429" in err or "RESOURCE_EXHAUSTED" in err:
        raise AIRateLimitError(retry_after=_parse_retry_after(exc)) from exc


def _extract_function_calls(model_response: Any) -> list[tuple[str, dict]]:
    """Return list of (function_name, args_dict) from a Gemini response."""
    parsed: list[tuple[str, dict]] = []

    # Primary path: response.function_calls is a list of FunctionCall objects
    fcs = list(getattr(model_response, "function_calls", None) or [])
    for fc in fcs:
        name = getattr(fc, "name", None)
        args = getattr(fc, "args", None)
        if name and isinstance(args, dict):
            parsed.append((str(name), args))
    if parsed:
        return parsed

    # Fallback: scan candidates[0].content.parts
    candidates = getattr(model_response, "candidates", None) or []
    if not candidates:
        return parsed
    content = getattr(candidates[0], "content", None)
    parts = getattr(content, "parts", None) or []
    for part in parts:
        fc_part = getattr(part, "function_call", None)
        if not fc_part:
            continue
        if isinstance(fc_part, dict):
            name, args = fc_part.get("name"), fc_part.get("args")
        else:
            name = getattr(fc_part, "name", None)
            args = getattr(fc_part, "args", None)
        if name and isinstance(args, dict):
            parsed.append((str(name), args))
    return parsed


def _response_text(resp: Any) -> str:
    return resp.text if hasattr(resp, "text") else str(resp)


# ---------------------------------------------------------------------------
# Backwards-compatible sync wrapper (used by legacy call sites)
# ---------------------------------------------------------------------------

def get_gemini_model() -> Any:
    """Sync wrapper kept for legacy call sites (generate_product_copy_from_image etc.)."""
    client = _get_genai_client()
    if not client:
        return None

    model_id = _get_model()

    class _ModelWrapper:
        def __init__(self, _client: Any, _model: str) -> None:
            self._client = _client
            self._model = _model

        def generate_content(self, prompt: str) -> Any:
            return self._client.models.generate_content(model=self._model, contents=prompt)

    return _ModelWrapper(client, model_id)


# ---------------------------------------------------------------------------
# Stub helpers (not yet implemented)
# ---------------------------------------------------------------------------

def generate_product_copy_from_image(
    image_url: str | None = None, image_base64: str | None = None
) -> dict[str, str]:
    return {"title": "", "description": "", "ai_seo_tags": ""}


def remove_background(
    image_url: str | None = None, image_base64: str | None = None
) -> str | None:
    return image_url


def generate_logo(
    shop_name: str, prompt: str | None = None, style: str | None = None
) -> str | None:
    return None


# ---------------------------------------------------------------------------
# Shop concierge
# ---------------------------------------------------------------------------

SHOP_SYSTEM = """You are the in-shop AI concierge for a Midora Online store.

The shop's catalog and contact details are already provided in this system prompt.
Answer the customer's question using that information.

Guidelines:
- Be friendly, concise, and accurate.
- Never fabricate product prices, stock levels, or contact details.
- If a product is not in the catalog, say it is not listed rather than guessing.
- If the catalog section below is empty, tell the customer you are unable to
  retrieve product details right now and suggest they try again shortly.
"""


async def chat_with_shop_tools(
    shop_id: str,
    shop_context: str,
    messages: list[dict],
) -> str:
    """
    Async in-shop concierge.

    Architecture:
    1. Pre-fetch catalog from Supabase (via asyncio.to_thread — non-blocking).
    2. Embed catalog + stored context directly in the system prompt.
    3. Send one async Gemini call (client.aio) — covers 95% of questions.
    4. If the model still requests a tool, execute it and do one more async call.
    5. Raise AIRateLimitError on 429 so the route can return HTTP 503.
    """
    try:
        from google.genai import types  # type: ignore
    except Exception:
        raise AIUnavailableError("google-genai package not installed.")

    client = _get_genai_client()
    if not client:
        raise AIUnavailableError("Gemini API key not configured.")

    model = _get_model()

    # 1. Pre-fetch catalog — check the TTL cache first, then Supabase.
    #    This avoids a Supabase round-trip on every message for the same shop.
    catalog_text = _get_cached_catalog(shop_id) or ""
    if not catalog_text:
        try:
            catalog = await asyncio.to_thread(
                pull_shop_catalog, shop_id=shop_id, max_products=40
            )
            catalog_text = catalog.get("text", "") if catalog.get("ok") else ""
            if catalog_text:
                _set_cached_catalog(shop_id, catalog_text)
        except Exception:
            catalog_text = ""

    # 2. Build system prompt with all available context already embedded
    system_parts: list[str] = [SHOP_SYSTEM]
    if shop_context.strip():
        system_parts.append(f"\n\n--- Stored shop context ---\n{shop_context.strip()}")
    if catalog_text.strip():
        system_parts.append(f"\n\n--- Live catalog ---\n{catalog_text.strip()}")
    system = "".join(system_parts)

    # 3. Build proper multi-turn conversation history
    contents: list[Any] = []
    for m in messages:
        role = "user" if (m.get("sender_type") or "customer") == "customer" else "model"
        text = (m.get("message") or "").strip()
        if text:
            contents.append(
                types.Content(role=role, parts=[types.Part.from_text(text=text)])
            )

    if not contents:
        return "Please send a message."

    # 4. KEY: only expose tools when the catalog is NOT already embedded.
    #
    #    When catalog_text is present the model already has everything it needs
    #    inside the system prompt. Declaring tools as well causes the model to
    #    call pull_shop_catalog anyway (it's available, so it uses it), which
    #    creates a second Gemini API call on almost every message and burns
    #    quota twice as fast. When we have the data → no tools → 1 API call.
    #
    #    Tools are only provided as a fallback when the catalog fetch failed
    #    (empty shop, Supabase down, etc.) so the model can try to retrieve
    #    data itself. Even then, at most one extra call is made.
    catalog_is_embedded = bool(catalog_text.strip())
    tool_registry = {
        "pull_shop_catalog": pull_shop_catalog,
        "ingest_shop_ai_context": ingest_shop_ai_context,
    }

    if catalog_is_embedded:
        gen_config = types.GenerateContentConfig(
            system_instruction=[system],
        )
    else:
        gen_config = types.GenerateContentConfig(
            system_instruction=[system],
            tools=list(tool_registry.values()),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                disable=True
            ),
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(mode="AUTO")
            ),
        )

    logger.info(
        "chat_with_shop_tools shop=%s model=%s catalog_embedded=%s",
        shop_id,
        model,
        catalog_is_embedded,
    )

    try:
        # 5. Primary async Gemini call — exactly 1 API call when catalog is embedded
        resp = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=gen_config,
        )

        function_calls = _extract_function_calls(resp)
        if function_calls:
            logger.info(
                "chat_with_shop_tools shop=%s unexpected tool_calls=%s (catalog was %s)",
                shop_id,
                [n for n, _ in function_calls],
                "embedded" if catalog_is_embedded else "missing",
            )

        if not function_calls:
            return _response_text(resp)

        # 6. Tool fallback — only reached when catalog was missing.
        #    Execute tools off the event loop, then one final call for the answer.
        if hasattr(resp, "candidates") and resp.candidates:
            contents.append(resp.candidates[0].content)

        for fn_name, fn_args in function_calls:
            fn = tool_registry.get(fn_name)
            try:
                if fn:
                    result = await asyncio.to_thread(fn, **(fn_args or {}))
                    # Cache fresh catalog data if pull_shop_catalog succeeded
                    if fn_name == "pull_shop_catalog" and result.get("ok"):
                        _set_cached_catalog(shop_id, result.get("text", ""))
                else:
                    result = {"ok": False, "error": f"Unknown tool: {fn_name}"}
            except Exception as e:
                logger.exception("Tool execution failed tool=%s", fn_name)
                result = {"ok": False, "error": str(e)}

            contents.append(
                types.Content(
                    role="tool",
                    parts=[
                        types.Part.from_function_response(
                            name=fn_name, response=result
                        )
                    ],
                )
            )

        # Use tool-config-free config for the final answer call
        final_config = types.GenerateContentConfig(system_instruction=[system])
        final_resp = await client.aio.models.generate_content(
            model=model,
            contents=contents,
            config=final_config,
        )
        return _response_text(final_resp)

    except AIRateLimitError:
        raise
    except Exception as exc:
        _raise_if_rate_limited(exc)
        logger.exception("chat_with_shop_tools failed shop=%s", shop_id)
        return "Sorry, I couldn't process that."


# ---------------------------------------------------------------------------
# Midora info bot
# ---------------------------------------------------------------------------

MIDORA_INFO_SYSTEM = """You are Midora Online's product assistant.

Your only job is to explain and answer questions about:
- Midora Online / DigitalMall as a product
- what it is, who it's for, and what you can do with it
- how shops, products, orders, payments, and AI assistants work at a high level

Guidelines:
- Be friendly, clear, and concise.
- Assume the user is a potential merchant or buyer on Midora Online.
- Do NOT give legal, medical, or financial advice.
- If the user asks for something outside Midora Online, politely redirect back.
"""


async def chat_midora_info(message: str) -> str:
    """Async Midora Online info bot (no shop context)."""
    client = _get_genai_client()
    if not client:
        raise AIUnavailableError("Gemini API key not configured.")

    try:
        from google.genai import types  # type: ignore

        resp = await client.aio.models.generate_content(
            model=_get_model(),
            contents=[
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text=message)],
                )
            ],
            config=types.GenerateContentConfig(
                system_instruction=[MIDORA_INFO_SYSTEM],
            ),
        )
        return _response_text(resp)
    except AIRateLimitError:
        raise
    except Exception as exc:
        _raise_if_rate_limited(exc)
        logger.exception("chat_midora_info failed")
        return "Sorry, I couldn't process that."


# ---------------------------------------------------------------------------
# Shop creation wizard
# ---------------------------------------------------------------------------

CREATE_SHOP_SYSTEM = """You are helping a user create their online shop on the mall. Be friendly and concise.

Ask for (one at a time or together):
1) Business name
2) Type of business: "product" (sells physical/digital goods), "service" (sells services), or "both"
3) A short description of what they sell or offer

When the user has given enough information (at least name and type), reply with:
- A brief confirmation message
- Then a JSON code block (markdown) with exactly: name, slug (lowercase, hyphens, no spaces), description, shop_type ("product"|"service"|"both"). Use this format:
```json
{"name": "...", "slug": "...", "description": "...", "shop_type": "product"}
```
If they didn't give a description, suggest one sentence. Slug must be unique-friendly (e.g. "my-coffee-shop").
Optionally mention they can add a logo later or we can generate one.
Do not output anything else after the JSON block. If you already output a JSON block in this conversation, you may output it again with any updates."""


async def chat_create_shop(messages: list[dict]) -> tuple[str, dict | None]:
    """
    Async conversational shop creation wizard.
    Returns (reply_text, suggested_shop_dict | None).
    """
    client = _get_genai_client()
    if not client:
        raise AIUnavailableError("Gemini API key not configured.")

    try:
        from google.genai import types  # type: ignore

        contents: list[Any] = []
        for m in messages:
            role = "user" if m.get("sender_type") == "customer" else "model"
            text = (m.get("message") or "").strip()
            if text:
                contents.append(
                    types.Content(role=role, parts=[types.Part.from_text(text=text)])
                )

        if not contents:
            contents = [
                types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="I want to create a shop.")],
                )
            ]

        resp = await client.aio.models.generate_content(
            model=_get_model(),
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=[CREATE_SHOP_SYSTEM],
            ),
        )
        reply = _response_text(resp)
    except AIRateLimitError:
        raise
    except Exception as exc:
        _raise_if_rate_limited(exc)
        logger.exception("chat_create_shop failed")
        return (
            "Sorry, I couldn't process that. Try again or create your shop from the dashboard.",
            None,
        )

    # Parse JSON block from reply
    suggested: dict | None = None
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.DOTALL)
    if match:
        try:
            raw = json.loads(match.group(1).strip())
            suggested = {
                "name": str(raw.get("name", "")).strip() or "My Shop",
                "slug": (
                    str(raw.get("slug", "")).strip().lower().replace(" ", "-")
                    or "my-shop"
                ),
                "description": (raw.get("description") or "").strip() or None,
                "shop_type": (
                    raw.get("shop_type")
                    if raw.get("shop_type") in ("product", "service", "both")
                    else "product"
                ),
            }
        except (json.JSONDecodeError, TypeError):
            pass

    return reply, suggested


# ---------------------------------------------------------------------------
# Legacy sync helper kept so chat_with_context still works
# ---------------------------------------------------------------------------

def chat_with_context(shop_context: str, product_summary: str | None, message: str) -> str:
    """Sync concierge kept for backwards compatibility."""
    model = get_gemini_model()
    if not model:
        return "AI is not configured."
    prompt = (
        f"Shop context:\n{shop_context}\n\n"
        f"Customer: {message}\n\nReply as the shop concierge briefly."
    )
    try:
        response = model.generate_content(prompt)
        return _response_text(response)
    except Exception:
        return "Sorry, I couldn't process that."
