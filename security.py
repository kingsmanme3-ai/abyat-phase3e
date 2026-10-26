from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import settings

ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8")[:72], hashed.encode("utf-8"))
    except ValueError:
        return False


def create_token(user_id: int) -> str:
    exp = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_minutes)
    return jwt.encode({"sub": str(user_id), "exp": exp}, settings.secret_key, algorithm=ALGORITHM)


def decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        return int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None


MEDIA_TOKEN_MINUTES = 120


def create_media_token(media_id: int, user_id: int) -> str:
    """رابط موقّع قصير العمر لملف وسائط، حتى تعمل وسوم img وvideo التي لا ترسل ترويسة Authorization.
    لا يحتوي المطالبة sub فلا يصلح أبداً كرمز دخول."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=MEDIA_TOKEN_MINUTES)
    return jwt.encode({"mid": media_id, "uid": user_id, "exp": exp}, settings.secret_key, algorithm=ALGORITHM)


def decode_media_token(token: str) -> tuple[int, int] | None:
    try:
        p = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
        return int(p["mid"]), int(p["uid"])
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None
