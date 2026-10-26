import os
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select


os.environ.setdefault("PULSE_AUTH_KEY", "test-only-key-0123456789-0123456789-0123456789")
os.environ.setdefault("PULSE_DB_PATH", str(Path(tempfile.gettempdir()) / "pulse-import-test.sqlite3"))

from backend.main import create_app  # noqa: E402
from backend.models import User  # noqa: E402


ORIGIN = {"Origin": "http://testserver"}


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_url = f"sqlite:///{(Path(self.temp.name) / 'users.sqlite3').as_posix()}"
        self.app = create_app(self.db_url, os.environ["PULSE_AUTH_KEY"])
        self.first = TestClient(self.app)
        self.second = TestClient(self.app)

    def tearDown(self):
        self.first.close()
        self.second.close()
        self.app.state.db.kw["bind"].dispose()
        self.temp.cleanup()

    def post(self, client, path, **kwargs):
        return client.post(path, headers=ORIGIN, **kwargs)

    def count(self):
        with self.app.state.db() as db:
            return db.scalar(select(func.count()).select_from(User))

    def test_guest_refresh_other_device_and_login(self):
        self.assertEqual(self.first.get("/").status_code, 200)
        self.assertEqual(self.first.get("/static/app.js").status_code, 200)
        self.assertEqual(self.first.get("/api/auth/me").status_code, 401)
        initial = self.post(self.first, "/api/auth/guest")
        self.assertEqual(initial.status_code, 200)
        first_id, code = initial.json()["user_id"], initial.json()["code"]
        self.assertEqual(uuid.UUID(first_id).version, 4)
        self.assertGreaterEqual(len(code), 32)
        cookie = initial.cookies.get("pulse_session")
        self.assertNotEqual(cookie, code)
        self.assertIn("httponly", initial.headers["set-cookie"].lower())
        self.assertIn("samesite=lax", initial.headers["set-cookie"].lower())
        self.assertIn("max-age=2592000", initial.headers["set-cookie"].lower())
        self.assertEqual(self.post(self.first, "/api/auth/guest").json(), {"user_id": first_id})
        self.assertEqual(self.first.get("/api/auth/me").json()["user_id"], first_id)

        second_id = self.post(self.second, "/api/auth/guest").json()["user_id"]
        self.assertNotEqual(first_id, second_id)
        self.assertEqual(self.count(), 2)
        with self.app.state.db() as db:
            self.assertNotEqual(db.get(User, first_id).password_hash, code)
        self.assertEqual(self.post(self.second, "/api/auth/login", json={"code": code}).json()["user_id"], first_id)
        self.assertEqual(self.first.get("/api/auth/me").json()["user_id"], first_id)
        self.assertEqual(self.second.get("/api/auth/me").json()["user_id"], first_id)
        self.assertEqual(self.count(), 2)

    def test_rotate_invalid_code_and_restart(self):
        self.assertEqual(self.post(self.first, "/api/auth/rotate").status_code, 401)
        initial = self.post(self.first, "/api/auth/guest").json()
        old_code = initial["code"]
        bad = self.post(self.second, "/api/auth/login", json={"code": "wrong"})
        self.assertEqual(bad.status_code, 401)
        self.assertEqual(bad.json()["detail"], "Неверный код доступа")
        self.assertEqual(self.count(), 1)
        rotated = self.post(self.first, "/api/auth/rotate").json()
        self.assertEqual(rotated["user_id"], initial["user_id"])
        self.assertNotEqual(rotated["code"], old_code)
        self.assertEqual(self.first.get("/api/auth/me").json()["user_id"], initial["user_id"])
        self.assertEqual(self.post(self.second, "/api/auth/login", json={"code": old_code}).status_code, 401)
        self.assertEqual(self.post(self.second, "/api/auth/login", json={"code": rotated["code"]}).status_code, 200)

        restarted = TestClient(create_app(self.db_url, os.environ["PULSE_AUTH_KEY"]))
        restarted.cookies.set("pulse_session", self.first.cookies.get("pulse_session"))
        self.assertEqual(restarted.get("/api/auth/me").status_code, 401)
        self.assertEqual(self.post(restarted, "/api/auth/login", json={"code": rotated["code"]}).json()["user_id"], initial["user_id"])
        restarted.close()
        restarted.app.state.db.kw["bind"].dispose()

    def test_origin_and_rate_limit(self):
        self.assertEqual(self.first.post("/api/auth/guest", headers={"Origin": "https://evil.example"}).status_code, 403)
        self.assertEqual(self.count(), 0)
        for _ in range(10):
            self.assertEqual(self.post(self.first, "/api/auth/login", json={"code": "wrong"}).status_code, 401)
        self.assertEqual(self.post(self.first, "/api/auth/login", json={"code": "wrong"}).status_code, 429)
        self.assertEqual(self.count(), 0)

    def test_https_cookie_is_secure(self):
        https = TestClient(self.app, base_url="https://testserver")
        response = https.post("/api/auth/guest", headers={"Origin": "https://testserver"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("secure", response.headers["set-cookie"].lower())
        https.close()

    def test_chat_echo_and_validation(self):
        message = " Привет, море!\nВторая строка "
        response = self.post(self.first, "/api/chat", json={"session_id": "demo", "message": message})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"session_id": "demo", "message": message})
        self.assertEqual(self.post(self.first, "/api/chat", json={"session_id": "demo", "message": "  "}).status_code, 422)
        self.assertEqual(self.first.post("/api/chat", headers={"Origin": "https://evil.example"}, json={"session_id": "demo", "message": message}).status_code, 403)
        self.assertEqual(self.count(), 0)


if __name__ == "__main__":
    unittest.main()
