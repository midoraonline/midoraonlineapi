import asyncio
from types import SimpleNamespace

from ai import service
from ai.tools.supabase_tools import _format_ugx


def test_format_ugx():
    assert _format_ugx(350000.0) == "UGX 350,000"
    assert _format_ugx("5000") == "UGX 5,000"
    assert _format_ugx(0) == "price on request"
    assert _format_ugx(None) == "price on request"


def _fake_client(replies):
    calls = []

    async def generate_content(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(text=replies.pop(0), function_calls=None, candidates=[])

    models = SimpleNamespace(generate_content=generate_content)
    return SimpleNamespace(aio=SimpleNamespace(models=models)), calls


def _run(monkeypatch, replies):
    client, calls = _fake_client(replies)
    monkeypatch.setattr(service, "_get_genai_client", lambda: client)
    monkeypatch.setattr(service, "_get_cached_catalog", lambda shop_id: "Live listings (1):\n- Table")
    reply = asyncio.run(
        service.chat_with_shop_tools("s1", "", [{"sender_type": "customer", "message": "Can the seller call me?"}])
    )
    return reply, calls


def test_empty_reply_retries_then_answers(monkeypatch):
    reply, calls = _run(monkeypatch, [None, "Tap Message seller to reach them."])
    assert reply == "Tap Message seller to reach them."
    assert len(calls) == 2


def test_empty_reply_never_blank(monkeypatch):
    reply, _ = _run(monkeypatch, [None, ""])
    assert reply == service._SHOP_FALLBACK
