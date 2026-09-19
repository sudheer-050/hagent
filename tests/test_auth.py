import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from hagent import auth, web
from hagent.models import AuthToken, User

PASSWORD = "correct horse battery"
# A path no route serves: the auth gate answers 401/403 first, otherwise the app answers 404.
PROTECTED = "/api/nothing-here"


@pytest.fixture()
def maker(auth_db):
    return auth_db


@pytest.fixture()
def client():
    return TestClient(web.app)


def add_user(maker, username="sudheer", role="member"):
    with maker() as s:
        return auth.create_user(s, username, PASSWORD, role).id


def add_token(maker, user_id, kind="api", ttl_days=None):
    with maker() as s:
        secret, row = auth.issue_token(s, s.get(User, user_id), kind=kind, name="t", ttl_days=ttl_days)
        return secret, row.id


def bearer(secret):
    return {"Authorization": f"Bearer {secret}"}


# --- passwords and accounts -------------------------------------------------

def test_password_hash_verifies_and_is_not_plaintext():
    encoded = auth.hash_password(PASSWORD)

    assert PASSWORD not in encoded and encoded.startswith("scrypt$")
    assert auth.verify_password(PASSWORD, encoded)
    assert not auth.verify_password("wrong password!!", encoded)
    assert not auth.verify_password(PASSWORD, "garbage")
    assert auth.hash_password(PASSWORD) != encoded  # salted


def test_account_rules(maker):
    with maker() as s:
        first = auth.create_user(s, "Sudheer", PASSWORD, "member")
        assert first.role == "owner"  # the first account always owns the instance
        with pytest.raises(auth.AuthError, match="at least"):
            auth.create_user(s, "short", "tiny")
        with pytest.raises(auth.AuthError, match="already exists"):
            auth.create_user(s, "sudheer", PASSWORD)  # case-insensitive
        with pytest.raises(auth.AuthError, match="no spaces"):
            auth.create_user(s, "has space", PASSWORD)
        with pytest.raises(auth.AuthError, match="last owner"):
            auth.remove_user(s, "sudheer")


def test_only_the_hash_of_a_token_is_stored(maker):
    secret, _ = add_token(maker, add_user(maker))

    with maker() as s:
        row = s.query(AuthToken).one()
    assert secret.startswith("hag_") and secret not in (row.token_hash, row.prefix) and len(row.token_hash) == 64


# --- the gate: no accounts yet ---------------------------------------------

def test_without_accounts_only_direct_loopback_clients_are_served():
    assert TestClient(web.app).get(PROTECTED).status_code == 404  # let through to the app
    assert TestClient(web.app, client=("203.0.113.9", 5000)).get(PROTECTED).status_code == 403
    assert TestClient(web.app).get(PROTECTED, headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 403


def test_healthz_is_always_open(maker):
    add_user(maker)

    assert TestClient(web.app, client=("203.0.113.9", 5000)).get("/healthz").json() == {"status": "ok"}


# --- the gate: accounts exist ----------------------------------------------

def test_unauthenticated_requests_are_refused(maker, client):
    add_user(maker)

    api = client.get(PROTECTED)
    page = client.get("/agents", follow_redirects=False)

    assert api.status_code == 401 and api.headers["www-authenticate"] == "Bearer"
    assert page.status_code == 303 and page.headers["location"] == "/login?next=/agents"
    assert client.get(PROTECTED, headers=bearer("hag_not-a-real-token")).status_code == 401


def test_valid_bearer_token_passes_the_gate(maker, client):
    secret, _ = add_token(maker, add_user(maker))

    assert client.get(PROTECTED, headers=bearer(secret)).status_code == 404


def test_revoked_and_expired_tokens_are_refused(maker, client):
    user_id = add_user(maker)
    revoked, revoked_id = add_token(maker, user_id)
    expired, _ = add_token(maker, user_id, ttl_days=1)
    assert client.get(PROTECTED, headers=bearer(revoked)).status_code == 404

    with maker() as s:
        auth.revoke_token(s, revoked_id)
        row = s.query(AuthToken).filter(AuthToken.token_hash == auth._hash_secret(expired)).one()
        row.expires_at = row.expires_at.replace(year=2000)
        s.commit()
    auth.invalidate_caches()

    assert client.get(PROTECTED, headers=bearer(revoked)).status_code == 401
    assert client.get(PROTECTED, headers=bearer(expired)).status_code == 401


def test_worker_tokens_are_confined_to_worker_endpoints(maker, client):
    user_id = add_user(maker)
    worker, _ = add_token(maker, user_id, kind="worker")
    api, _ = add_token(maker, user_id, kind="api")

    assert client.get(PROTECTED, headers=bearer(worker)).status_code == 403
    assert client.get("/api/worker/whatever", headers=bearer(api)).status_code == 403


def test_webhook_urls_keep_working_without_login(maker, client, monkeypatch):
    add_user(maker)
    monkeypatch.setattr(web, "find_webhook_trigger", lambda token: None)

    response = client.post("/webhooks/some-token")

    assert response.status_code == 404 and response.json() == {"error": "unknown webhook"}


def test_websockets_need_authentication(maker, client):
    add_user(maker)

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/anything"):
            pass


# --- login, logout, sessions -----------------------------------------------

def login(client, username="sudheer", password=PASSWORD, next_url="/"):
    return client.post("/login", data={"username": username, "password": password, "next": next_url}, follow_redirects=False)


def test_login_sets_a_hardened_session_cookie_that_grants_access(maker, client):
    add_user(maker)

    response = login(client, next_url="/agents")
    cookie = response.headers["set-cookie"].lower()

    assert response.status_code == 303 and response.headers["location"] == "/agents"
    assert "httponly" in cookie and "samesite=strict" in cookie
    assert client.get(PROTECTED).status_code == 404  # the cookie now authenticates


def test_wrong_password_and_unknown_user_look_the_same(maker, client):
    add_user(maker)

    wrong = login(client, password="not the password")
    unknown = login(client, username="nobody")

    assert wrong.status_code == unknown.status_code == 401
    assert "Incorrect username or password" in wrong.text and "Incorrect username or password" in unknown.text
    assert "set-cookie" not in wrong.headers


def test_login_is_throttled_after_repeated_failures(maker, client):
    add_user(maker)
    for _ in range(auth.MAX_FAILURES):
        assert login(client, password="nope nope nope").status_code == 401

    assert login(client).status_code == 429  # even the right password is refused while blocked


@pytest.mark.parametrize("target", ["//evil.example", "https://evil.example", "/\\evil.example"])
def test_login_never_redirects_off_site(maker, client, target):
    add_user(maker)

    assert login(client, next_url=target).headers["location"] == "/"


def test_logout_revokes_the_session(maker, client):
    add_user(maker)
    login(client)
    assert client.get(PROTECTED).status_code == 404

    out = client.post("/logout", follow_redirects=False)

    assert out.status_code == 303 and out.headers["location"] == "/login"
    assert client.get(PROTECTED).status_code == 401


def test_changing_a_password_signs_out_browser_sessions_but_not_api_tokens(maker, client):
    user_id = add_user(maker)
    token, _ = add_token(maker, user_id)
    login(client)

    with maker() as s:
        auth.set_password(s, "sudheer", "a brand new passphrase")

    assert client.get(PROTECTED).status_code == 401
    assert TestClient(web.app).get(PROTECTED, headers=bearer(token)).status_code == 404
    assert login(client, password="a brand new passphrase").status_code == 303


def test_cross_origin_posts_with_a_session_cookie_are_refused(maker, client):
    add_user(maker)
    login(client)

    evil = client.post(PROTECTED, headers={"Origin": "https://evil.example"})
    same = client.post(PROTECTED, headers={"Origin": "http://testserver"})

    assert evil.status_code == 403 and same.status_code == 404
