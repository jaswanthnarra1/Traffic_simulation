"""Authentication gate: login, session cookie, route protection, logout, and no password leakage."""
import logging
import time

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.api import app

GOOD = {"login_id": auth.LOGIN_ID, "password": "tgpolice"}
PROTECTED = ["/api/meta", "/api/network", "/api/traffic/current", "/api/incidents", "/api/scenarios",
             "/api/evaluation/forecast", "/api/candidates", "/api/forecast-map"]


@pytest.fixture()
def client():
    return TestClient(app)  # no lifespan: no model warm-up needed for auth tests


def test_correct_credentials_create_session(client):
    r = client.post("/api/auth/login", json=GOOD)
    assert r.status_code == 200
    assert r.json()["login_id"] == "tgpolice" and r.json()["display_name"]
    cookie = r.headers["set-cookie"].lower()
    assert auth.COOKIE in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/auth/me").json()["login_id"] == "tgpolice"  # session persists across requests (refresh)


@pytest.mark.parametrize("body", [{"login_id": "tgpolice", "password": "wrong"},
                                  {"login_id": "someone", "password": "tgpolice"},
                                  {"login_id": "TGPOLICE", "password": "TGPOLICE"}])
def test_wrong_credentials_rejected_with_generic_message(client, body):
    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 401
    assert r.json() == {"detail": "Invalid login ID or password."}  # never says which field was wrong
    assert "set-cookie" not in r.headers
    assert client.get("/api/auth/me").status_code == 401


@pytest.mark.parametrize("body", [{"login_id": "", "password": ""}, {"login_id": "tgpolice"}, {},
                                  {"login_id": 5, "password": ["x"]}, {"login_id": "a" * 500, "password": "secret-pw"}])
def test_empty_and_malformed_requests_rejected_without_echo(client, body):
    r = client.post("/api/auth/login", json=body)
    assert r.status_code == 422
    assert "secret-pw" not in r.text and "input" not in r.text


def test_non_json_body_rejected(client):
    r = client.post("/api/auth/login", content="login_id=tgpolice&password=tgpolice",
                    headers={"content-type": "application/x-www-form-urlencoded"})
    assert r.status_code == 422 and "tgpolice" not in r.text


def test_protected_routes_require_session(client):
    for path in PROTECTED:
        assert client.get(path).status_code == 401, path
    assert client.post("/api/simulation/run", json={"candidate_id": "PLAN0365"}).status_code == 401
    assert client.get("/api/health").status_code == 200  # liveness stays public
    client.post("/api/auth/login", json=GOOD)
    for path in PROTECTED:
        assert client.get(path).status_code == 200, path


def test_logout_destroys_session(client):
    client.post("/api/auth/login", json=GOOD)
    assert client.get("/api/meta").status_code == 200
    r = client.post("/api/auth/logout")
    assert r.status_code == 200 and auth.COOKIE in r.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/meta").status_code == 401


def test_tampered_and_expired_tokens_rejected():
    tok = auth.issue_token("tgpolice")
    assert auth.verify_token(tok)
    body, sig = tok.split(".")
    assert auth.verify_token(body + "." + sig[:-2] + "AA") is None                     # forged signature
    assert auth.verify_token(auth.issue_token("tgpolice", now=time.time() - auth.TTL_S - 5)) is None  # expired
    assert auth.verify_token("garbage") is None and auth.verify_token(None) is None
    c = TestClient(app)
    c.cookies.set(auth.COOKIE, body + ".forged")
    assert c.get("/api/meta").status_code == 401


def test_password_never_in_responses_or_logs(client, caplog, capsys):
    secret = "tgpolice"
    with caplog.at_level(logging.DEBUG):
        ok = client.post("/api/auth/login", json=GOOD)
        bad = client.post("/api/auth/login", json={"login_id": "x", "password": "not-the-password-123"})
        me = client.get("/api/auth/me")
    for r in (ok, me):
        assert "password" not in r.text.lower()
    assert "not-the-password-123" not in bad.text
    assert secret not in ok.headers["set-cookie"]  # the token is signed claims, not the password
    logged = caplog.text + capsys.readouterr().out
    assert "not-the-password-123" not in logged and '"password"' not in logged
