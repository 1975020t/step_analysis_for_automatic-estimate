"""Signing in from the screens, in a real browser (Playwright, Chromium), against the API serving the built screens.

No AWS: a stand-in user pool. Its key set is served here (COGNITO_ISSUER points at it), and the browser's visits to
the sign-in pages (COGNITO_DOMAIN) are answered by the test: the sign-in page sends the browser back with a code, the
token endpoint checks the PKCE verifier and hands out an ID token signed with the stand-in's key.
Needs the built screens (cd web && npm ci && npm run build); skipped without them.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.test_e2e_flow import DIST, ROOT, free_port, launch

pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists(), reason="web/dist がありません（cd web && npm run build）")

CLIENT, DOMAIN = "client-e2e", "https://login.example.com"


@pytest.fixture
def user_pool():
    """The stand-in user pool's key set, at http://127.0.0.1:<port>/pool/.well-known/jwks.json."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwks = json.dumps({"keys": [{**public, "kid": "k1", "alg": "RS256", "use": "sig"}]}).encode()

    class Keys(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            ok = self.path == "/pool/.well-known/jwks.json"
            self.send_response(200 if ok else 404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(jwks if ok else b"{}")

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Keys)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield key, f"http://127.0.0.1:{httpd.server_address[1]}/pool"
    httpd.shutdown()


@pytest.fixture
def server(tmp_path, _history_copy, user_pool):
    port = free_port()
    env = {**os.environ, "ESTIMATE_STORAGE_DIR": str(tmp_path / "storage"), "PAST_QUOTES_PATH": str(_history_copy),
           "QUOTE_LOG_PATH": str(tmp_path / "quote_log.csv"), "ANALYSIS_ANTHROPIC_API_KEY": "", "ANTHROPIC_API_KEY": "",
           "DATABASE_URL": "", "COGNITO_USER_POOL_ID": "ap-northeast-1_E2E", "COGNITO_CLIENT_ID": CLIENT,
           "COGNITO_DOMAIN": DOMAIN, "COGNITO_ISSUER": user_pool[1], "NO_PROXY": "127.0.0.1,localhost",
           "no_proxy": "127.0.0.1,localhost"}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.main:app", "--port", str(port)], cwd=ROOT, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(300):
        try:
            urllib.request.urlopen(base + "/api/health", timeout=1)
            break
        except OSError:
            time.sleep(0.2)
    yield base
    proc.terminate()
    proc.wait(timeout=20)


def test_sign_in_work_as_the_user_and_sign_out(server, user_pool):
    from playwright.sync_api import expect, sync_playwright

    key, issuer = user_pool
    pending: dict[str, str] = {}
    seen: dict[str, list] = {"token": [], "logout": []}

    def authorize(route):
        q = parse_qs(urlparse(route.request.url).query)
        assert q["client_id"] == [CLIENT] and q["code_challenge_method"] == ["S256"]
        assert q["redirect_uri"] == [f"{server}/auth/callback"]
        pending["challenge"] = q["code_challenge"][0]
        back = f"{server}/auth/callback?" + urlencode({"code": "code-1", "state": q["state"][0]})
        route.fulfill(status=200, content_type="text/html", body=f"<script>location.replace({json.dumps(back)})</script>")

    def token(route):
        form = parse_qs(route.request.post_data or "")
        seen["token"].append(form)
        verifier = form["code_verifier"][0].encode()
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier).digest()).rstrip(b"=").decode()
        assert form["grant_type"] == ["authorization_code"] and form["code"] == ["code-1"]
        assert challenge == pending["challenge"]  # PKCE: only the browser that started the sign-in can finish it
        now = int(time.time())
        id_token = jwt.encode({"sub": "u-1", "aud": CLIENT, "iss": issuer, "token_use": "id", "iat": now, "exp": now + 3600,
                               "name": "山田 太郎", "email": "yamada@alpha.example", "custom:tenant_id": "alpha"},
                              key, algorithm="RS256", headers={"kid": "k1"})
        route.fulfill(status=200, content_type="application/json", headers={"Access-Control-Allow-Origin": "*"},
                      body=json.dumps({"id_token": id_token, "refresh_token": "r-1", "expires_in": 3600}))

    def signed_out(route):
        seen["logout"].append(parse_qs(urlparse(route.request.url).query))
        route.fulfill(status=200, content_type="text/html", body="<p>signed out</p>")

    with sync_playwright() as p:
        browser = launch(p)
        page = browser.new_page(viewport={"width": 1440, "height": 950}, locale="ja-JP")
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.route(f"{DOMAIN}/oauth2/authorize*", authorize)
        page.route(f"{DOMAIN}/oauth2/token", token)
        page.route(f"{DOMAIN}/logout*", signed_out)
        expect.set_options(timeout=60_000)

        page.goto(server + "/drawings")
        page.get_by_role("button", name="ログイン").click()
        # back from the sign-in page, on the page the user asked for, as the signed-in user
        expect(page.get_by_role("heading", name="図面", exact=True)).to_be_visible()
        assert urlparse(page.url).path == "/drawings"
        expect(page.locator(".side .who")).to_contain_text("ログイン中：山田 太郎")
        page.goto(server + "/settings/staff")
        expect(page.get_by_text("ログインしている人の名前が残ります")).to_be_visible()
        expect(page.get_by_label("操作する担当者")).to_have_count(0)

        page.get_by_role("button", name="ログアウト").click()
        expect(page.get_by_text("signed out")).to_be_visible()
        assert seen["logout"] == [{"client_id": [CLIENT], "logout_uri": [f"{server}/"]}]
        page.goto(server + "/")
        expect(page.get_by_role("button", name="ログイン")).to_be_visible()
        assert len(seen["token"]) == 1 and not errors
        browser.close()

    # the API refuses calls without the sign-in
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(server + "/api/drawings")
    assert refused.value.code == 401
