from typing import Any

from core.config import get_settings


def get_gemini_model():
    """Lazy init Gemini model."""
    settings = get_settings()
    if not settings.gemini_api_key:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=settings.gemini_api_key)
        return genai.GenerativeModel("gemini-1.5-flash")
    except Exception:
        return None


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
    settings = get_settings()
    if not settings.gemini_api_key:
        return None
    try:
        import google.generativeai as genai
        genai.configure(api_key=settings.gemini_api_key)
        return genai.GenerativeModel("gemini-1.5-flash", system_instruction=CREATE_SHOP_SYSTEM)
    except Exception:
        return None


def chat_create_shop(messages: list[dict]) -> tuple[str, dict | None]:
    """
    Conversational shop creation: prompt user for name, type, description; when ready return (reply, suggested_shop).
    messages: list of {"sender_type": "customer"|"ai_concierge", "message": "..."} in order.
    """
    import json
    import re
    model = _get_create_shop_model()
    if not model:
        return "AI is not configured. Please create your shop manually.", None
    # Build conversation for Gemini (user turn, model turn, ...)
    history = []
    for m in messages:
        role = "user" if m.get("sender_type") == "customer" else "model"
        history.append({"role": role, "parts": [m.get("message", "")]})
    try:
        chat = model.start_chat(history=history[:-1] if len(history) > 1 else [])
        response = chat.send_message(history[-1]["parts"][0] if history else "I want to create a shop.")
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
