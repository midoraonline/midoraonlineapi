import asyncio

import pytest

from mail import queue, send


def test_serverless_sends_inside_the_request(monkeypatch):
    calls = []

    async def fake_send_ids(ids):
        calls.append(list(ids))
        return len(ids)

    monkeypatch.setattr(queue, "send_ids", fake_send_ids)
    monkeypatch.setenv("VERCEL", "1")
    asyncio.run(queue._dispatch(["a", "b"]))
    assert calls == [["a", "b"]]


def test_elsewhere_sends_in_the_background(monkeypatch):
    calls = []

    async def fake_send_ids(ids):
        calls.append(list(ids))
        return len(ids)

    async def run():
        await queue._dispatch(["a"])
        await asyncio.gather(*list(queue._background))

    monkeypatch.setattr(queue, "send_ids", fake_send_ids)
    monkeypatch.delenv("VERCEL", raising=False)
    monkeypatch.delenv("VERCEL_ENV", raising=False)
    asyncio.run(run())
    assert calls == [["a"]]


def test_drain_stops_when_queue_empty(monkeypatch):
    monkeypatch.setattr(queue, "_claim_next", lambda: None)
    assert asyncio.run(queue.drain_mail_queue(10)) == {"sent": 0, "failed": 0}


def test_new_mail_is_sent_by_id_not_behind_older_rows(monkeypatch):
    claimed, sent_rows = [], []
    monkeypatch.setattr(queue, "_claim_id", lambda i: claimed.append(i) or {"id": i})

    async def fake_send_and_mark(record):
        sent_rows.append(record["id"])

    monkeypatch.setattr(queue, "_send_and_mark", fake_send_and_mark)
    monkeypatch.setattr(queue, "_status_of", lambda i: "sent")
    monkeypatch.setattr(queue, "_claim_next", lambda: pytest.fail("must not take the oldest row"))
    assert asyncio.run(queue.send_ids(["new1", "new2"])) == 2
    assert claimed == sent_rows == ["new1", "new2"]


def test_a_row_someone_else_already_took_is_skipped(monkeypatch):
    monkeypatch.setattr(queue, "_claim_id", lambda i: None)
    assert asyncio.run(queue.send_ids(["x"])) == 0


def test_resending_a_code_is_never_skipped_as_duplicate(monkeypatch):
    inserted = []
    monkeypatch.setattr(queue, "_insert_rows", lambda rec, sub, body, dedupe: inserted.append(dedupe) or ["id1"])

    async def fake_dispatch(ids):
        return None

    monkeypatch.setattr(queue, "_dispatch", fake_dispatch)
    asyncio.run(queue.enqueue_mail("a@b.com", "Your Midora verification code", "<p>x</p>", dedupe=False))
    asyncio.run(queue.enqueue_mail("a@b.com", "Weekly note", "<p>x</p>"))
    assert inserted == [False, True]


def test_failed_code_email_is_closed_and_reported(monkeypatch):
    closed = []

    class DB:
        def table(self, _n):
            return self

        def update(self, patch):
            closed.append(patch)
            return self

        def __getattr__(self, _name):
            return lambda *a, **k: self

        def execute(self):
            return self

    async def failing_send_ids(ids):
        return 0

    monkeypatch.setattr(queue, "_insert_rows", lambda *a, **k: ["id1"])
    monkeypatch.setattr(queue, "send_ids", failing_send_ids)
    monkeypatch.setattr(queue, "_status_of", lambda i: "pending")
    monkeypatch.setattr(queue, "get_supabase_admin", lambda: DB())
    assert asyncio.run(queue.deliver_now("a@b.com", "Code", "<p>1</p>")) is False
    assert {"status": "failed"} in closed


def test_code_email_raises_when_not_delivered(monkeypatch):
    async def not_delivered(to, subject, body):
        return False

    monkeypatch.setattr(queue, "deliver_now", not_delivered)
    with pytest.raises(send.EmailNotSent):
        asyncio.run(send.send_email_otp("a@b.com", "123456"))


def test_code_email_contains_the_code_when_delivered(monkeypatch):
    seen = {}

    async def delivered(to, subject, body):
        seen.update(to=to, subject=subject, body=body)
        return True

    monkeypatch.setattr(queue, "deliver_now", delivered)
    asyncio.run(send.send_email_otp("a@b.com", "123456"))
    assert seen["to"] == "a@b.com" and "123456" in seen["body"]


def test_smtp_timeout_is_short():
    assert send.SMTP_TIMEOUT_SECONDS <= 20
