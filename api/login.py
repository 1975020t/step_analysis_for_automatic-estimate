"""Sign-in with Amazon Cognito: who the user is and which company they belong to, from their ID token.

The screens sign in on Cognito's own sign-in pages (authorization code with PKCE, web/src/auth.ts) and send the ID
token with every call (`Authorization: Bearer <ID token>`). Here the token is checked: its signature against the
keys the user pool publishes (`<issuer>/.well-known/jwks.json`, kept and fetched again for an unknown key), the
issuer, the app client (`aud`), that it is an ID token, and that it has not expired. The company is the user's
`custom:tenant_id` (COGNITO_TENANT_CLAIM), the name shown and written to the histories is `name` (else the e-mail).
Passwords never reach this server.
"""
from __future__ import annotations

import json
import threading
import urllib.request
from dataclasses import dataclass
from typing import Callable

import jwt

from src.services.settings import Settings
from src.tenancy import check_tenant

LEEWAY_S = 30  # clocks of the user pool and of this server may differ a little


@dataclass(frozen=True)
class User:
    sub: str
    name: str
    email: str
    tenant: str | None          # None: the user has no company yet (refused with 403)


class LoginError(Exception):
    """The token is not a valid sign-in of this user pool and app client."""


def fetch_keys(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 - the user pool's https URL
        return json.load(response)


class CognitoVerifier:
    def __init__(self, settings: Settings, keys: Callable[[str], dict] = fetch_keys) -> None:
        self.issuer = settings.login_issuer
        self.client_id = settings.cognito_client_id
        self.tenant_claim = settings.cognito_tenant_claim
        self._fetch = keys
        self._keys: dict[str, jwt.PyJWK] = {}
        self._lock = threading.Lock()

    def _key(self, kid: str) -> jwt.PyJWK:
        with self._lock:
            if kid not in self._keys:  # first use, or the user pool rotated its keys
                jwks = self._fetch(f"{self.issuer}/.well-known/jwks.json")
                self._keys = {k["kid"]: jwt.PyJWK(k) for k in jwks.get("keys", []) if k.get("kid")}
            if kid not in self._keys:
                raise LoginError("unknown key")
            return self._keys[kid]

    def verify(self, token: str) -> User:
        try:
            kid = jwt.get_unverified_header(token).get("kid", "")
            claims = jwt.decode(token, self._key(kid), algorithms=["RS256"], audience=self.client_id,
                                issuer=self.issuer, leeway=LEEWAY_S,
                                options={"require": ["exp", "iat", "iss", "aud", "sub", "token_use"]})
        except (jwt.PyJWTError, LoginError, OSError, ValueError) as exc:
            raise LoginError(str(exc)) from None
        if claims.get("token_use") != "id":
            raise LoginError("not an ID token")
        tenant = claims.get(self.tenant_claim) or None
        if tenant is not None:
            try:
                tenant = check_tenant(str(tenant))
            except ValueError:
                tenant = None
        email = str(claims.get("email") or "")
        name = str(claims.get("name") or email or claims.get("cognito:username") or claims["sub"])[:80]
        return User(sub=str(claims["sub"]), name=name, email=email, tenant=tenant)


def looks_like_jwt(token: str) -> bool:
    return token.count(".") == 2 and token.startswith("eyJ")
