"""Lightweight local password gate for the Streamlit dashboard.

This is defense-in-depth, not the primary protection — the primary
protection is that the server only ever binds to 127.0.0.1 (see
.streamlit/config.toml) and is unreachable off this machine. The password
just means someone else briefly at your keyboard/unlocked session can't open
the dashboard without it.
"""

import hashlib
import hmac
import json
import os
from pathlib import Path

SECRETS_PATH = Path(__file__).resolve().parent / "secrets.local.json"
PBKDF2_ITERATIONS = 200_000


def is_configured() -> bool:
    return SECRETS_PATH.exists()


def set_password(password: str):
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    SECRETS_PATH.write_text(json.dumps({"salt": salt.hex(), "hash": digest.hex()}))
    os.chmod(SECRETS_PATH, 0o600)


def verify_password(password: str) -> bool:
    if not is_configured():
        return False
    data = json.loads(SECRETS_PATH.read_text())
    salt = bytes.fromhex(data["salt"])
    expected = bytes.fromhex(data["hash"])
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(expected, actual)


# --- Remembered sessions -----------------------------------------------------
# Streamlit's session_state is lost on every page refresh, new tab, dropped
# websocket or server restart, which logged the user out constantly. A login
# therefore issues a random token that the browser keeps in a session cookie
# (COOKIE_NAME, SameSite=Strict, path=/) — never in the page URL, so it does
# not end up in browser history, bookmarks or the address bar during screen
# sharing. Only a SHA-256 hash of each token is stored, locally in
# data/sessions.json (0600), with an expiry. A login lasts at most
# SESSION_MINUTES (absolute); the page also logs out after IDLE_SECONDS
# without mouse/keyboard/scroll activity.
import secrets
import time

SESSIONS_PATH = Path(__file__).resolve().parent / "data" / "sessions.json"
SESSION_MINUTES = 15
IDLE_SECONDS = 30
COOKIE_NAME = "invoice_session"


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _load_sessions() -> dict:
    try:
        sessions = json.loads(SESSIONS_PATH.read_text())
    except (FileNotFoundError, ValueError):
        return {}
    now = time.time()
    return {h: exp for h, exp in sessions.items() if exp > now}


def _save_sessions(sessions: dict):
    SESSIONS_PATH.parent.mkdir(exist_ok=True)
    SESSIONS_PATH.write_text(json.dumps(sessions))
    os.chmod(SESSIONS_PATH, 0o600)


def create_session() -> str:
    token = secrets.token_urlsafe(32)
    sessions = _load_sessions()
    sessions[_hash_token(token)] = time.time() + SESSION_MINUTES * 60
    _save_sessions(sessions)
    return token


def session_is_valid(token) -> bool:
    if not token:
        return False
    return _hash_token(token) in _load_sessions()


def end_session(token):
    sessions = _load_sessions()
    if token and sessions.pop(_hash_token(token), None) is not None:
        _save_sessions(sessions)


def session_expires_at(token):
    """Unix time the session ends, or None if the token isn't valid."""
    if not token:
        return None
    return _load_sessions().get(_hash_token(token))


def token_from_cookies(cookies) -> str | None:
    """The session token the browser sent, if any (st.context.cookies)."""
    try:
        token = cookies.get(COOKIE_NAME)
    except Exception:
        return None
    return token or None
