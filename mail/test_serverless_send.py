import asyncio

from mail import queue


def test_inline_send_only_on_serverless(monkeypatch):
    calls = []

    async def fake_drain(limit):
        calls.append(limit)
        return {"sent": limit, "failed": 0}

    monkeypatch.setattr(queue, "drain_mail_queue", fake_drain)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    asyncio.run(queue._send_now_if_serverless(3))
    assert calls == []
    monkeypatch.setenv("VERCEL", "1")
    asyncio.run(queue._send_now_if_serverless(12))
    assert calls == [queue.INLINE_SEND_LIMIT]


def test_drain_stops_when_queue_empty(monkeypatch):
    monkeypatch.setattr(queue, "_claim_next", lambda: None)
    assert asyncio.run(queue.drain_mail_queue(10)) == {"sent": 0, "failed": 0}
