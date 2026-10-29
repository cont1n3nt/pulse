import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from backend.agent.config import DATA_DIR, LLM_API_KEY
from backend.auth import AuthState
from backend.database import make_session_factory
from backend.endpoints import router
from backend import models  # noqa: F401 - register model before create_all


load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logging.basicConfig(level=(os.getenv("LOG_LEVEL") or "INFO").upper(),
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s", datefmt="%H:%M:%S")
# каждый HTTP-запрос клиентов виден и так по логам модели и инструментов
for noisy in ("httpx", "httpx2", "httpcore", "openai", "urllib3"):
    logging.getLogger(noisy).setLevel(logging.WARNING)


def auth_key() -> str:
    """Ключ подписи сессий: PULSE_AUTH_KEY, а без него — сгенерированный при первом запуске
    и сохранённый в DATA_DIR (в Docker это том), чтобы вход переживал перезапуск."""
    if key := os.getenv("PULSE_AUTH_KEY"):
        return key
    path = DATA_DIR / "auth.key"
    if not path.exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
        path.chmod(0o600)
    return path.read_text(encoding="utf-8").strip()


def create_app(database_url: str | None = None, secret_key: str | None = None) -> FastAPI:
    app = FastAPI(title="Pulse", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.auth = AuthState(secret_key or auth_key())
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
if not LLM_API_KEY:
    logging.getLogger("pulse").warning(
        "LLM_API_KEY не задан: интерфейс откроется, но агент отвечать не будет. "
        "Создайте .env из .env.example, впишите ключ и перезапустите сервис.")
