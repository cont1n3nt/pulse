import hmac
import hashlib
import json
import logging
import uuid
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import delete, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from backend.agent import (account_cart, change_account_cart, forget_memory, get_memory, get_order,
                           hotel_photo, resume_checkout, stream_message, trip_view)
from backend.agent.service import update_memory
from backend.agent.memory import MAX_TEXT
from backend.agent.location import HERE
from backend.auth import COOKIE_NAME, SESSION_AGE
from backend.models import ArchivedChat, HiddenOrder, Chat, ChatMessage, User, utc_now


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
    user_id = request.app.state.auth.get_user_id(request.cookies.get(COOKIE_NAME))
    if user_id is None:
        return None
    # подпись верна, но базу могли стереть: такой сессии больше нет
    try:
        with request.app.state.db() as db:
            return user_id if db.get(User, user_id) is not None else None
    except SQLAlchemyError as exc:
        raise HTTPException(503, "База данных недоступна") from exc


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


def short_title(text: str, limit: int = 32) -> str:
    """Название чата из первого сообщения: целые слова до limit символов, остальное — многоточием."""
    words = " ".join(text.split())
    if len(words) <= limit:
        return words
    cut = words[:limit + 1].rsplit(" ", 1)[0] if " " in words[:limit + 1] else words[:limit]
    return cut.rstrip(" ,.;:—-") + "…"


def message_data(message: ChatMessage) -> dict:
    return {"id": message.id, "role": message.role, "content": message.content}


def owned_chat(db, chat_id: str, user_id: str, include_archived: bool = False) -> Chat:
    item = db.scalar(select(Chat).where(Chat.id == chat_id, Chat.user_id == user_id))
    if item is None or (not include_archived and db.get(ArchivedChat, chat_id)):
        raise HTTPException(404, "Чат не найден")
    return item


def trip_data(chat_id: str) -> dict | None:
    """Состояние поездки для восстановления карточек после перезагрузки. ID чата = сессия агента."""
    try:
        view = trip_view(chat_id)
    except Exception:
        # без ключа LLM или при сбое агента история чата всё равно открывается
        log.exception("Не удалось прочитать состояние поездки чата %s", chat_id)
        return None
    view["orders"] = [order.model_dump(mode="json") for order_id in view["orders"] if (order := get_order(order_id))]
    return view


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


def agent_stream(request: Request, chat_id: str, events, opened: dict | None = None) -> StreamingResponse:
    """События агента в SSE. Текст ответа сохраняется в историю, даже если клиент отключился.
    opened — данные чата для события в самом начале: название из первого сообщения видно сразу."""
    session_factory = request.app.state.db

    def body():
        parts: list[str] = []
        saved = False
        if opened:
            yield f"data: {json.dumps({'type': 'chat', 'chat': opened}, ensure_ascii=False)}\n\n"
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
def index() -> HTMLResponse:
    html = (FRONTEND / "index.html").read_text(encoding="utf-8")
    for asset in ("style.css", "app.js"):
        version = hashlib.sha256((FRONTEND / asset).read_bytes()).hexdigest()[:16]
        html = html.replace(f'/static/{asset}"', f'/static/{asset}?v={version}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-store"})


@router.get("/health")
def health(request: Request) -> dict[str, str]:
    """Для healthcheck: сервер отвечает и база открывается."""
    try:
        with request.app.state.db() as db:
            db.execute(text("SELECT 1"))
    except SQLAlchemyError:
        raise HTTPException(503, "База недоступна")
    return {"status": "ok"}


@router.get("/api/location")
def location() -> dict:
    """Локация агента для интерфейса: подпись поездки и подсказки пустого чата."""
    return {"id": HERE.id, "title": HERE.names.title, "city": HERE.names.city,
            "districts": list(HERE.district_names), "trains": HERE.rail is not None,
            "examples": [e.model_dump() for e in HERE.examples]}


@router.get("/api/auth/me")
def me(request: Request) -> dict[str, str]:
    user_id = current_user_id(request)
    if user_id is None:
        raise HTTPException(401, "Нет действующей сессии")
    return {"user_id": user_id}


@router.post("/api/chat")
def chat(request: Request, payload: ChatRequest) -> StreamingResponse:
    """Сообщение пользователя сохраняется сразу, ответ агента приходит потоком SSE.

    События: reasoning, tool, block (packages/hotels/flights/cart/order/reply), token, confirm, chat,
    error, done.
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
                ~Chat.id.in_(select(ArchivedChat.chat_id)),
            ).values(updated_at=utc_now()))
            if result.rowcount == 0:
                raise HTTPException(404, "Чат не найден")
            current = db.get(Chat, payload.session_id)
            if db.scalar(select(ChatMessage.id).where(ChatMessage.chat_id == current.id).limit(1)) is None:
                current.title = short_title(text)
            db.add(ChatMessage(chat_id=current.id, role="user", content=text))
            db.commit()
            db.refresh(current)
            data = chat_data(current)
    except SQLAlchemyError as exc:
        raise HTTPException(503, "База данных недоступна") from exc
    # ID чата служит сессией агента: у каждого чата своя поездка и своя корзина
    return agent_stream(request, payload.session_id, stream_message(payload.session_id, text, user_id), data)


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
    return agent_stream(request, chat_id, resume_checkout(chat_id, decision.approved, user_id))


@router.get("/api/memory")
def memory(request: Request) -> dict:
    """Что агент помнит о пользователе между чатами."""
    return get_memory(require_user_id(request))


class MemoryUpdate(BaseModel):
    text: str = Field(max_length=MAX_TEXT)


@router.put("/api/memory")
def edit_memory(request: Request, body: MemoryUpdate) -> dict:
    check_origin(request)
    return update_memory(require_user_id(request), body.text)


@router.delete("/api/memory", status_code=204)
def forget(request: Request) -> Response:
    check_origin(request)
    forget_memory(require_user_id(request))
    return Response(status_code=204)


@router.get("/api/hotels/{hotel_id}/photo")
def photo(request: Request, hotel_id: str) -> Response:
    """Фото отеля: с сайта отеля или из Wikimedia Commons. 404 — фото нет, фронт покажет карту."""
    require_user_id(request)
    found = hotel_photo(hotel_id)
    if found is None:
        raise HTTPException(404, "Фото нет")
    # заголовки — только ASCII, поэтому подпись и ссылка закодированы
    headers = {"Cache-Control": "private, max-age=86400", "X-Photo-Source": found["source"],
               "X-Photo-Credit": quote(found["credit"] or ""), "X-Photo-Page": quote(found["page"] or "")}
    return FileResponse(found["path"], headers=headers)


@router.get("/api/orders/{order_id}")
def order(request: Request, order_id: str) -> dict:
    user_id = require_user_id(request)
    found = get_order(order_id)
    if found is None:
        raise HTTPException(404, "Заказ не найден")
    try:
        with request.app.state.db() as db:
            owned_chat(db, found.session_id, user_id, include_archived=True)
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
            chats = db.scalars(select(Chat).where(Chat.user_id == user_id, ~Chat.id.in_(select(ArchivedChat.chat_id))).order_by(Chat.updated_at.desc(), Chat.id.desc())).all()
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


class CartAction(BaseModel):
    version: int = Field(ge=0)


@router.delete("/api/chats/{chat_id}")
def delete_chat(request: Request, chat_id: str) -> dict:
    check_origin(request)
    user_id = require_user_id(request)
    with request.app.state.db() as db:
        owned_chat(db, chat_id, user_id, include_archived=True)
        if not db.get(ArchivedChat, chat_id):
            db.add(ArchivedChat(chat_id=chat_id))
        db.execute(delete(ChatMessage).where(ChatMessage.chat_id == chat_id))
        db.commit()
    return {"ok": True}


@router.get("/api/purchases")
def purchases(request: Request) -> dict:
    user_id = require_user_id(request)
    with request.app.state.db() as db:
        chats = db.scalars(select(Chat).where(Chat.user_id == user_id).order_by(Chat.updated_at.desc())).all()
        hidden = set(db.scalars(select(HiddenOrder.order_id).where(HiddenOrder.user_id == user_id)))
        archived = set(db.scalars(select(ArchivedChat.chat_id)))
    carts, history = [], []
    for chat in chats:
        cart = account_cart(chat.id)
        if cart["items"]:
            carts.append({"chat_id": chat.id, "title": chat.title, "archived": chat.id in archived, **cart})
        view = trip_view(chat.id)
        for order_id in view["orders"]:
            if order_id not in hidden and (order := get_order(order_id)):
                history.append(order.model_dump(mode="json"))
    history.sort(key=lambda o: o["created_at"], reverse=True)
    return {"carts": carts, "orders": history}


@router.post("/api/purchases/{chat_id}/pay")
def pay_cart(request: Request, chat_id: str, payload: CartAction) -> dict:
    return mutate_cart(request, chat_id, payload, pay=True)


@router.delete("/api/purchases/{chat_id}")
def remove_cart(request: Request, chat_id: str, payload: CartAction) -> dict:
    return mutate_cart(request, chat_id, payload, pay=False)


def mutate_cart(request: Request, chat_id: str, payload: CartAction, pay: bool) -> dict:
    check_origin(request)
    user_id = require_user_id(request)
    with request.app.state.db() as db:
        owned_chat(db, chat_id, user_id, include_archived=True)
    try:
        return change_account_cart(chat_id, payload.version, pay, user_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/api/orders/{order_id}")
def hide_order(request: Request, order_id: str) -> dict:
    check_origin(request)
    user_id = require_user_id(request)
    order(request, order_id)
    with request.app.state.db() as db:
        if not db.get(HiddenOrder, order_id):
            db.add(HiddenOrder(order_id=order_id, user_id=user_id))
            db.commit()
    return {"ok": True}
