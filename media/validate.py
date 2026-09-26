"""Publish-time checks for listing media URLs."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException

logger = logging.getLogger(__name__)

_HEAD_TIMEOUT = httpx.Timeout(3.0, connect=2.0)
_CONCURRENCY = 8
_ALLOWED_HOSTS = {"utfs.io", "ufs.sh", "images.unsplash.com"}
_ALLOWED_SUFFIXES = (".ufs.sh", ".utfs.io")


@dataclass
class ProbeResult:
    status: int | None = None
    content_type: str = ""
    timed_out: bool = False


def media_url_is_allowed(url: str) -> bool:
    raw = (url or "").strip()
    lowered = raw.lower()
    if not raw or lowered.startswith("blob:") or lowered.startswith("data:"):
        return False
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower().rstrip(".")
    if host in _ALLOWED_HOSTS or host.endswith(_ALLOWED_SUFFIXES):
        return True
    return False


def _is_media_type(content_type: str) -> bool:
    base = (content_type or "").split(";", 1)[0].strip().lower()
    return base.startswith("image/") or base.startswith("video/")


async def head_media_urls(urls: list[str]) -> dict[str, ProbeResult]:
    if not urls:
        return {}
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def one(client: httpx.AsyncClient, url: str) -> tuple[str, ProbeResult]:
        async with sem:
            try:
                response = await client.head(url)
            except httpx.TimeoutException:
                logger.warning("media HEAD timed out for %s; allowing", url)
                return url, ProbeResult(timed_out=True)
            except httpx.HTTPError as exc:
                logger.warning("media HEAD failed for %s (%s); allowing", url, exc)
                return url, ProbeResult(timed_out=True)
            return url, ProbeResult(
                status=response.status_code,
                content_type=response.headers.get("content-type", ""),
            )

    async with httpx.AsyncClient(timeout=_HEAD_TIMEOUT, follow_redirects=True) as client:
        pairs = await asyncio.gather(*(one(client, url) for url in urls))
    return dict(pairs)


async def unreachable_media_urls(urls: list[str], *, head=None) -> list[str]:
    """URLs that must not be published. Timeouts are allowed and logged."""
    ordered = [str(url).strip() for url in urls if str(url or "").strip()]
    bad = [url for url in ordered if not media_url_is_allowed(url)]
    allowed = [url for url in ordered if media_url_is_allowed(url)]
    if not allowed:
        return bad
    if head is None:
        probed = await head_media_urls(allowed)
    else:
        probed = {}
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def one(url: str):
            async with sem:
                try:
                    result = head(url)
                    if asyncio.iscoroutine(result):
                        result = await result
                except httpx.TimeoutException:
                    logger.warning("media HEAD timed out for %s; allowing", url)
                    result = ProbeResult(timed_out=True)
                except httpx.HTTPError as exc:
                    logger.warning("media HEAD failed for %s (%s); allowing", url, exc)
                    result = ProbeResult(timed_out=True)
                probed[url] = result

        await asyncio.gather(*(one(url) for url in allowed))

    for url in allowed:
        result = probed.get(url) or ProbeResult(timed_out=True)
        if result.timed_out:
            continue
        if result.status == 200 and _is_media_type(result.content_type):
            continue
        bad.append(url)
    return bad


async def assert_published_media(urls: list[str] | None, *, head=None) -> None:
    if not urls:
        return
    bad = await unreachable_media_urls(list(urls), head=head)
    if not bad:
        return
    raise HTTPException(
        status_code=422,
        detail={
            "detail": "One or more listing images could not be reached.",
            "code": "media_unreachable",
            "bad_urls": bad,
        },
    )
