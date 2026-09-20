"""Password hashing, JWT issuance, and input validation helpers."""
import datetime
import re
from functools import wraps
from typing import Callable, Optional

import bcrypt
import jwt
from flask import g, request

from config import Config
from responses import err
from supabase_client import get_user_by_id

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def hash_password(password: str) -> str:
    """Hash a plaintext password with bcrypt at Config.BCRYPT_ROUNDS rounds."""
    salt = bcrypt.gensalt(rounds=Config.BCRYPT_ROUNDS)
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def is_valid_email(email: str) -> bool:
    """Lightweight email shape check; Supabase's unique constraint is authoritative."""
    return bool(EMAIL_RE.match(email or ""))


def is_valid_password(password: str) -> bool:
    """Minimum password strength: at least 8 characters."""
    return bool(password) and len(password) >= 8


def encode_token(user_id: str) -> str:
    """Create a signed JWT for a user, expiring after Config.JWT_EXPIRY_DAYS."""
    payload = {
        "sub": user_id,
        "iat": datetime.datetime.now(datetime.timezone.utc),
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(days=Config.JWT_EXPIRY_DAYS),
    }
    return jwt.encode(payload, Config.JWT_SECRET, algorithm=Config.JWT_ALGORITHM)


def extract_token() -> Optional[str]:
    """Pull the bearer token out of the Authorization header, if present."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    return auth_header[len("Bearer "):].strip()


def extract_user_id_unverified() -> Optional[str]:
    """Best-effort JWT subject extraction for rate-limit keying (no signature check).

    Used only to key the rate limiter per user; every route that actually
    trusts the identity still goes through require_auth's verified decode.
    """
    token = extract_token()
    if not token:
        return None
    try:
        payload = jwt.decode(token, options={"verify_signature": False})
        return payload.get("sub")
    except jwt.InvalidTokenError:
        return None


def require_auth(f: Callable) -> Callable:
    """Require a valid JWT; attach the current user row to flask.g.current_user.

    Per spec, both a missing/malformed token and an expired one return 401.
    """

    @wraps(f)
    def wrapper(*args, **kwargs):
        token = extract_token()
        if not token:
            return err("Authentication required.", 401)

        try:
            payload = jwt.decode(token, Config.JWT_SECRET, algorithms=[Config.JWT_ALGORITHM])
        except jwt.ExpiredSignatureError:
            return err("Session expired, please log in again.", 401)
        except jwt.InvalidTokenError:
            return err("Invalid authentication token.", 401)

        user = get_user_by_id(payload["sub"])
        if not user:
            return err("User not found.", 401)

        g.current_user = user
        return f(*args, **kwargs)

    return wrapper
