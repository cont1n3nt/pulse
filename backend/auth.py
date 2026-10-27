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
        self.login_attempts: dict[str, deque[float]] = defaultdict(deque)

    def digest(self, code: str) -> str:
        return hmac.new(self.key, code.encode("utf-8"), hashlib.sha256).hexdigest()

    def new_code(self) -> str:
        return secrets.token_urlsafe(32)

    def get_user_id(self, token: str | None) -> str | None:
        """Токен вида user_id.expires.подпись: проверяется ключом, поэтому переживает перезапуск."""
        if not token or token.count(".") != 2:
            return None
        user_id, expires, signature = token.split(".")
        if not hmac.compare_digest(signature, self.digest(f"session:{user_id}.{expires}")):
            return None
        if not expires.isdigit() or int(expires) <= time.time():
            return None
        return user_id

    def new_session(self, user_id: str) -> str:
        expires = int(time.time()) + SESSION_AGE
        return f"{user_id}.{expires}.{self.digest(f'session:{user_id}.{expires}')}"

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
