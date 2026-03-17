from typing import Any

from core.config import get_settings


def _get_genai_client():
    """Lazy init Google GenAI client (new google.genai SDK)."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return None
    try:
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)
        return client
    except Exception:
        return None


def get_gemini_model() -> Any:
    """
    Backwards-compatible helper returning a small wrapper with a generate_content(prompt: str) method,
    so existing call sites do not need to change.
    """
    client = _get_genai_client()
    if not client:
        return None

    class _ModelWrapper:
        def __init__(self, _client):
            self._client = _client
            # Default text model for general Midora chat.
            # Using a free-tier supported model ID.
            self._model = "gemini-2.5-flash"

        def generate_content(self, prompt: str):
            return self._client.models.generate_content(model=self._model, contents=prompt)

    return _ModelWrapper(client)


def generate_product_copy_from_image(image_url: str | None = None, image_base64: str | None = None) -> dict[str, str]:
    """Merchant Copilot: suggest title, description, ai_seo_tags from image. Returns dict with title, description, ai_seo_tags."""
    model = get_gemini_model()
    if not model:
        return {"title": "", "description": "", "ai_seo_tags": ""}
    # Stub: would pass image to model and get structured output
    return {"title": "Product", "description": "", "ai_seo_tags": ""}


def chat_with_context(shop_context: str, product_summary: str | None, message: str) -> str:
    """In-shop concierge: reply using shop_ai_context and optional product RAG."""
    model = get_gemini_model()
    if not model:
        return "AI is not configured."
    prompt = f"Shop context:\n{shop_context}\n\nCustomer: {message}\n\nReply as the shop concierge briefly."
    try:
        response = model.generate_content(prompt)
        return response.text if hasattr(response, "text") else str(response)
    except Exception:
        return "Sorry, I couldn't process that."


MIDORA_INFO_SYSTEM = """You are Midora Online's product assistant.

Your only job is to explain and answer questions about:
- Midora Online / DigitalMall as a product
- what it is, who it's for, and what you can do with it
- how shops, products, orders, payments, and AI assistants work at a high level

Guidelines:
- Be friendly, clear, and concise.
- Assume the user is a potential merchant or buyer on Midora Online.
- Do NOT give legal, medical, or financial advice.
- If the user asks for something outside Midora Online, politely redirect back to what Midora is and how it works.
"""


def chat_midora_info(message: str) -> str:
    """General Midora Online info bot (not tied to any shop)."""
    model = get_gemini_model()
    if not model:
        return "AI is not configured."
    prompt = f"{MIDORA_INFO_SYSTEM}\n\nUser: {message}\n\nAssistant:"
    try:
        response = model.generate_content(prompt)
        return response.text if hasattr(response, "text") else str(response)
    except Exception as e:
        # Surface underlying error so it's easier to debug in dev
        return f"AI error: {e}"


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


def _get_create_shop_model():
    """Gemini model with create-shop system instruction."""
    client = _get_genai_client()
    if not client:
        return None
    try:
        # Use a dedicated model with system_instruction if supported
        from google import genai  # type: ignore

        # New google.genai SDK prefers configuring system_instruction via config in generate_content,
        # but we keep this factory for potential future extension. For now, we just reuse the client.
        return client
    except Exception:
        return None


def chat_create_shop(messages: list[dict]) -> tuple[str, dict | None]:
    """
    Conversational shop creation: prompt user for name, type, description; when ready return (reply, suggested_shop).
    messages: list of {"sender_type": "customer"|"ai_concierge", "message": "..."} in order.
    """
    import json
    import re
    client = _get_create_shop_model()
    if not client:
        return "AI is not configured. Please create your shop manually.", None
    # Build conversation for Gemini (user turn, model turn, ...)
    history = []
    for m in messages:
        role = "user" if m.get("sender_type") == "customer" else "model"
        history.append({"role": role, "parts": [m.get("message", "")]})
    try:
        from google.genai.types import GenerateContentConfig  # type: ignore

        prompt = history[-1]["parts"][0] if history else "I want to create a shop."
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
            config=GenerateContentConfig(
                system_instruction=[CREATE_SHOP_SYSTEM],
            ),
        )
        reply = response.text if hasattr(response, "text") else str(response)
    except Exception:
        return "Sorry, I couldn't process that. Try again or create your shop from the dashboard.", None
    # Parse JSON from reply (```json ... ``` or similar)
    suggested = None
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", reply, re.DOTALL)
    if match:
        try:
            raw = json.loads(match.group(1).strip())
            suggested = {
                "name": str(raw.get("name", "")).strip() or "My Shop",
                "slug": str(raw.get("slug", "")).strip().lower().replace(" ", "-") or "my-shop",
                "description": (raw.get("description") or "").strip() or None,
                "shop_type": raw.get("shop_type") if raw.get("shop_type") in ("product", "service", "both") else "product",
            }
        except (json.JSONDecodeError, TypeError):
            pass
    return reply, suggested


def remove_background(image_url: str | None = None, image_base64: str | None = None) -> str | None:
    """Nano Banana: remove background from image. Returns cleaned image URL or base64."""
    settings = get_settings()
    if not getattr(settings, "nano_banana_api_key", None) and not getattr(settings, "nano_banana_url", None):
        return None
    # TODO: call Nano Banana API via httpx
    return image_url


def generate_logo(shop_name: str, prompt: str | None = None, style: str | None = None) -> str | None:
    """Nano Banana: generate simple logo for shop. Returns logo URL or base64."""
    settings = get_settings()
    if not getattr(settings, "nano_banana_api_key", None):
        return None
    # TODO: call Nano Banana API
    return None
