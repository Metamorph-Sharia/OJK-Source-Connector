from __future__ import annotations

import time
from urllib.parse import parse_qs

import httpx
import pytest

from ojk_connector.sources import SourceAuthError
from ojk_connector.sources.regalert.auth import DEVICE_GRANT, RegAlertAuth, Token, TokenStore, device_login

TOKEN_URL = "https://auth.example/oauth/token"


def _form(request: httpx.Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(request.content.decode()).items()}


def test_token_file_is_private(tmp_path) -> None:
    store = TokenStore(tmp_path / "creds" / "regalert.json")
    store.save(Token("a", "r", None, "c", TOKEN_URL))
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.load() == Token("a", "r", None, "c", TOKEN_URL)


def test_refreshes_on_401_and_saves_the_new_token(tmp_path) -> None:
    store = TokenStore(tmp_path / "regalert.json")
    store.save(Token("old", "refresh-1", None, "client-1", TOKEN_URL, resource="https://mcp.example/mcp"))
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            form = _form(request)
            assert form == {"grant_type": "refresh_token", "refresh_token": "refresh-1", "client_id": "client-1",
                            "resource": "https://mcp.example/mcp"}
            return httpx.Response(200, json={"access_token": "new", "expires_in": 3600})
        seen.append(request.headers["Authorization"])
        ok = request.headers["Authorization"] == "Bearer new"
        return httpx.Response(200 if ok else 401, json={})

    auth = RegAlertAuth(store, login_hint="ojk-connector auth login regalert")
    with httpx.Client(transport=httpx.MockTransport(handler), auth=auth) as http:
        assert http.get("https://api.example/v1/account").status_code == 200
    assert seen == ["Bearer old", "Bearer new"]
    saved = store.load()
    assert saved.access_token == "new" and saved.refresh_token == "refresh-1"  # kept when not rotated


def test_expired_token_is_refreshed_before_the_request(tmp_path) -> None:
    store = TokenStore(tmp_path / "regalert.json")
    store.save(Token("old", "refresh-1", time.time() - 10, "client-1", TOKEN_URL))

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == TOKEN_URL:
            return httpx.Response(200, json={"access_token": "new", "refresh_token": "refresh-2", "expires_in": 60})
        assert request.headers["Authorization"] == "Bearer new"
        return httpx.Response(200, json={})

    with httpx.Client(transport=httpx.MockTransport(handler), auth=RegAlertAuth(store, login_hint="x")) as http:
        http.get("https://api.example/v1/account")
    assert store.load().refresh_token == "refresh-2"


def test_failed_refresh_asks_to_log_in_again(tmp_path) -> None:
    store = TokenStore(tmp_path / "regalert.json")
    store.save(Token("old", "refresh-1", time.time() - 10, "client-1", TOKEN_URL))
    transport = httpx.MockTransport(lambda r: httpx.Response(400, json={"error": "invalid_grant"}))
    auth = RegAlertAuth(store, login_hint="ojk-connector auth login regalert")
    with httpx.Client(transport=transport, auth=auth) as http, pytest.raises(SourceAuthError, match="auth login"):
        http.get("https://api.example/v1/account")


def test_device_login(tmp_path) -> None:
    polls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == "https://mcp.example/.well-known/oauth-protected-resource/mcp":
            return httpx.Response(200, json={"resource": "https://mcp.example/mcp",
                                             "authorization_servers": ["https://auth.example"]})
        if url == "https://auth.example/.well-known/oauth-authorization-server":
            return httpx.Response(200, json={
                "authorization_endpoint": "https://auth.example/oauth/authorize", "token_endpoint": TOKEN_URL,
                "registration_endpoint": "https://auth.example/oauth/register",
                "device_authorization_endpoint": "https://auth.example/oauth/device_authorization",
            })
        if url == "https://auth.example/oauth/register":
            return httpx.Response(201, json={"client_id": "dyn-client"})
        if url == "https://auth.example/oauth/device_authorization":
            assert "offline_access" in _form(request)["scope"]
            return httpx.Response(200, json={"device_code": "dc", "user_code": "ABCD-EFGH", "interval": 1,
                                             "verification_uri": "https://auth.example/device", "expires_in": 60})
        if url == TOKEN_URL:
            form = _form(request)
            assert form["grant_type"] == DEVICE_GRANT and form["client_id"] == "dyn-client"
            polls["n"] += 1
            if polls["n"] < 2:
                return httpx.Response(400, json={"error": "authorization_pending"})
            return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3600})
        return httpx.Response(404)

    messages: list[str] = []
    store = TokenStore(tmp_path / "regalert.json")
    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        device_login(http, "https://mcp.example", store, notify=messages.append, sleep=lambda s: None)
    token = store.load()
    assert (token.access_token, token.refresh_token, token.client_id) == ("at", "rt", "dyn-client")
    assert token.resource == "https://mcp.example/mcp"
    assert "ABCD-EFGH" in messages[0]
