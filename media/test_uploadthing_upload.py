import asyncio

import httpx

from media import uploadthing


def test_upload_uses_prepare_then_put(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))
        if request.url.path.endswith("/v7/prepareUpload"):
            return httpx.Response(200, json={"key": "abc123", "url": "https://sea1.ingest.uploadthing.com/abc123?sig=x"})
        assert request.method == "PUT"
        assert b"PNGDATA" in request.content
        return httpx.Response(200, json={"ufsUrl": "https://app1.ufs.sh/f/abc123"})

    real = httpx.AsyncClient
    monkeypatch.setattr(uploadthing.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(uploadthing, "uploadthing_api_key", lambda: "sk_test")
    url = asyncio.run(uploadthing.upload_file_bytes(b"PNGDATA", filename="a.png", content_type="image/png"))
    assert url == "https://app1.ufs.sh/f/abc123"
    assert calls[0][0] == "POST" and calls[1][0] == "PUT"


def test_upload_falls_back_to_app_url(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json={"key": "k9", "url": "https://ingest/k9"})
        return httpx.Response(200, text="ok")

    real = httpx.AsyncClient
    monkeypatch.setattr(uploadthing.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(uploadthing, "uploadthing_api_key", lambda: "sk_test")
    monkeypatch.setattr(uploadthing, "uploadthing_app_id", lambda: "myapp")
    url = asyncio.run(uploadthing.upload_file_bytes(b"x", filename="a.png", content_type="image/png"))
    assert url == "https://myapp.ufs.sh/f/k9"
