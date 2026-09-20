"""Login-ID + password gate with a signed session cookie (standard library only).

- Credentials come from FLOWSENSE_LOGIN_ID / FLOWSENSE_LOGIN_PASSWORD (demo defaults: tgpolice / tgpolice).
- On success the server sets an HttpOnly cookie "fs_session" = base64(payload).HMAC-SHA256(payload).
  JavaScript never sees the token, and the password never leaves this module.
- Every /api/* route except health and /api/auth/* requires a valid, unexpired cookie (see api.py middleware).

ponytail: a single shared demo account, no rate limiting, and stateless tokens (logout clears the cookie;
a copied token stays valid until it expires). Fine for a closed demo; upgrade path for real use =
per-user accounts with hashed passwords, server-side session store / revocation list, login throttling.
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time

COOKIE = "fs_session"
TTL_S = int(os.getenv("FLOWSENSE_SESSION_TTL_HOURS", "12")) * 3600
LOGIN_ID = os.getenv("FLOWSENSE_LOGIN_ID", "tgpolice")
_PASSWORD = os.getenv("FLOWSENSE_LOGIN_PASSWORD", "tgpolice")
DISPLAY_NAME = os.getenv("FLOWSENSE_DISPLAY_NAME", "TG Police")
# Without a configured secret, a random one is generated per process: sessions end when the API restarts.
_SECRET = (os.getenv("FLOWSENSE_SESSION_SECRET") or secrets.token_hex(32)).encode()
COOKIE_SECURE = os.getenv("FLOWSENSE_COOKIE_SECURE", "false").lower() == "true"  # set true behind HTTPS


def check_credentials(login_id: str, password: str) -> bool:
    """Constant-time comparison of both fields; never says which one was wrong."""
    ok_id = hmac.compare_digest(login_id.encode(), LOGIN_ID.encode())
    ok_pw = hmac.compare_digest(password.encode(), _PASSWORD.encode())
    return ok_id and ok_pw


def _sign(body: bytes) -> str:
    return base64.urlsafe_b64encode(hmac.new(_SECRET, body, hashlib.sha256).digest()).decode().rstrip("=")


_REVOKED: set[str] = set()  # ponytail: in-memory, per process, grows until restart. Upgrade: shared store (Redis) with expiry


def revoke(token: str | None) -> None:
    """Logout: a revoked token is refused even if someone kept a copy of the cookie."""
    if token:
        _REVOKED.add(token)


def issue_token(login_id: str, now: float | None = None) -> str:
    payload = {"sub": login_id, "exp": int((now or time.time()) + TTL_S), "jti": secrets.token_hex(8)}   # jti: a fresh login is never equal to a revoked token
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{body}.{_sign(body.encode())}"


def verify_token(token: str | None, now: float | None = None) -> dict | None:
    """Return the payload if the signature is valid and the token is unexpired, else None."""
    if not token or token in _REVOKED or token.count(".") != 1:
        return None
    body, sig = token.split(".")
    if not hmac.compare_digest(sig, _sign(body.encode())):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("exp", 0) < (now or time.time()) or payload.get("sub") != LOGIN_ID:
        return None
    return payload


def user(payload: dict) -> dict:
    return {"login_id": payload["sub"], "display_name": DISPLAY_NAME, "expires_at": payload["exp"]}
