from fastapi import FastAPI

from app.factory.lifespan import lifespan
from app.factory.middleware import register_middleware
from app.factory.routers import register_routers


def create_app() -> FastAPI:
    app = FastAPI(
        title="DigitalMall API",
        version="1.0.0",
        lifespan=lifespan,
    )
    register_middleware(app)
    register_routers(app)
    return app
