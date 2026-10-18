"""Passwords, server-side sessions and a tiny login rate limiter (standard library only)."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from collections import defaultdict, deque

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .db import AuthSession, User

USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,20}$")
_N, _R, _P = 2 ** 14, 8, 1


class AuthError(Exception):
    pass


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, digest_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                                n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(db: Session, username: str, password: str, today: str,
                invite: str = "") -> User:
    if settings.invite_code and not hmac.compare_digest(invite.strip(), settings.invite_code):
        raise AuthError("Wrong invite code. Ask your teacher for the code.")
    if not USERNAME_RE.match(username):
        raise AuthError("Username must be 3-20 characters: letters, numbers or underscore.")
    if len(password) < 8 or len(password) > 200:
        raise AuthError("Password must be at least 8 characters.")
    if db.scalar(select(User).where(User.username_lower == username.lower())):
        raise AuthError("That username is taken.")
    now = time.time()
    user = User(
        username=username, username_lower=username.lower(), pw_hash=hash_password(password),
        created_at=now, cash=settings.start_balance, start_balance=settings.start_balance,
        season=1, season_started_at=now,
        require_sl=False,
        max_risk_pct=settings.default_max_risk_pct, daily_loss_pct=settings.default_daily_loss_pct,
        day_start_equity=settings.start_balance, day_start_date=today,
    )
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, username: str, password: str) -> User:
    user = db.scalar(select(User).where(User.username_lower == username.lower()))
    # Always run a hash so response time does not reveal whether the username exists.
    stored = user.pw_hash if user else "scrypt$16384$8$1$00$00"
    ok = verify_password(password, stored)
    if not user or not ok:
        raise AuthError("Wrong username or password.")
    return user


def new_session(db: Session, user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    db.add(AuthSession(token_hash=_token_hash(token), user_id=user_id,
                       expires_at=time.time() + settings.session_days * 86400))
    db.commit()
    return token


def user_for_token(db: Session, token: str | None) -> User | None:
    if not token:
        return None
    sess = db.get(AuthSession, _token_hash(token))
    if not sess or sess.expires_at < time.time():
        return None
    return db.get(User, sess.user_id)


def end_session(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(AuthSession).where(AuthSession.token_hash == _token_hash(token)))
        db.commit()


def purge_expired(db: Session) -> None:
    db.execute(delete(AuthSession).where(AuthSession.expires_at < time.time()))
    db.commit()


class RateLimiter:
    """Sliding window: at most `limit` hits per `window` seconds per key."""

    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self.hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.time()
        q = self.hits[key]
        while q and q[0] < now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def sweep(self) -> None:
        now = time.time()
        for key in [k for k, q in self.hits.items() if not q or q[-1] < now - self.window]:
            del self.hits[key]
