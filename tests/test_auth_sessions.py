from tests._guard import refuse_real_database  # noqa: F401  (stops tests touching the real DB)
import time

import auth


def _use_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(auth, "SESSIONS_PATH", tmp_path / "sessions.json")


def test_session_survives_reload(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    token = auth.create_session()
    assert auth.session_is_valid(token)
    assert token not in (tmp_path / "sessions.json").read_text()  # only the hash is stored


def test_unknown_or_missing_token_rejected(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    auth.create_session()
    assert not auth.session_is_valid("not-a-real-token")
    assert not auth.session_is_valid(None)


def test_expired_session_rejected(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    token = auth.create_session()
    monkeypatch.setattr(time, "time", lambda: 10**12)
    assert not auth.session_is_valid(token)


def test_logout_ends_session(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    token = auth.create_session()
    auth.end_session(token)
    assert not auth.session_is_valid(token)



def test_session_lasts_15_minutes(monkeypatch, tmp_path):
    _use_tmp(monkeypatch, tmp_path)
    start = time.time()
    token = auth.create_session()
    assert auth.SESSION_MINUTES == 15
    monkeypatch.setattr(time, "time", lambda: start + 14 * 60 + 59)
    assert auth.session_is_valid(token)
    monkeypatch.setattr(time, "time", lambda: start + 15 * 60 + 1)
    assert not auth.session_is_valid(token)
