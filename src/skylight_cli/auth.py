"""Log in to Skylight using the OAuth2 authorization-code + PKCE flow.

Skylight retired the old ``/api/sessions`` email-and-password endpoint, so the
only way in is to replay the same browser login the official apps use:

1. ``GET /oauth/authorize`` returns the hosted login page, which carries a Rails
   ``authenticity_token`` (CSRF) in a hidden input.
2. ``POST /auth/session`` with that token plus the credentials. On success the
   server redirects, via a hop or two, to ``skylight-family://welcome?code=...``.
3. ``POST /oauth/token`` trades the code and the PKCE verifier for a bearer token.

Nothing here talks to a browser; it is a plain HTTP replay.
"""

from __future__ import annotations

import base64
import hashlib
import re
import secrets
import time
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

from .constants import (
    BASE_URL,
    BROWSER_USER_AGENT,
    DEFAULT_TIMEOUT,
    OAUTH_CLIENT_ID,
    OAUTH_CODE_CHALLENGE_METHOD,
    OAUTH_REDIRECT_URI,
    OAUTH_SCOPE,
)
from .errors import SkylightAuthError, SkylightError

_CSRF_RE = re.compile(r'name="authenticity_token"[^>]*value="([^"]+)"')
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MAX_HOPS = 10


@dataclass
class Credentials:
    """A bearer token and its refresh token, as returned by ``/oauth/token``."""

    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None

    @property
    def authorization(self) -> str:
        return f"Bearer {self.access_token}"

    def is_expired(self, *, now: float | None = None, leeway: float = 60.0) -> bool:
        """True when the access token has expired, or is about to."""
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) >= self.expires_at - leeway


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _absolute(location: str) -> str:
    return location if location.startswith("http") else BASE_URL + location


def _credentials_from_payload(payload: dict) -> Credentials:
    access_token = payload.get("access_token")
    if not access_token:
        raise SkylightAuthError("Token response contained no access_token")
    created_at = payload.get("created_at")
    expires_in = payload.get("expires_in")
    expires_at = None
    if isinstance(created_at, int | float) and isinstance(expires_in, int | float):
        expires_at = float(created_at) + float(expires_in)
    refresh_token = payload.get("refresh_token")
    return Credentials(
        access_token=str(access_token),
        refresh_token=str(refresh_token) if refresh_token else None,
        expires_at=expires_at,
    )


def _follow(client: httpx.Client, response: httpx.Response) -> httpx.Response:
    """Chase redirects until a real page, stopping at the app's custom scheme."""
    hops = 0
    while (
        response.status_code in _REDIRECT_STATUSES
        and (location := response.headers.get("location"))
        and not location.startswith("skylight-family:")
        and hops < _MAX_HOPS
    ):
        response = client.get(
            _absolute(location),
            headers={"User-Agent": BROWSER_USER_AGENT},
            follow_redirects=False,
        )
        hops += 1
    return response


def _final_location(client: httpx.Client, response: httpx.Response) -> str | None:
    """Chase redirects after the login POST and return the ``skylight-family:`` URL."""
    location = response.headers.get("location")
    hops = 0
    while location and not location.startswith("skylight-family:") and hops < _MAX_HOPS:
        response = client.get(
            _absolute(location),
            headers={"User-Agent": BROWSER_USER_AGENT},
            follow_redirects=False,
        )
        location = response.headers.get("location")
        hops += 1
    return location


def login(
    email: str,
    password: str,
    *,
    http: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Credentials:
    """Exchange an email and password for a bearer token."""
    client = http or httpx.Client(timeout=timeout)
    try:
        verifier = _b64url(secrets.token_bytes(32))
        challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
        state = _b64url(secrets.token_bytes(18))

        try:
            page = client.get(
                f"{BASE_URL}/oauth/authorize",
                params={
                    "response_type": "code",
                    "client_id": OAUTH_CLIENT_ID,
                    "redirect_uri": OAUTH_REDIRECT_URI,
                    "scope": OAUTH_SCOPE,
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": OAUTH_CODE_CHALLENGE_METHOD,
                    "prompt": "login",
                },
                headers={"User-Agent": BROWSER_USER_AGENT},
                follow_redirects=False,
            )
            csrf = _CSRF_RE.search(_follow(client, page).text)
            if not csrf:
                raise SkylightAuthError(
                    "Could not find the CSRF token on Skylight's login page. "
                    "Their login flow has probably changed."
                )

            submitted = client.post(
                f"{BASE_URL}/auth/session",
                data={
                    "authenticity_token": csrf.group(1),
                    "email": email,
                    "password": password,
                },
                headers={"User-Agent": BROWSER_USER_AGENT},
                follow_redirects=False,
            )
            location = _final_location(client, submitted)
            if not (location and location.startswith("skylight-family:")):
                raise SkylightAuthError("Skylight rejected that email and password")

            query = parse_qs(urlparse(location).query)
            if query.get("state", [""])[0] != state:
                raise SkylightAuthError("OAuth state mismatch; aborting login")
            code = query.get("code", [""])[0]
            if not code:
                raise SkylightAuthError("Skylight returned no authorization code")

            token = client.post(
                f"{BASE_URL}/oauth/token",
                data={
                    "grant_type": "authorization_code",
                    "client_id": OAUTH_CLIENT_ID,
                    "code": code,
                    "redirect_uri": OAUTH_REDIRECT_URI,
                    "code_verifier": verifier,
                },
                headers={"User-Agent": BROWSER_USER_AGENT},
            )
        except httpx.HTTPError as exc:
            raise SkylightError(f"Network error during login: {exc}") from exc

        if token.status_code >= 400:
            raise SkylightAuthError(f"Token exchange failed (HTTP {token.status_code})")
        try:
            return _credentials_from_payload(token.json())
        except ValueError as exc:
            raise SkylightAuthError("Token response was not valid JSON") from exc
    finally:
        if http is None:
            client.close()


def refresh(
    refresh_token: str,
    *,
    http: httpx.Client | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Credentials:
    """Trade a refresh token for a fresh access token."""
    client = http or httpx.Client(timeout=timeout)
    try:
        try:
            token = client.post(
                f"{BASE_URL}/oauth/token",
                data={
                    "grant_type": "refresh_token",
                    "client_id": OAUTH_CLIENT_ID,
                    "refresh_token": refresh_token,
                },
                headers={"User-Agent": BROWSER_USER_AGENT},
            )
        except httpx.HTTPError as exc:
            raise SkylightError(f"Network error during token refresh: {exc}") from exc
        if token.status_code >= 400:
            raise SkylightAuthError(f"Token refresh failed (HTTP {token.status_code})")
        try:
            return _credentials_from_payload(token.json())
        except ValueError as exc:
            raise SkylightAuthError("Refresh response was not valid JSON") from exc
    finally:
        if http is None:
            client.close()
