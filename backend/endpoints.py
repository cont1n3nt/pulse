import hmac
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from backend.auth import COOKIE_NAME, SESSION_AGE
from backend.models import User


router = APIRouter()
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


class LoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if origin != expected:
        raise HTTPException(403, "Недопустимый Origin")


def current_user_id(request: Request) -> str | None:
    return request.app.state.auth.get_user_id(request.cookies.get(COOKIE_NAME))


def set_cookie(request: Request, response: Response, user_id: str) -> None:
    token = request.app.state.auth.new_session(user_id)
    response.set_cookie(
        COOKIE_NAME, token, max_age=SESSION_AGE, httponly=True,
        samesite="lax", secure=request.url.scheme == "https", path="/",
    )


@router.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(FRONTEND / "index.html")


@router.get("/api/auth/me")
def me(request: Request) -> dict[str, str]:
    user_id = current_user_id(request)
    if user_id is None:
        raise HTTPException(401, "Нет действующей сессии")
    return {"user_id": user_id}


@router.post("/api/auth/guest")
def guest(request: Request, response: Response) -> dict[str, str]:
    check_origin(request)
    auth = request.app.state.auth
    with auth.lock:
        user_id = current_user_id(request)
        if user_id is not None:
            return {"user_id": user_id}
        user_id = str(uuid.uuid4())
        code = auth.new_code()
        try:
            with request.app.state.db() as db:
                db.add(User(id=user_id, password_hash=auth.digest(code)))
                db.commit()
        except SQLAlchemyError as exc:
            raise HTTPException(503, "База данных недоступна") from exc
        set_cookie(request, response, user_id)
        return {"user_id": user_id, "code": code}


@router.post("/api/auth/login")
def login(request: Request, response: Response, payload: LoginRequest) -> dict[str, str]:
    check_origin(request)
    auth = request.app.state.auth
    address = request.client.host if request.client else "unknown"
    if not auth.allow_login(address):
        raise HTTPException(429, "Слишком много попыток. Подождите минуту")
    digest = auth.digest(payload.code)
    try:
        with request.app.state.db() as db:
            users = db.scalars(select(User)).all()
            found = None
            for user in users:
                if hmac.compare_digest(user.password_hash, digest):
                    found = user
    except SQLAlchemyError as exc:
        raise HTTPException(503, "База данных недоступна") from exc
    if found is None:
        raise HTTPException(401, "Неверный код доступа")
    set_cookie(request, response, found.id)
    return {"user_id": found.id}


@router.post("/api/auth/rotate")
def rotate(request: Request) -> dict[str, str]:
    check_origin(request)
    auth = request.app.state.auth
    with auth.lock:
        user_id = current_user_id(request)
        if user_id is None:
            raise HTTPException(401, "Нет действующей сессии")
        code = auth.new_code()
        try:
            with request.app.state.db() as db:
                user = db.get(User, user_id)
                if user is None:
                    raise HTTPException(401, "Пользователь не найден")
                user.password_hash = auth.digest(code)
                db.commit()
        except SQLAlchemyError as exc:
            raise HTTPException(503, "База данных недоступна") from exc
        return {"user_id": user_id, "code": code}
