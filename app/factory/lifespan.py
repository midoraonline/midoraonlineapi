from contextlib import asynccontextmanager
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend

from mail.queue import start_worker, stop_worker

@asynccontextmanager
async def lifespan(app):
    # Startup: e.g. init Mail, warm caches, start background workers
    FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
    start_worker()
    yield
    # Shutdown: cleanup
    await stop_worker()
