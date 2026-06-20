import logging
from contextlib import asynccontextmanager
from fastapi_cache import FastAPICache
from fastapi_cache.backends.inmemory import InMemoryBackend

from mail.queue import start_worker, stop_worker

logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app):
    # Startup: init caches, start workers, expire stale boosts
    FastAPICache.init(InMemoryBackend(), prefix="fastapi-cache")
    start_worker()

    try:
        from ranking.boost_service import expire_stale_boosts
        expired = expire_stale_boosts()
        if expired:
            logger.info("Expired %d stale boost(s) on startup", expired)
    except Exception as exc:
        logger.warning("Failed to expire stale boosts on startup: %s", exc)

    yield
    # Shutdown: cleanup
    await stop_worker()
