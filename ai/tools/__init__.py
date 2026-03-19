"""
Tool functions exposed to the Gemini agent.

These functions are intended to be passed to the `google-genai` SDK as
`tools=[...]` so the model can emit function_call parts.
"""

from .supabase_tools import ingest_shop_ai_context, pull_shop_catalog  # noqa: F401
from .research_tools import research_query  # noqa: F401

