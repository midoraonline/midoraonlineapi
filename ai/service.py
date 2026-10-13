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

from core.categories import categories_for_prompt, normalize_category
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
    # Product listing BG removal runs in the browser via @imgly/background-removal.
    # This API endpoint is intentionally unimplemented (no remove.bg / Photoroom key).
    raise AIUnavailableError(
        "Server-side background removal is not configured. "
        "Use the Midora web app Auto-remove background checkbox "
        "(client-side @imgly/background-removal)."
    )


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

MIDORA_INFO_SYSTEM = """You are Ask Midora, the help assistant for Midora Online, a marketplace
for Uganda. Prices are in Ugandan shillings (UGX).

WHAT MIDORA IS
- People post listings of three kinds: products (new or used goods),
  services (repairs, cleaning, salons, tutoring...) and opportunities
  (jobs, gigs, internships, tenders). Each listing has a category, price in
  UGX, location (or Online), photos and category-specific details.
- Posting is free and needs no shop: use Post (or the "Get noticed" banner
  on the home feed). A listing goes live after automatic checks.
- Shops are optional storefronts that group a seller's listings, with a
  logo, banner, about text, hours and contacts. Open one from "Open shop";
  there is an AI assistant that drafts the shop details.
- Plans (monthly): Basic is free (1 shop, 10 listings per shop, no
  analytics). Standard is UGX 30,000 (3 shops, 20 listings per shop, shop
  analytics). Premium is UGX 80,000 (10 shops, 50 listings per shop,
  analytics). Analytics show impressions, listing views, WhatsApp clicks
  and messages.
- Verification: to post, verify your phone with a one-time code (OTP) or
  verify your email. Listing more than 5 items, or upgrading to a paid
  plan, also needs Identity Verified (ID and selfie, reviewed by Midora).
- Buyers browse the feed and search, and filter by kind, category, price,
  location or near me, availability, verified sellers and rating. They can
  rate and review listings and sellers.
- Contact is by WhatsApp or in-app messages. Before WhatsApp opens, Midora
  shows safety tips: verify the seller before paying, never send money
  upfront, and report anything suspicious. Meeting in a public place is wise.
  Midora does not hold payments between buyers and sellers.

HOW TO ANSWER
- Be friendly, brief and practical. Use short steps for how-to questions.
- Use Ugandan context: local towns, UGX amounts, mobile money and boda
  delivery where relevant.
- Only describe features listed above. If you are not sure something exists,
  say so and suggest checking Settings or contacting Midora support. Never
  invent prices, limits or features.
- No legal, medical or financial advice. Politely steer off-topic questions
  back to Midora.
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
# Listing quality check (pre-submit)
# ---------------------------------------------------------------------------

_LISTING_QUALITY_PROMPT = """You are a strict e-commerce listing coach on
Midora, a Ugandan marketplace. Given a listing's title, description, up to
4 product images, and the marketplace's category tree, judge whether the
listing is ready to publish and suggest concrete improvements.

Return ONLY a JSON object, no prose, no code fences:
{
  "score": 0-100,               // overall listing quality score
  "title_matches": bool,        // title accurately describes the item shown
  "description_quality": "poor" | "fair" | "good",
  "images_match": bool,         // images show the item mentioned in title
  "feedback": "one short user-facing sentence in plain English",
  "suggestions": ["specific fix 1", "specific fix 2"],  // 0-3 items, actionable
  "suggested_title": "a better title, or null if the current one is already good",
  "suggested_description": "a better 2-4 sentence description, or null if already good",
  "suggested_category": "one label from the category tree, or null if unsure",
  "suggested_subcategory": "one subcategory label under suggested_category, or null"
}

Rules:
- If title/description/images clearly refer to different things, set
  title_matches or images_match to false and score < 40.
- "poor" description = under two sentences, generic ("nice item"), missing
  size/condition/what's included, or just repeats the title.
- "fair" = accurate but thin (buyer will need to ask questions).
- "good" = at least two clear sentences, covers condition/what's included/
  who it's for, and reads like the seller wrote it.
- The listing comes with "Structured details": values the seller already
  entered in dedicated form fields (price, location, condition, category
  fields such as brand, size or deadline). Treat them as part of the listing.
  NEVER suggest adding to the description, title or a suggestion anything a
  structured detail already captures, and do not judge the description
  "poor" for leaving those out. A field marked "(empty)" is one the seller
  skipped: if it matters to buyers, suggest filling that field by its label
  ("Fill in Brand"), not writing it into the description.
- Focus suggestions on what is genuinely missing or weak: unclear or
  misleading title, photos that don't show the item, missing selling points,
  what's included, defects, warranty, delivery or pickup, who it suits, or
  for services and jobs: scope, requirements, schedule, how to apply.
- Prices are in UGX. Only comment on price if it looks like a typo.
- Suggestions must be concrete ("Mention the charger is included"), not vague.
- Keep feedback friendly and under 20 words.
- suggested_title / suggested_description are OPTIONAL rewrites the
  merchant can accept or ignore — only propose one when it is a real
  improvement over what they wrote, never a trivial reword.
- suggested_category MUST be copied verbatim from the category tree's
  parent labels. suggested_subcategory MUST be copied verbatim from the
  children listed under that exact parent. Never invent a category.
- If the listing already has a correct category, still return it in
  suggested_category/suggested_subcategory so the UI can confirm it.
"""


def format_listing_details(details: dict[str, str] | None) -> str:
    lines: list[str] = []
    for label, value in (details or {}).items():
        name = str(label).strip()[:60]
        if not name:
            continue
        text = str(value or "").strip()[:200]
        lines.append(f"- {name}: {text or '(empty)'}")
        if len(lines) >= 40:
            break
    return "\n".join(lines) or "- none"


async def check_listing_quality(
    title: str,
    description: str,
    image_urls: list[str],
    category: str | None = None,
    subcategory: str | None = None,
    listing_kind: str | None = None,
    details: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Score a listing (title + description + images) for publish-readiness.

    Fails open on any Gemini / config error: returns {"ok": True, ...} so
    the frontend doesn't block the merchant on our internal failure.
    """
    client = _get_genai_client()
    fallback = {
        "ok": True,
        "critical": False,
        "score": 100,
        "title_matches": True,
        "description_quality": "good",
        "images_match": True,
        "feedback": "AI review unavailable — publishing without pre-check.",
        "suggestions": [],
        "suggested_title": None,
        "suggested_description": None,
        "suggested_category": None,
        "suggested_subcategory": None,
    }
    if client is None:
        return fallback

    try:
        from google.genai import types  # type: ignore
        import httpx
    except Exception as exc:
        logger.warning("google-genai / httpx import failed: %s", exc)
        return fallback

    parts: list[Any] = []
    if image_urls:
        async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as http:
            # Cap at 4 images so this fits well inside the request budget.
            for url in image_urls[:4]:
                if not url or url.startswith("data:"):
                    continue
                try:
                    resp = await http.get(url)
                    resp.raise_for_status()
                except Exception:
                    continue
                data = resp.content
                if len(data) > 4 * 1024 * 1024:
                    continue
                mime = "image/jpeg"
                lower = url.lower()
                if lower.endswith(".png") or data[:8] == b"\x89PNG\r\n\x1a\n":
                    mime = "image/png"
                elif lower.endswith(".webp"):
                    mime = "image/webp"
                parts.append(types.Part.from_bytes(data=data, mime_type=mime))

    from core.categories import category_tree_for_prompt, subcategories_for_parent

    listing_text = (
        f"Category tree (parent: subcategories):\n{category_tree_for_prompt()}\n\n"
        f"Listing type: {listing_kind or 'product'}\n"
        f"Current category: {category or 'unspecified'}"
        f"{' > ' + subcategory if subcategory else ''}\n\n"
        f"Structured details (already captured, do not ask for these in the description):\n"
        f"{format_listing_details(details)}\n\n"
        f"Title: {title.strip()}\n\n"
        f"Description:\n{(description or '').strip()}"
    )
    parts.append(types.Part.from_text(text=listing_text))

    try:
        resp = await client.aio.models.generate_content(
            model=_get_model(),
            contents=parts,
            config=types.GenerateContentConfig(
                system_instruction=[_LISTING_QUALITY_PROMPT],
                temperature=0.0,
                response_mime_type="application/json",
            ),
        )
    except Exception as exc:
        _raise_if_rate_limited(exc)
        logger.warning("check_listing_quality: gemini call failed: %s", exc)
        return fallback

    text = _response_text(resp).strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        data = json.loads(text)
    except Exception:
        logger.warning("check_listing_quality: bad JSON: %r", text[:200])
        return fallback

    score = int(data.get("score", 100))
    title_matches = bool(data.get("title_matches", True))
    images_match = bool(data.get("images_match", True))
    quality = str(data.get("description_quality", "good")).lower()
    if quality not in {"poor", "fair", "good"}:
        quality = "fair"
    suggestions_raw = data.get("suggestions") or []
    suggestions = [str(s).strip() for s in suggestions_raw if str(s).strip()][:3]
    feedback = str(data.get("feedback") or "").strip()
    if not feedback:
        feedback = (
            "Looks ready to publish."
            if title_matches and images_match and quality != "poor"
            else "This listing needs a bit more work before it's ready."
        )

    def _clean_text_suggestion(raw: object, original: str) -> str | None:
        text = str(raw or "").strip()
        if not text or text.lower() == "null":
            return None
        # Don't surface a "suggestion" that's identical to what's already there.
        if text.strip().lower() == original.strip().lower():
            return None
        return text

    suggested_title = _clean_text_suggestion(data.get("suggested_title"), title)
    suggested_description = _clean_text_suggestion(
        data.get("suggested_description"), description
    )

    # Validate the AI's category pick against the real tree — never trust
    # a hallucinated label past this endpoint.
    from core.categories import normalize_category

    suggested_category: str | None = None
    suggested_subcategory: str | None = None
    raw_category = str(data.get("suggested_category") or "").strip()
    if raw_category and raw_category.lower() != "null":
        try:
            suggested_category = normalize_category(raw_category)
        except ValueError:
            suggested_category = None
    if suggested_category:
        valid_children = subcategories_for_parent(suggested_category)
        raw_sub = str(data.get("suggested_subcategory") or "").strip()
        if raw_sub and raw_sub.lower() != "null" and raw_sub in valid_children:
            suggested_subcategory = raw_sub

    # Coach signal (ok) vs hard wall (critical). Phase 1 Should: only critically
    # low quality blocks publish — everything else is Improve / coach.
    ok = (
        score >= 60
        and title_matches
        and images_match
        and quality != "poor"
    )
    critical = (
        score < 25
        or (not title_matches and not images_match and score < 40)
    )

    return {
        "ok": ok,
        "critical": critical,
        "score": score,
        "title_matches": title_matches,
        "description_quality": quality,
        "images_match": images_match,
        "feedback": feedback,
        "suggestions": suggestions,
        "suggested_title": suggested_title,
        "suggested_description": suggested_description,
        "suggested_category": suggested_category,
        "suggested_subcategory": suggested_subcategory,
    }


# ---------------------------------------------------------------------------
# Shop creation wizard
# ---------------------------------------------------------------------------


def _normalize_ai_category(value: object) -> str | None:
    raw = (str(value) if value is not None else "").strip()
    if not raw:
        return None
    try:
        return normalize_category(raw)
    except ValueError:
        return None


_CREATE_SHOP_CATEGORIES = categories_for_prompt()

CREATE_SHOP_SYSTEM = f"""You are Midora's shop-setup assistant. Midora is a Ugandan marketplace
where people list products, services, and opportunities (jobs, gigs,
tenders). Anyone can post without a shop; a shop is an optional storefront
that groups listings and unlocks analytics on paid plans. Prices are in UGX.

Help the user set up a strong shop in as few messages as possible.

WHAT TO LEARN (2-3 short questions per message, skip anything already known):
- What they offer, across products, services and opportunities, with a few
  concrete examples and a rough price range in UGX.
- Who buys from them (students, families, offices, farmers, hotels...).
- Where they operate: area and town (e.g. Ntinda, Kampala), and whether they
  deliver, do pickup, work on site, or are online only. Areas they serve.
- When they are open or reachable.
- The shop name, if they have one. Offer 2 options if they don't.

CONTEXT YOU ALREADY HAVE:
The first message may include a "Known context" block with the user's
verified contacts and their existing listings. Use it. Never ask for an
email or phone number that is marked verified: it will be reused on the
shop. Infer the category, shop type and price range from existing listings
before asking.

SUGGESTIONS MUST BE SPECIFIC:
- Write for Ugandan buyers. Use local place names, UGX amounts written like
  "UGX 25,000", and payment and delivery habits that fit (mobile money,
  boda delivery, pickup points) only when they match what the user said.
- Never invent facts: no made-up prices, awards, years in business or
  guarantees. If unsure, leave the field empty.
- description: one line, at most 160 characters, saying what and where.
- about: 2-4 sentences: what they offer, who it is for, where and how they
  deliver or serve, and why buy from them.

CATEGORY must be exactly one of: {_CREATE_SHOP_CATEGORIES}
shop_type is "product", "service", or "both" (use "both" when they also post
opportunities alongside goods or services).

When you know at least the name, what they offer and the location, reply
with one short sentence and then this JSON block, with nothing after it:
```json
{{
  "name": "...",
  "slug": "...",
  "description": "...",
  "about": "...",
  "shop_type": "product",
  "category": "...",
  "location": "...",
  "availability": "...",
  "shop_email": "...",
  "whatsapp_number": "...",
  "target_customers": "...",
  "service_areas": "...",
  "price_range": "...",
  "listing_ideas": ["...", "..."],
  "tips": ["...", "..."]
}}
```

Field rules:
- slug: lowercase letters, digits and hyphens (e.g. "ntinda-phone-hub").
- availability: plain hours text, e.g. "Mon-Sat 8am-7pm".
- price_range: e.g. "UGX 15,000 - 120,000", or "" if unknown.
- listing_ideas: up to 4 first listings to post, each a short title.
- tips: up to 3 concrete setup tips for this shop (photos, pricing,
  service area, safe meet-ups), no generic advice.
- Use "" for unknown text fields, [] for unknown lists, but keep every key.
- You may send an updated JSON block when the user adds information."""


def _clean_list(raw: object, limit: int) -> list[str]:
    if not isinstance(raw, list):
        return []
    out = [str(item).strip() for item in raw if str(item or "").strip()]
    return [item[:140] for item in out][:limit]


def format_create_shop_context(context: dict | None) -> str:
    """Known facts about the user, prepended to the conversation."""
    if not context:
        return ""
    lines: list[str] = []
    if context.get("full_name"):
        lines.append(f"Name: {context['full_name']}")
    if context.get("email"):
        state = "verified" if context.get("email_verified") else "not verified"
        lines.append(f"Email: {context['email']} ({state})")
    if context.get("phone"):
        state = "verified" if context.get("phone_verified") else "not verified"
        lines.append(f"Phone / WhatsApp: {context['phone']} ({state})")
    listings = context.get("listings") or []
    if listings:
        lines.append("Existing listings:")
        for item in listings[:15]:
            parts = [str(item.get("item_type") or "product"), str(item.get("title") or "").strip()]
            if item.get("category"):
                parts.append(str(item["category"]))
            if item.get("price_ugx"):
                parts.append(f"UGX {int(item['price_ugx']):,}")
            if item.get("location_name"):
                parts.append(str(item["location_name"]))
            lines.append("- " + " | ".join(p for p in parts if p))
    if not lines:
        return ""
    return "Known context (from the user's account, do not ask again):\n" + "\n".join(lines)


async def chat_create_shop(
    messages: list[dict], context: dict | None = None
) -> tuple[str, dict | None]:
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
        known = format_create_shop_context(context)
        if known:
            contents.insert(
                0, types.Content(role="user", parts=[types.Part.from_text(text=known)])
            )

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
                "about": (raw.get("about") or "").strip() or None,
                "shop_type": (
                    raw.get("shop_type")
                    if raw.get("shop_type") in ("product", "service", "both")
                    else "product"
                ),
                "shop_email": (raw.get("shop_email") or "").strip() or None,
                "whatsapp_number": (raw.get("whatsapp_number") or "").strip() or None,
                "location": (raw.get("location") or "").strip() or None,
                "category": _normalize_ai_category(raw.get("category")),
                "availability": (raw.get("availability") or "").strip() or None,
                "target_customers": str(raw.get("target_customers") or "").strip() or None,
                "service_areas": str(raw.get("service_areas") or "").strip() or None,
                "price_range": str(raw.get("price_range") or "").strip() or None,
                "listing_ideas": _clean_list(raw.get("listing_ideas"), 4),
                "tips": _clean_list(raw.get("tips"), 3),
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
