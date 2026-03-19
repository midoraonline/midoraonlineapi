from __future__ import annotations

from typing import Any


def research_query(
    query: str,
    max_points: int = 6,
) -> dict[str, Any]:
    """
    Do "research-style" summarization for the agent.

    Note: This runs on the model's knowledge (it does not require a separate
    web search API). Use it for non-Shop-specific questions, general best
    practices, or when the user wants a structured answer.
    """
    # Local import + local client init to avoid circular imports (this module
    # is imported by `ai/service.py`, which defines the Gemini helpers).
    from core.config import get_settings

    settings = get_settings()
    if not getattr(settings, "gemini_api_key", None):
        return {"ok": False, "error": "AI is not configured."}

    try:
        from google import genai  # type: ignore

        client = genai.Client(api_key=settings.gemini_api_key)
        model_id = "gemini-2.0-flash"
        system = (
            "You are a helpful research assistant for Midora Online.\n"
            "Return a structured summary with clear takeaways.\n"
            "When relevant, include:\n"
            "- What it is\n"
            "- Key points\n"
            "- Practical recommendations\n"
            "- Risks/unknowns the user should verify\n"
            f"Limit to at most {max_points} bullet-style items across the whole answer."
        )
        prompt = f"{system}\n\nResearch query:\n{query}\n\nAnswer:"
        response = client.models.generate_content(model=model_id, contents=prompt)
        text = response.text if hasattr(response, "text") else str(response)
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "text": text}

