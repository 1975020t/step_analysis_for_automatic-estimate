"""Sign-in with Amazon Cognito: the ID token decides the user and the company. No AWS call: the tests make their own
signing keys and tokens and hand the public key to the API in place of the user pool's key set."""
from __future__ import annotations

import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

import tests.test_platform as platform_tests
from api.main import create_app
from src.services.settings import Settings

POOL, CLIENT = "ap-northeast-1_TestPool", "client-123"
ISSUER = f"https://cognito-idp.ap-northeast-1.amazonaws.com/{POOL}"


class UserPool:
    """Stands in for the user pool: its signing key (rotated on demand) and the ID tokens it would issue."""

    def __init__(self) -> None:
        self.fetches = 0
        self.rotate()

    def rotate(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = f"kid-{time.monotonic_ns()}"

    def keys(self, url: str) -> dict:
        assert url == f"{ISSUER}/.well-known/jwks.json"
        self.fetches += 1
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key(), as_dict=True)
        return {"keys": [{**public, "kid": self.kid, "alg": "RS256", "use": "sig"}]}

    def token(self, key=None, **changes) -> str:
        now = int(time.time())
        claims = {"sub": "u-1", "aud": CLIENT, "iss": ISSUER, "token_use": "id", "iat": now, "exp": now + 3600,
                  "auth_time": now, "email": "yamada@alpha.example", "name": "山田 太郎", "custom:tenant_id": "alpha",
                  "cognito:username": "yamada"}
        claims.update(changes)
        claims = {k: v for k, v in claims.items() if v is not None}
        return jwt.encode(claims, key or self.key, algorithm="RS256", headers={"kid": self.kid})


@pytest.fixture
def pool():
    return UserPool()


def login_settings(tmp_path, history, **changes) -> Settings:
    storage = tmp_path / "storage"
    values = dict(data_dir=platform_tests.ROOT / "data", storage_dir=storage, history_path=history,
                  quote_log_path=storage / "quote_log.csv", cognito_user_pool_id=POOL, cognito_client_id=CLIENT,
                  cognito_domain="https://login.example.com")
    values.update(changes)
    return Settings(**values)


@pytest.fixture
def client(tmp_path, _history_copy, pool):
    app = create_app(login_settings(tmp_path, _history_copy, tenant_tokens="beta=token-b"),
                     reader_factory=lambda: platform_tests.RecordedReader(platform_tests.RECORDED), login_keys=pool.keys)
    with TestClient(app) as c:
        yield c


def as_user(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_the_screens_learn_how_to_sign_in(client, tmp_path, _history_copy):
    config = client.get("/api/auth/config").json()
    assert config == {"mode": "login", "client_id": CLIENT, "scopes": "openid email profile",
                      "authorize_url": "https://login.example.com/oauth2/authorize",
                      "token_url": "https://login.example.com/oauth2/token", "logout_url": "https://login.example.com/logout"}
    with TestClient(create_app(Settings(data_dir=platform_tests.ROOT / "data", storage_dir=tmp_path / "open",
                                        history_path=_history_copy))) as open_client:
        assert open_client.get("/api/auth/config").json() == {"mode": "open"}


def test_a_signed_in_user_works_in_their_company(client, pool):
    assert client.get("/api/drawings").status_code == 401
    assert client.get("/api/drawings").json()["error"]["message"] == "ログインが必要です。"
    user = as_user(pool.token())
    assert client.get("/api/me", headers=user).json() == {"name": "山田 太郎", "email": "yamada@alpha.example",
                                                          "tenant": "alpha", "signed_in": True}
    client.headers.update(user)
    drawing_id = platform_tests.register(client, "LOGIN-001")
    assert [d["drawing_no"] for d in client.get("/api/drawings").json()] == ["LOGIN-001"]
    # the API token of another company still works for scripts, and sees nothing of alpha
    other = client.get("/api/drawings", headers={"Authorization": "Bearer token-b"})
    assert other.status_code == 200 and other.json() == []
    assert client.get(f"/api/drawings/{drawing_id}", headers={"Authorization": "Bearer token-b"}).status_code == 404


def test_the_actor_is_the_signed_in_user_not_the_header(client, pool):
    client.headers.update(as_user(pool.token()))
    q = platform_tests.estimate(client, platform_tests.register(client, "LOGIN-ACT"), material="SPCC")
    r = client.put(f"/api/estimates/{q['id']}", json={"version": q["version"], "inputs": {"quantity": 5}},
                   headers={"X-Actor": "someone-else"})
    assert r.status_code == 200
    log = client.get(f"/api/estimates/{q['id']}").json()["edit_log"]
    assert log and {e["actor"] for e in log} == {"山田 太郎"}


@pytest.mark.parametrize("change", [
    {"exp": int(time.time()) - 3600},          # expired
    {"aud": "another-client"},                 # another app
    {"iss": "https://cognito-idp.ap-northeast-1.amazonaws.com/ap-northeast-1_Other"},  # another user pool
    {"token_use": "access"},                   # an access token, not an ID token
    {"sub": None},                             # incomplete
])
def test_tokens_that_are_not_a_valid_sign_in_are_refused(client, pool, change):
    assert client.get("/api/drawings", headers=as_user(pool.token(**change))).status_code == 401


def test_a_forged_or_altered_token_is_refused(client, pool):
    forged = pool.token(key=rsa.generate_private_key(public_exponent=65537, key_size=2048))
    assert client.get("/api/drawings", headers=as_user(forged)).status_code == 401
    head, body, sig = pool.token().split(".")
    other_body = pool.token(**{"custom:tenant_id": "beta"}).split(".")[1]
    assert client.get("/api/drawings", headers=as_user(f"{head}.{other_body}.{sig}")).status_code == 401
    assert client.get("/api/drawings", headers=as_user("eyJ.not.a-token")).status_code == 401


def test_a_user_without_a_company_is_told_so(client, pool):
    for tenant in (None, "Not A Valid Id"):
        r = client.get("/api/drawings", headers=as_user(pool.token(**{"custom:tenant_id": tenant})))
        assert r.status_code == 403 and r.json()["error"]["code"] == "NO_TENANT"


def test_the_keys_are_kept_and_fetched_again_when_the_pool_rotates_them(client, pool):
    for _ in range(3):
        assert client.get("/api/me", headers=as_user(pool.token())).status_code == 200
    assert pool.fetches == 1
    pool.rotate()
    assert client.get("/api/me", headers=as_user(pool.token())).status_code == 200
    assert pool.fetches == 2


def test_the_settings_read_the_user_pool(tmp_path):
    s = Settings.from_env({"COGNITO_USER_POOL_ID": POOL, "COGNITO_CLIENT_ID": CLIENT,
                           "COGNITO_DOMAIN": "https://login.example.com/"})
    assert s.login and s.login_issuer == ISSUER and s.cognito_domain == "https://login.example.com"
    assert s.cognito_tenant_claim == "custom:tenant_id"
    assert not Settings.from_env({"COGNITO_USER_POOL_ID": POOL}).login  # the app client is needed too
