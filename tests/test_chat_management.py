import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.main import create_app
from backend.models import ArchivedChat, ChatMessage


class ChatManagementTests(unittest.TestCase):
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

    def test_changes_require_origin(self):
        self.assertEqual(self.client.post("/api/chats").status_code, 403)
        self.assertEqual(self.client.delete(f"/api/chats/{self.chat['id']}").status_code, 403)

    def test_other_users_cannot_read_or_delete(self):
        with TestClient(self.app, base_url="http://testserver") as other:
            other.post("/api/auth/guest", headers=self.headers)
            path = f"/api/chats/{self.chat['id']}"
            self.assertEqual(other.get(path).status_code, 404)
            self.assertEqual(other.delete(path, headers=self.headers).status_code, 404)
            self.assertEqual(other.get("/api/chats").json(), {"chats": []})

    def test_delete_hides_chat_and_removes_messages(self):
        chat_id = self.chat["id"]
        with self.app.state.db() as db:
            db.add(ChatMessage(chat_id=chat_id, role="user", content="Привет"))
            db.commit()
        self.assertEqual(self.client.delete(f"/api/chats/{chat_id}", headers=self.headers).status_code, 200)
        self.assertEqual(self.client.get("/api/chats").json(), {"chats": []})
        self.assertEqual(self.client.get(f"/api/chats/{chat_id}").status_code, 404)
        with self.app.state.db() as db:
            self.assertIsNone(db.scalar(select(ChatMessage)))
            # чат только скрыт: сессия агента с корзиной и заказами остаётся для «Покупок»
            self.assertIsNotNone(db.get(ArchivedChat, chat_id))

    @patch("backend.endpoints.trip_data", return_value={"hotels": [], "confirm": None})
    def test_history_comes_with_trip(self, trip):
        with self.app.state.db() as db:
            db.add(ChatMessage(chat_id=self.chat["id"], role="user", content="В Сочи"))
            db.commit()
        response = self.client.get(f"/api/chats/{self.chat['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["messages"][0]["content"], "В Сочи")
        self.assertEqual(response.json()["trip"], {"hotels": [], "confirm": None})
        trip.assert_called_once_with(self.chat["id"])

    @patch("backend.endpoints.stream_message", return_value=iter([{"type": "done"}]))
    def test_first_message_names_chat_once(self, stream):
        path = f"/api/chats/{self.chat['id']}"
        for message in ["В Сочи на майские", "А на день позже?"]:
            response = self.client.post("/api/chat", headers=self.headers,
                                        json={"session_id": self.chat["id"], "message": message})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(path).json()["chat"]["title"], "В Сочи на майские")


if __name__ == "__main__":
    unittest.main()
