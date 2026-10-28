import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.main import create_app
from backend.models import ChatMessage


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

    def test_rename_persists_without_reordering(self):
        response = self.client.patch(f"/api/chats/{self.chat['id']}", headers=self.headers,
                                     json={"title": "  Сочи   в мае  "})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["chat"]["title"], "Сочи в мае")
        self.assertEqual(response.json()["chat"]["updated_at"], self.chat["updated_at"])
        self.assertEqual(self.client.get("/api/chats").json()["chats"][0]["title"], "Сочи в мае")

    def test_invalid_names_and_origin(self):
        for title in ["", "   ", "x" * 61]:
            response = self.client.patch(f"/api/chats/{self.chat['id']}", headers=self.headers,
                                         json={"title": title})
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.patch(f"/api/chats/{self.chat['id']}",
                                           json={"title": "Название"}).status_code, 403)
        self.assertEqual(self.client.delete(f"/api/chats/{self.chat['id']}").status_code, 403)

    def test_other_users_cannot_read_rename_or_delete(self):
        with TestClient(self.app, base_url="http://testserver") as other:
            other.post("/api/auth/guest", headers=self.headers)
            path = f"/api/chats/{self.chat['id']}"
            self.assertEqual(other.get(path).status_code, 404)
            self.assertEqual(other.get(path + "/trip").status_code, 404)
            self.assertEqual(other.patch(path, headers=self.headers, json={"title": "Чужой"}).status_code, 404)
            self.assertEqual(other.delete(path, headers=self.headers).status_code, 404)

    @patch("backend.endpoints.reset_session")
    def test_delete_removes_messages_and_last_chat(self, reset):
        chat_id = self.chat["id"]
        with self.app.state.db() as db:
            db.add(ChatMessage(chat_id=chat_id, role="user", content="Привет"))
            db.commit()
        self.assertEqual(self.client.delete(f"/api/chats/{chat_id}", headers=self.headers).status_code, 200)
        reset.assert_called_once_with(chat_id)
        self.assertEqual(self.client.get("/api/chats").json(), {"chats": []})
        self.assertEqual(self.client.get(f"/api/chats/{chat_id}").status_code, 404)
        self.assertEqual(self.client.delete(f"/api/chats/{chat_id}", headers=self.headers).status_code, 404)
        with self.app.state.db() as db:
            self.assertIsNone(db.scalar(select(ChatMessage)))

    @patch("backend.endpoints.trip_data", side_effect=AssertionError("History must not initialize the agent"))
    def test_history_does_not_wait_for_trip(self, trip):
        with self.app.state.db() as db:
            db.add(ChatMessage(chat_id=self.chat["id"], role="user", content="В Сочи"))
            db.commit()
        response = self.client.get(f"/api/chats/{self.chat['id']}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["messages"][0]["content"], "В Сочи")
        self.assertNotIn("trip", response.json())
        trip.assert_not_called()

    def test_empty_chat_does_not_need_trip_request(self):
        response = self.client.get(f"/api/chats/{self.chat['id']}")
        self.assertIsNone(response.json()["trip"])

    def test_session_cleanup_does_not_initialize_llm(self):
        from backend.agent.service import reset_session
        with patch("backend.agent.service._agent", side_effect=AssertionError("Must not initialize LLM")), \
                patch("backend.agent.service._checkpointer") as saver:
            reset_session(self.chat["id"])
            saver.return_value.delete_thread.assert_called_once_with(self.chat["id"])

    @patch("backend.endpoints.stream_message", return_value=iter([{"type": "done"}]))
    def test_first_message_keeps_custom_name(self, stream):
        path = f"/api/chats/{self.chat['id']}"
        self.client.patch(path, headers=self.headers, json={"title": "Мой отпуск"})
        response = self.client.post("/api/chat", headers=self.headers,
                                    json={"session_id": self.chat["id"], "message": "В Сочи"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.get(path).json()["chat"]["title"], "Мой отпуск")

    @patch("backend.endpoints.trip_data", return_value={"hotels": [], "confirm": None})
    def test_trip_is_loaded_separately(self, trip):
        response = self.client.get(f"/api/chats/{self.chat['id']}/trip")
        self.assertEqual(response.status_code, 200)
        self.assertIn("trip", response.json())
        trip.assert_called_once_with(self.chat["id"])


if __name__ == "__main__":
    unittest.main()
