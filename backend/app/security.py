import base64
import datetime
import hashlib
import hmac
import secrets

import jwt
from cryptography.fernet import Fernet, InvalidToken

from .config import get_settings

settings = get_settings()


# --- Passwords (stdlib scrypt, no extra dependency) ---

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64(salt)}${_b64(digest)}"


# Verified against when the username doesn't exist, so a wrong username takes
# as long as a wrong password and doesn't reveal which accounts exist.
_DUMMY_HASH = hash_password(secrets.token_hex(16))


def verify_password(password: str, stored: str | None) -> bool:
    if not stored:
        stored, ok = _DUMMY_HASH, False
    else:
        ok = True
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, base64.b64decode(digest)) and ok


# --- Session tokens ---

def create_access_token(user_id: int, token_version: int | None) -> str:
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {"sub": str(user_id), "tv": token_version or 0, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> tuple[int, int] | None:
    """Returns (user_id, token_version), or None if the token is invalid."""
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return int(payload["sub"]), int(payload.get("tv", 0))
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None


# --- Encryption at rest (Garmin sessions, AI keys) ---

def _fernet() -> Fernet:
    if not settings.fernet_key:
        raise RuntimeError(
            "FERNET_KEY is not set. Generate one with: "
            'python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    return Fernet(settings.fernet_key.encode("utf-8"))


def encrypt_text(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_text(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("Could not decrypt stored Garmin session; reconnect Garmin.") from exc
