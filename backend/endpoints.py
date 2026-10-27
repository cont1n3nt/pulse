import hmac
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from backend.agent import get_order, get_state, pending_checkout, resume_checkout, stream_message
from backend.auth import COOKIE_NAME, SESSION_AGE
from backend.models import Chat, ChatMessage, User, utc_now


log = logging.getLogger(__name__)
router = APIRouter()
FRONTEND = Path(__file__).resolve().parent.parent / "frontend"


class LoginRequest(BaseModel):
    code: str = Field(min_length=1, max_length=256)


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=5000)


class CheckoutDecision(BaseModel):
    approved: bool


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


def owned_chat(db, chat_id: str, user_id: str) -> Chat:
    item = db.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))
    if item is None:
        raise HTTPException(404, "Чат не найден")
    return item


def trip_data(chat_id: str) -> dict | None:
    """Состояние поездки для восстановления карточек после перезагрузки. ID чата = сессия агента."""
    try:
        trip = get_state(chat_id)
        confirm = pending_checkout(chat_id)
    except Exception:
        # без ключа LLM или при сбое агента история чата всё равно открывается
        log.exception("Не удалось прочитать состояние поездки чата %s", chat_id)
        return None
    orders = [order.model_dump(mode="json") for order_id in trip.orders if (order := get_order(order_id))]
    return {
        "stage": trip.stage,
        "hotels": [hotel.model_dump(mode="json") for hotel in trip.hotels],
        "flights": [flight.model_dump(mode="json") for flight in trip.flights],
        "cart": {"items": [item.model_dump(mode="json") for item in trip.cart],
                 "total_rub": trip.cart_total_rub, "issues": [issue.model_dump() for issue in trip.issues]},
        "orders": orders,
        "confirm": confirm,
    }


def save_reply(session_factory, chat_id: str, text: str) -> dict | None:
    """Сохраняет ответ ассистента и поднимает чат в списке. Возвращает обновлённые данные чата."""
    try:
        with session_factory() as db:
            if text:
                db.add(ChatMessage(chat_id=chat_id, role="assistant", content=text))
            db.execute(update(Chat).where(Chat.id == chat_id).values(updated_at=utc_now()))
            db.commit()
            item = db.get(Chat, chat_id)
            db.refresh(item)
            return chat_data(item)
    except SQLAlchemyError:
        log.exception("Не удалось сохранить ответ агента в чат %s", chat_id)
        return None


def agent_stream(request: Request, chat_id: str, events) -> StreamingResponse:
    """События агента в SSE. Текст ответа сохраняется в историю, даже если клиент отключился."""
    session_factory = request.app.state.db

    def body():
        parts: list[str] = []
        saved = False
        try:
            for event in events:
                if event["type"] == "token":
                    parts.append(event["text"])
                elif event["type"] == "done":
                    saved = True
                    if chat := save_reply(session_factory, chat_id, "".join(parts).strip()):
                        yield f"data: {json.dumps({'type': 'chat', 'chat': chat}, ensure_ascii=False)}\n\n"
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            if not saved:
                save_reply(session_factory, chat_id, "".join(parts).strip())

    return StreamingResponse(body(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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
def chat(request: Request, payload: ChatRequest) -> StreamingResponse:
    """Сообщение пользователя сохраняется сразу, ответ агента приходит потоком SSE.

    События: reasoning, tool, block (hotels/flights/cart/order), token, confirm, chat, error, done.
    """
    check_origin(request)
    user_id = require_user_id(request)
    text = payload.message.strip()
    if not text:
        raise HTTPException(422, "Сообщение не может быть пустым")
    try:
        with request.app.state.db() as db:
            # Acquire SQLite's write lock before reading the title or adding a message.
            result = db.execute(update(Chat).where(
                Chat.id == payload.session_id, Chat.user_id == user_id,
            ).values(updated_at=utc_now()))
            if result.rowcount == 0:
                raise HTTPException(404, "Чат не найден")
            current = db.get(Chat, payload.session_id)
            if db.scalar(select(ChatMessage.id).where(ChatMessage.chat_id == current.id).limit(1)) is None:
                current.title = " ".join(text.split())[:60]
            db.add(ChatMessage(chat_id=current.id, role="user", content=text))
            db.commit()
    except SQLAlchemyError as exc:
        raise HTTPException(503, "База данных недоступна") from exc
    # ID чата служит сессией агента: у каждого чата своя поездка и своя корзина
    return agent_stream(request, payload.session_id, stream_message(payload.session_id, text))


@router.post("/api/chats/{chat_id}/checkout")
def checkout(request: Request, chat_id: str, decision: CheckoutDecision) -> StreamingResponse:
    """Кнопка «Оплатить» или «Отмена» в карточке подтверждения: граф агента продолжается."""
    check_origin(request)
    user_id = require_user_id(request)
    try:
        with request.app.state.db() as db:
            owned_chat(db, chat_id, user_id)
    except SQLAlchemyError as exc:
        raise HTTPException(503, "База данных недоступна") from exc
    return agent_stream(request, chat_id, resume_checkout(chat_id, decision.approved))


@router.get("/api/orders/{order_id}")
def order(request: Request, order_id: str) -> dict:
    user_id = require_user_id(request)
    found = get_order(order_id)
    if found is None:
        raise HTTPException(404, "Заказ не найден")
    try:
        with request.app.state.db() as db:
            owned_chat(db, found.session_id, user_id)
    except HTTPException:
        raise HTTPException(404, "Заказ не найден") from None
    except SQLAlchemyError as exc:
        raise HTTPException(503, "База данных недоступна") from exc
    return found.model_dump(mode="json")


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
            item = owned_chat(db, chat_id, user_id)
            messages = db.scalars(select(ChatMessage).where(ChatMessage.chat_id == chat_id).order_by(ChatMessage.id)).all()
            data = {"chat": chat_data(item), "messages": [message_data(message) for message in messages]}
    except SQLAlchemyError as exc:
        raise HTTPException(503, "База данных недоступна") from exc
    data["trip"] = trip_data(chat_id)
    return data


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
