import hmac
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from backend.auth import COOKIE_NAME, SESSION_AGE
from backend.models import Chat, ChatMessage, User, utc_now


router = APIRouter()
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


class LoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=5000)


def check_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    expected = f"{request.url.scheme}://{request.headers.get('host', '')}"
    if origin != expected:
        raise HTTPException(403, "Недопустимый Origin")


def current_user_id(request: Request) -> str | None:
    return request.app.state.auth.get_user_id(request.cookies.get(COOKIE_NAME))


def require_user_id(request: Request) -> str:
    user_id = current_user_id(request)
    if user_id is None:
        raise HTTPException(401, "Нет действующей сессии")
    return user_id


def chat_data(chat: Chat) -> dict:
    return {
        "id": chat.id, "title": chat.title,
        "created_at": chat.created_at.isoformat() + "Z",
        "updated_at": chat.updated_at.isoformat() + "Z",
    }


def message_data(message: ChatMessage) -> dict:
    return {"id": message.id, "role": message.role, "content": message.content}


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


@router.post("/api/chat")
def chat(request: Request, payload: ChatRequest) -> dict:
    check_origin(request)
    user_id = require_user_id(request)
    if not payload.message.strip():
        raise HTTPException(422, "Сообщение не может быть пустым")
    try:
        with request.app.state.db() as db:
            # Acquire SQLite's write lock before reading the title or adding a pair.
            result = db.execute(update(Chat).where(
                Chat.id == payload.session_id, Chat.user_id == user_id,
            ).values(updated_at=utc_now()))
            if result.rowcount == 0:
                raise HTTPException(404, "Чат не найден")
            current = db.get(Chat, payload.session_id)
            if db.scalar(select(ChatMessage.id).where(ChatMessage.chat_id == current.id).limit(1)) is None:
                current.title = " ".join(payload.message.split())[:60]
            sent = ChatMessage(chat_id=current.id, role="user", content=payload.message)
            reply = ChatMessage(chat_id=current.id, role="assistant", content=payload.message)
            db.add(sent)
            db.flush()
            db.add(reply)
            db.commit()
            # Refresh dates so serialization is identical for new and loaded rows.
            db.refresh(current)
            return {
                "session_id": current.id, "message": reply.content,
                "chat": chat_data(current), "messages": [message_data(sent), message_data(reply)],
            }
    except SQLAlchemyError as exc:
        raise HTTPException(503, "База данных недоступна") from exc


@router.get("/api/chats")
def list_chats(request: Request) -> dict:
    user_id = require_user_id(request)
    try:
        with request.app.state.db() as db:
            chats = db.scalars(select(Chat).where(Chat.user_id == user_id).order_by(Chat.updated_at.desc(), Chat.id.desc())).all()
            return {"chats": [chat_data(item) for item in chats]}
    except SQLAlchemyError as exc:
        raise HTTPException(503, "База данных недоступна") from exc


@router.post("/api/chats", status_code=201)
def create_chat(request: Request) -> dict:
    check_origin(request)
    user_id = require_user_id(request)
    try:
        with request.app.state.db() as db:
            item = Chat(id=str(uuid.uuid4()), user_id=user_id)
            db.add(item)
            db.commit()
            db.refresh(item)
            return {"chat": chat_data(item), "messages": []}
    except SQLAlchemyError as exc:
        raise HTTPException(503, "База данных недоступна") from exc


@router.get("/api/chats/{chat_id}")
def get_chat(request: Request, chat_id: str) -> dict:
    user_id = require_user_id(request)
    try:
        with request.app.state.db() as db:
            item = db.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))
            if item is None:
                raise HTTPException(404, "Чат не найден")
            messages = db.scalars(select(ChatMessage).where(ChatMessage.chat_id == chat_id).order_by(ChatMessage.id)).all()
            return {"chat": chat_data(item), "messages": [message_data(message) for message in messages]}
    except SQLAlchemyError as exc:
        raise HTTPException(503, "База данных недоступна") from exc


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
