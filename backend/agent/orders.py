"""Заказы: SQLite-хранилище с защитой от повторного создания. Оплата имитируется."""

import hashlib
import json
import secrets
import sqlite3
import threading
from datetime import datetime, timezone

from .config import DATA_DIR
from .contracts import CartItem, Order

# без похожих символов (0/O, 1/I), чтобы номер можно было продиктовать
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_LOCK = threading.Lock()
_CONN: sqlite3.Connection | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    items TEXT NOT NULL,
    total_rub INTEGER NOT NULL,
    payment TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS orders_session ON orders(session_id);
"""


def _db() -> sqlite3.Connection:
    global _CONN
    if _CONN is None:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _CONN = sqlite3.connect(DATA_DIR / "orders.db", check_same_thread=False)
        _CONN.row_factory = sqlite3.Row
        _CONN.executescript(_SCHEMA)
    return _CONN


def _row(row: sqlite3.Row) -> Order:
    return Order(
        id=row["id"], session_id=row["session_id"], status=row["status"],
        items=[CartItem.model_validate(x) for x in json.loads(row["items"])],
        total_rub=row["total_rub"], payment=row["payment"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def idempotency_key(session_id: str, version: int, items: list[CartItem]) -> str:
    """Один и тот же вариант корзины даёт один заказ, сколько бы раз ни подтверждали."""
    payload = json.dumps([session_id, version, [i.model_dump(mode="json") for i in items]],
                         ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


def create(session_id: str, version: int, items: list[CartItem], total_rub: int) -> Order:
    key = idempotency_key(session_id, version, items)
    with _LOCK:
        db = _db()
        existing = db.execute("SELECT * FROM orders WHERE idempotency_key = ?", (key,)).fetchone()
        if existing:
            return _row(existing)
        order = Order(
            id="PLS-" + "".join(secrets.choice(_ALPHABET) for _ in range(6)),
            session_id=session_id, status="confirmed", items=items, total_rub=total_rub,
            created_at=datetime.now(timezone.utc),
        )
        db.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (order.id, session_id, key, order.status,
             json.dumps([i.model_dump(mode="json") for i in items], ensure_ascii=False),
             total_rub, order.payment, order.created_at.isoformat()),
        )
        db.commit()
        return order


def get(order_id: str) -> Order | None:
    with _LOCK:
        row = _db().execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
    return _row(row) if row else None


def by_session(session_id: str) -> list[Order]:
    with _LOCK:
        rows = _db().execute("SELECT * FROM orders WHERE session_id = ? ORDER BY created_at",
                             (session_id,)).fetchall()
    return [_row(r) for r in rows]
