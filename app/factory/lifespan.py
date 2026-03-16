from contextlib import asynccontextmanager


@asynccontextmanager
async def lifespan(app):
    # Startup: e.g. init Mail, warm caches
    yield
    # Shutdown: cleanup
    pass
