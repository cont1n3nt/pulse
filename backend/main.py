import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.auth import AuthState
from backend.database import make_session_factory
from backend.endpoints import router
from backend import models  # noqa: F401 - register model before create_all


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
# каждый HTTP-запрос клиентов виден и так по логам модели и инструментов
for noisy in ("httpx", "httpx2", "httpcore", "openai", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def create_app(database_url: str | None = None, secret_key: str | None = None) -> FastAPI:
    key = secret_key or os.getenv("PULSE_AUTH_KEY")
    if not key:
        raise RuntimeError("Set PULSE_AUTH_KEY to a random value of at least 32 bytes")
    app = FastAPI(title="Pulse Auth", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.auth = AuthState(key)
    app.state.db = make_session_factory(database_url)
    @app.middleware("http")
    async def revalidate_frontend(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.include_router(router)
    app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent.parent / "frontend"), name="static")
    return app


app = create_app()
