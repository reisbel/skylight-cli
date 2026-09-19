"""The OAuth login replay, exercised against a mocked Skylight."""

from __future__ import annotations

import httpx
import pytest

from skylight_cli import auth
from skylight_cli.errors import SkylightAuthError

LOGIN_PAGE = (
    '<form method="post" action="/auth/session">'
    '<input type="hidden" name="authenticity_token" value="csrf-abc123" />'
    "</form>"
)


def make_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def happy_path(*, password: str = "correct-horse") -> callable:
    state: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/oauth/authorize":
            state["state"] = request.url.params["state"]
            state["challenge"] = request.url.params["code_challenge"]
            return httpx.Response(302, headers={"location": "/login"})
        if path == "/login":
            return httpx.Response(200, text=LOGIN_PAGE)
        if path == "/auth/session":
            body = dict(pair.split("=", 1) for pair in request.content.decode().split("&"))
            if body.get("authenticity_token") != "csrf-abc123":
                return httpx.Response(422, text="bad csrf")
            if body.get("password") != password:
                return httpx.Response(302, headers={"location": "/login"})
            return httpx.Response(
                302,
                headers={
                    "location": f"skylight-family://welcome?code=auth-code-1&state={state['state']}"
                },
            )
        if path == "/oauth/token":
            return httpx.Response(
                200,
                json={
                    "access_token": "tok-123",
                    "refresh_token": "ref-456",
                    "created_at": 1000,
                    "expires_in": 7200,
                },
            )
        raise AssertionError(f"unexpected request to {path}")

    return handler


def test_login_returns_credentials() -> None:
    with make_client(happy_path()) as http:
        credentials = auth.login("me@example.com", "correct-horse", http=http)

    assert credentials.access_token == "tok-123"
    assert credentials.refresh_token == "ref-456"
    assert credentials.expires_at == 8200.0
    assert credentials.authorization == "Bearer tok-123"


def test_login_rejects_wrong_password() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/authorize":
            return httpx.Response(200, text=LOGIN_PAGE)
        if request.url.path == "/auth/session":
            # A failed login loops back to the form rather than redirecting out.
            return httpx.Response(302, headers={"location": "/login"})
        return httpx.Response(200, text=LOGIN_PAGE)

    with make_client(handler) as http:
        with pytest.raises(SkylightAuthError, match="rejected"):
            auth.login("me@example.com", "wrong", http=http)


def test_login_fails_when_form_has_no_csrf_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>maintenance</html>")

    with make_client(handler) as http:
        with pytest.raises(SkylightAuthError, match="CSRF"):
            auth.login("me@example.com", "whatever", http=http)


def test_login_rejects_mismatched_state() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth/authorize":
            return httpx.Response(200, text=LOGIN_PAGE)
        if request.url.path == "/auth/session":
            return httpx.Response(
                302,
                headers={"location": "skylight-family://welcome?code=c&state=not-ours"},
            )
        raise AssertionError("should not reach the token endpoint")

    with make_client(handler) as http:
        with pytest.raises(SkylightAuthError, match="state mismatch"):
            auth.login("me@example.com", "pw", http=http)


def test_refresh_exchanges_the_refresh_token() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(pair.split("=", 1) for pair in request.content.decode().split("&")))
        return httpx.Response(200, json={"access_token": "tok-new"})

    with make_client(handler) as http:
        credentials = auth.refresh("ref-456", http=http)

    assert credentials.access_token == "tok-new"
    assert seen["grant_type"] == "refresh_token"
    assert seen["refresh_token"] == "ref-456"


def test_refresh_surfaces_a_rejected_token() -> None:
    with make_client(lambda request: httpx.Response(401, json={})) as http:
        with pytest.raises(SkylightAuthError, match="refresh failed"):
            auth.refresh("stale", http=http)


def test_expiry_uses_a_leeway() -> None:
    credentials = auth.Credentials("tok", expires_at=1000.0)
    assert credentials.is_expired(now=1000.0)
    assert credentials.is_expired(now=950.0)  # inside the 60s leeway
    assert not credentials.is_expired(now=800.0)
    assert not auth.Credentials("tok").is_expired(now=10**9)
