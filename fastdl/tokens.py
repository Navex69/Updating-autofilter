import base64
import hashlib
import hmac
import time


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(secret: str, payload: bytes) -> bytes:
    return hmac.new(secret.encode(), payload, hashlib.sha256).digest()[:16]


def make_token(secret: str, msg_id: int, user_id: int, ttl_seconds: int) -> str:
    """Self-contained signed token: the stream server can verify it without
    touching the database, so Oracle needs no DB access at all."""
    exp = int(time.time()) + int(ttl_seconds)
    payload = f"{msg_id}:{user_id}:{exp}".encode()
    return f"{_b64(payload)}.{_b64(_sign(secret, payload))}"


def verify_token(secret: str, token: str):
    """Returns (msg_id, user_id, expires_at) or None if forged/expired."""
    try:
        payload_part, sig_part = token.split(".", 1)
        payload = _unb64(payload_part)
        sig = _unb64(sig_part)
        if not hmac.compare_digest(sig, _sign(secret, payload)):
            return None
        msg_id, user_id, exp = (int(x) for x in payload.decode().split(":"))
    except Exception:
        return None
    if exp < time.time():
        return None
    return msg_id, user_id, exp
