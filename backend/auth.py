import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque


COOKIE_NAME = "pulse_session"
SESSION_AGE = 30 * 24 * 60 * 60


class AuthState:
    def __init__(self, secret_key: str):
        if len(secret_key.encode("utf-8")) < 32:
            raise ValueError("PULSE_AUTH_KEY must contain at least 32 bytes")
        self.key = secret_key.encode("utf-8")
        self.lock = threading.RLock()
        self.sessions: dict[str, tuple[str, float]] = {}
        self.login_attempts: dict[str, deque[float]] = defaultdict(deque)

    def digest(self, code: str) -> str:
        return hmac.new(self.key, code.encode("utf-8"), hashlib.sha256).hexdigest()

    def new_code(self) -> str:
        return secrets.token_urlsafe(32)

    def get_user_id(self, token: str | None) -> str | None:
        if not token:
            return None
        with self.lock:
            session = self.sessions.get(token)
            if session is None:
                return None
            user_id, expires = session
            if expires <= time.time():
                del self.sessions[token]
                return None
            return user_id

    def new_session(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[token] = (user_id, time.time() + SESSION_AGE)
        return token

    def allow_login(self, address: str) -> bool:
        now = time.monotonic()
        with self.lock:
            attempts = self.login_attempts[address]
            while attempts and now - attempts[0] >= 60:
                attempts.popleft()
            if len(attempts) >= 10:
                return False
            attempts.append(now)
            return True
