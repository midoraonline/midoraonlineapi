from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.factory.errors import register_exception_handlers
from app.factory.lifespan import lifespan
from app.factory.middleware import register_middleware
from app.factory.routers import register_routers


def create_app() -> FastAPI:
    app = FastAPI(
        title="Midora Online API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory="static"), name="static")
    register_middleware(app)
    register_exception_handlers(app)
    register_routers(app)
    return app
