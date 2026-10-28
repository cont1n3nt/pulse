"""Ошибки API: сбой базы — 503 с одной причиной, перебор кода входа ограничен, долгий ход шлёт ping."""

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from backend import endpoints
from backend.auth import LOGINS_TOTAL, AuthState
from backend.main import create_app


class DatabaseErrorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "test.sqlite3"
        self.app = create_app(f"sqlite:///{path.as_posix()}", secret_key="test-key-" * 8)
        self.client = TestClient(self.app, base_url="http://testserver")
        self.headers = {"Origin": "http://testserver"}
        self.client.post("/api/auth/guest", headers=self.headers)
        self.chat = self.client.post("/api/chats", headers=self.headers).json()["chat"]

    def tearDown(self):
        self.client.close()
        self.app.state.db.kw["bind"].dispose()
        self.directory.cleanup()

    def test_app_database_failure_is_503(self):
        broken = OperationalError("SELECT 1", {}, Exception("disk I/O error"))
        with patch.object(self.app.state, "db", side_effect=broken):
            for response in (self.client.get("/health"), self.client.get("/api/chats"),
                             self.client.get("/api/purchases"),
                             self.client.delete(f"/api/chats/{self.chat['id']}", headers=self.headers)):
                self.assertEqual(response.status_code, 503)
                self.assertEqual(response.json(), {"detail": "База данных недоступна"})

    def test_agent_database_failure_is_503(self):
        with patch.object(endpoints, "account_cart", side_effect=sqlite3.OperationalError("database is locked")):
            response = self.client.get("/api/purchases")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"detail": "База данных недоступна"})


def test_login_limit_holds_when_address_changes():
    auth = AuthState("test-key-" * 8)
    allowed = sum(auth.allow_login(f"10.0.0.{i}") for i in range(LOGINS_TOTAL + 20))
    assert allowed == LOGINS_TOTAL


def test_login_limit_per_address():
    auth = AuthState("test-key-" * 8)
    assert sum(auth.allow_login("10.0.0.1") for _ in range(15)) == 10
    assert auth.allow_login("10.0.0.2")


class HeartbeatTests(unittest.TestCase):
    """Пока агент молчит, в потоке идёт ping: фронт не считает долгий ход обрывом связи."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "test.sqlite3"
        self.app = create_app(f"sqlite:///{path.as_posix()}", secret_key="test-key-" * 8)
        self.client = TestClient(self.app, base_url="http://testserver")
        self.headers = {"Origin": "http://testserver"}
        self.client.post("/api/auth/guest", headers=self.headers)
        self.chat = self.client.post("/api/chats", headers=self.headers).json()["chat"]

    def tearDown(self):
        self.client.close()
        self.app.state.db.kw["bind"].dispose()
        self.directory.cleanup()

    def test_slow_turn_sends_ping_and_saves_reply(self):
        def slow(session_id, text, user_id):
            time.sleep(0.3)
            yield {"type": "token", "text": "Готово"}
            yield {"type": "done"}

        with patch.object(endpoints, "HEARTBEAT_S", 0.05), patch.object(endpoints, "stream_message", slow):
            body = self.client.post("/api/chat", headers=self.headers,
                                    json={"session_id": self.chat["id"], "message": "Сочи"}).text
        self.assertIn(": ping", body)
        self.assertLess(body.index(": ping"), body.index("Готово"))
        messages = self.client.get(f"/api/chats/{self.chat['id']}").json()["messages"]
        self.assertEqual([m["content"] for m in messages], ["Сочи", "Готово"])
