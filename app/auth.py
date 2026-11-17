"""Autenticação de usuário único com senha (hash scrypt) e sessão no servidor."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**15, 8, 1


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, maxmem=64 * 1024 * 1024, dklen=32)
    b64 = lambda b: base64.urlsafe_b64encode(b).decode().rstrip("=")  # noqa: E731
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algo, n, r, p, salt_b64, digest_b64 = encoded.split("$")
        if algo != "scrypt":
            return False
        pad = lambda s: s + "=" * (-len(s) % 4)  # noqa: E731
        salt = base64.urlsafe_b64decode(pad(salt_b64))
        expected = base64.urlsafe_b64decode(pad(digest_b64))
        got = hashlib.scrypt(password.encode(), salt=salt, n=int(n), r=int(r), p=int(p), maxmem=64 * 1024 * 1024, dklen=len(expected))
        return hmac.compare_digest(got, expected)
    except Exception:  # hash malformado
        return False


@dataclass
class Session:
    sid: str
    expires_at: float


class SessionStore:
    """Sessões em memória. Reiniciar o servidor exige novo login (aceitável para uso individual)."""

    def __init__(self, secret: str, ttl: int):
        self._secret = (secret or secrets.token_urlsafe(32)).encode()
        self._ttl = ttl
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def _sign(self, sid: str) -> str:
        return hmac.new(self._secret, sid.encode(), hashlib.sha256).hexdigest()[:32]

    def create(self) -> tuple[Session, str]:
        sid = secrets.token_urlsafe(32)
        sess = Session(sid, time.time() + self._ttl)
        with self._lock:
            self._sessions[sid] = sess
        return sess, f"{sid}.{self._sign(sid)}"

    def get(self, cookie_value: str | None) -> Session | None:
        if not cookie_value or "." not in cookie_value:
            return None
        sid, sig = cookie_value.rsplit(".", 1)
        if not hmac.compare_digest(sig, self._sign(sid)):
            return None
        with self._lock:
            sess = self._sessions.get(sid)
            if sess and sess.expires_at > time.time():
                return sess
            self._sessions.pop(sid, None)
        return None

    def destroy(self, cookie_value: str | None) -> str | None:
        sess = self.get(cookie_value)
        if sess:
            with self._lock:
                self._sessions.pop(sess.sid, None)
            return sess.sid
        return None

    def purge(self) -> None:
        now = time.time()
        with self._lock:
            for sid in [s for s, v in self._sessions.items() if v.expires_at <= now]:
                del self._sessions[sid]


class LoginLimiter:
    """Limita tentativas de login por IP numa janela deslizante."""

    def __init__(self, max_attempts: int, window: int):
        self.max_attempts, self.window = max_attempts, window
        self._fails: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def blocked(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            hits = [t for t in self._fails.get(key, []) if now - t < self.window]
            self._fails[key] = hits
            return len(hits) >= self.max_attempts

    def fail(self, key: str) -> None:
        with self._lock:
            self._fails.setdefault(key, []).append(time.time())

    def reset(self, key: str) -> None:
        with self._lock:
            self._fails.pop(key, None)
