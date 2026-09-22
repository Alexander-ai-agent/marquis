"""Auth endpoints: login, signup, missing fields, and JWT expiration."""
import pytest

LOGIN_URL = "/api/v1/marquis/auth/login"
SIGNUP_URL = "/api/v1/marquis/auth/signup"
PROFILE_URL = "/api/v1/marquis/user/profile"


# --- test_auth_login ---------------------------------------------------

def test_login_valid_credentials(client, make_user):
    user, password = make_user(email="valid@example.com", password="hunter22x", name="Val Founder")

    resp = client.post(LOGIN_URL, json={"email": "valid@example.com", "password": password})

    assert resp.status_code == 200
    body = resp.get_json()
    assert body["user_id"] == user["id"]
    assert body["name"] == "Val Founder"
    assert isinstance(body["token"], str) and body["token"]


def test_login_wrong_password(client, make_user):
    make_user(email="wrongpw@example.com", password="the-real-password")

    resp = client.post(LOGIN_URL, json={"email": "wrongpw@example.com", "password": "not-it"})

    assert resp.status_code == 401
    assert "error" in resp.get_json()


def test_login_unknown_email(client, fake_db):
    resp = client.post(LOGIN_URL, json={"email": "nobody@example.com", "password": "whatever1"})

    assert resp.status_code == 401


def test_login_returns_503_when_supabase_unreachable(client, fake_db, monkeypatch):
    """Regression test for the deployed hang bug: a Supabase failure must
    surface as a fast, clean error — never an unhandled exception that
    leaves the client hanging."""
    import app as app_module

    def raise_timeout(email):
        raise TimeoutError("simulated Supabase timeout")

    monkeypatch.setattr(app_module, "get_user_by_email", raise_timeout)

    resp = client.post(LOGIN_URL, json={"email": "anyone@example.com", "password": "whatever1"})

    assert resp.status_code == 503
    assert "error" in resp.get_json()


def test_signup_returns_503_when_supabase_unreachable(client, fake_db, monkeypatch):
    import app as app_module

    def raise_timeout(email):
        raise TimeoutError("simulated Supabase timeout")

    monkeypatch.setattr(app_module, "get_user_by_email", raise_timeout)

    resp = client.post(
        SIGNUP_URL, json={"email": "anyone@example.com", "password": "validpass1", "name": "X"}
    )

    assert resp.status_code == 503
    assert "error" in resp.get_json()


# --- test_auth_signup ----------------------------------------------------

def test_signup_valid(client, fake_db):
    resp = client.post(
        SIGNUP_URL,
        json={"email": "new@example.com", "password": "brand-new-pw", "name": "New Founder"},
    )

    assert resp.status_code == 201
    body = resp.get_json()
    assert isinstance(body["token"], str) and body["token"]
    assert body["user_id"]
    assert fake_db.get_user_by_email("new@example.com") is not None
    # Signup should log a user_signup activity event (weekly digest depends on it).
    logs = [a for a in fake_db.activity_logs if a["event_type"] == "user_signup"]
    assert len(logs) == 1


def test_signup_duplicate_email(client, make_user):
    make_user(email="dup@example.com")

    resp = client.post(
        SIGNUP_URL,
        json={"email": "dup@example.com", "password": "another-pw1", "name": "Someone Else"},
    )

    assert resp.status_code == 400
    assert "error" in resp.get_json()


def test_signup_invalid_email_format(client, fake_db):
    resp = client.post(
        SIGNUP_URL, json={"email": "not-an-email", "password": "validpass1", "name": "X"}
    )
    assert resp.status_code == 400


def test_signup_weak_password(client, fake_db):
    resp = client.post(
        SIGNUP_URL, json={"email": "weak@example.com", "password": "short", "name": "X"}
    )
    assert resp.status_code == 400


# --- test_missing_fields --------------------------------------------------

@pytest.mark.parametrize("payload", [{}, {"email": "only@example.com"}, {"password": "onlypw123"}])
def test_login_missing_fields(client, payload):
    resp = client.post(LOGIN_URL, json=payload)
    assert resp.status_code == 400
    assert "error" in resp.get_json()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"email": "a@example.com", "password": "somepassword1"},  # missing name
        {"email": "a@example.com", "name": "A"},  # missing password
        {"password": "somepassword1", "name": "A"},  # missing email
    ],
)
def test_signup_missing_fields(client, payload):
    resp = client.post(SIGNUP_URL, json=payload)
    assert resp.status_code == 400
    assert "error" in resp.get_json()


# --- test_jwt_expiration ---------------------------------------------------

def test_expired_token_returns_401(client, make_user, make_expired_token):
    user, _ = make_user()
    token = make_expired_token(user["id"])

    resp = client.get(PROFILE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
    assert "error" in resp.get_json()


def test_missing_auth_header_returns_401(client):
    resp = client.get(PROFILE_URL)
    assert resp.status_code == 401


def test_malformed_token_returns_401(client):
    resp = client.get(PROFILE_URL, headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_token_for_deleted_user_returns_401(client, make_user, fake_db):
    user, _ = make_user()
    from auth_utils import encode_token

    token = encode_token(user["id"])
    del fake_db.users[user["id"]]

    resp = client.get(PROFILE_URL, headers={"Authorization": f"Bearer {token}"})

    assert resp.status_code == 401
