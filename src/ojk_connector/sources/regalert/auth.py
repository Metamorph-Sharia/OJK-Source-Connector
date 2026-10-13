"""Sign-in for RegAlert: OAuth 2.1 against its authorization server (Clerk).

* Browser sign-in (default): authorization code + PKCE with a loopback redirect.
* Device sign-in (machines without a browser): the device authorization grant.

Both register a public client on the fly (dynamic client registration) and ask for
``offline_access``, so the scheduled pipeline can refresh its token unattended.
Tokens are stored outside the repository (``credentials_dir``) with mode 0600.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
import webbrowser
from collections.abc import Callable, Generator
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx

from ojk_connector.sources.base import SourceAuthError

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"
SCOPES = "openid profile email offline_access"
EXPIRY_SKEW_SECONDS = 60


@dataclass
class Token:
    access_token: str
    refresh_token: str | None
    expires_at: float | None  # unix time
    client_id: str
    token_endpoint: str
    resource: str | None = None
    scope: str | None = None

    def expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now if now is not None else time.time()) >= self.expires_at - EXPIRY_SKEW_SECONDS


class TokenStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> Token | None:
        try:
            return Token(**json.loads(self.path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            return None
        except (ValueError, TypeError) as e:
            raise SourceAuthError(f"unreadable token file {self.path}: {e}") from None

    def save(self, token: Token) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(asdict(token), f, indent=2)
        os.replace(tmp, self.path)


def token_from_response(
    payload: dict[str, Any], *, client_id: str, token_endpoint: str, resource: str | None,
    previous: Token | None = None,
) -> Token:
    if not payload.get("access_token"):
        raise SourceAuthError("token response has no access_token")
    expires_in = payload.get("expires_in")
    return Token(
        access_token=payload["access_token"],
        # Servers may omit the refresh token on refresh; keep the one we had.
        refresh_token=payload.get("refresh_token") or (previous.refresh_token if previous else None),
        expires_at=time.time() + float(expires_in) if expires_in else None,
        client_id=client_id,
        token_endpoint=token_endpoint,
        resource=resource,
        scope=payload.get("scope"),
    )


class RegAlertAuth(httpx.Auth):
    """Adds the bearer token, refreshing it when expired or rejected."""

    requires_response_body = True

    def __init__(self, store: TokenStore, *, static_token: str | None = None, login_hint: str) -> None:
        self._store = store
        self._static = static_token
        self._login_hint = login_hint

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        if self._static:
            request.headers["Authorization"] = f"Bearer {self._static}"
            yield request
            return
        token = self._store.load()
        if token is None:
            raise SourceAuthError(f"not signed in to RegAlert; run: {self._login_hint}")
        if token.expired():
            token = yield from self._refresh(token)
        request.headers["Authorization"] = f"Bearer {token.access_token}"
        response = yield request
        if response.status_code == 401 and token.refresh_token:
            token = yield from self._refresh(token)
            request.headers["Authorization"] = f"Bearer {token.access_token}"
            yield request

    def _refresh(self, token: Token) -> Generator[httpx.Request, httpx.Response, Token]:
        if not token.refresh_token:
            raise SourceAuthError(f"RegAlert session expired and cannot be refreshed; run: {self._login_hint}")
        data = {"grant_type": "refresh_token", "refresh_token": token.refresh_token, "client_id": token.client_id}
        if token.resource:
            data["resource"] = token.resource
        response = yield httpx.Request("POST", token.token_endpoint, data=data)
        if response.status_code != 200:
            raise SourceAuthError(
                f"RegAlert token refresh failed (HTTP {response.status_code}); run: {self._login_hint}"
            )
        new = token_from_response(
            response.json(), client_id=token.client_id, token_endpoint=token.token_endpoint,
            resource=token.resource, previous=token,
        )
        self._store.save(new)
        return new


# --- sign-in ---------------------------------------------------------------


@dataclass(frozen=True)
class AuthServer:
    resource: str | None
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: str | None
    device_authorization_endpoint: str | None


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def discover_auth_server(http: httpx.Client, base_url: str) -> AuthServer:
    base = base_url.rstrip("/")
    prm: dict[str, Any] | None = None
    for path in ("/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"):
        r = http.get(base + path)
        if r.status_code == 200:
            prm = _json(r)
            break
    if not prm or not prm.get("authorization_servers"):
        raise SourceAuthError(f"could not discover RegAlert's authorization server from {base}")
    issuer = str(prm["authorization_servers"][0]).rstrip("/")
    meta: dict[str, Any] = {}
    for path in ("/.well-known/oauth-authorization-server", "/.well-known/openid-configuration"):
        r = http.get(issuer + path)
        if r.status_code == 200:
            meta = _json(r)
            break
    if not meta.get("authorization_endpoint") or not meta.get("token_endpoint"):
        raise SourceAuthError(f"incomplete authorization server metadata at {issuer}")
    return AuthServer(
        resource=prm.get("resource"),
        authorization_endpoint=meta["authorization_endpoint"],
        token_endpoint=meta["token_endpoint"],
        registration_endpoint=meta.get("registration_endpoint"),
        device_authorization_endpoint=meta.get("device_authorization_endpoint"),
    )


def register_client(
    http: httpx.Client, server: AuthServer, *, grant_types: list[str], redirect_uris: list[str] | None = None
) -> str:
    if not server.registration_endpoint:
        raise SourceAuthError("RegAlert's authorization server does not support client registration")
    body: dict[str, Any] = {
        "client_name": "ojk-connector",
        "grant_types": grant_types,
        "token_endpoint_auth_method": "none",
        "scope": SCOPES,
    }
    if redirect_uris:
        body["redirect_uris"] = redirect_uris
        body["response_types"] = ["code"]
    r = http.post(server.registration_endpoint, json=body)
    if r.status_code not in (200, 201) or not _json(r).get("client_id"):
        raise SourceAuthError(f"client registration failed: HTTP {r.status_code} {r.text[:300]}")
    return _json(r)["client_id"]


def _exchange(http: httpx.Client, server: AuthServer, client_id: str, data: dict[str, str]) -> Token:
    if server.resource:
        data = {**data, "resource": server.resource}
    r = http.post(server.token_endpoint, data=data)
    if r.status_code != 200:
        raise SourceAuthError(f"token request failed: HTTP {r.status_code} {_json(r).get('error', r.text[:200])}")
    return token_from_response(
        _json(r), client_id=client_id, token_endpoint=server.token_endpoint, resource=server.resource
    )


class _LoopbackReceiver:
    """A one-shot HTTP server on 127.0.0.1 that captures the OAuth redirect."""

    def __enter__(self) -> _LoopbackReceiver:
        result: dict[str, str] = {}

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (http.server API)
                parsed = urlsplit(self.path)
                if parsed.path != "/callback":
                    self.send_response(404)
                    self.end_headers()
                    return
                result.update({k: v[0] for k, v in parse_qs(parsed.query).items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<p>Signed in to RegAlert for ojk-connector. You can close this tab.</p>")

            def log_message(self, *args: object) -> None:
                pass

        self._result = result
        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self._server.timeout = 0.5
        return self

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/callback"

    def wait_for_code(self, state: str, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        while not self._result and time.monotonic() < deadline:
            self._server.handle_request()
        if not self._result:
            raise SourceAuthError("timed out waiting for the RegAlert sign-in")
        if "error" in self._result:
            raise SourceAuthError(f"sign-in refused: {self._result.get('error_description') or self._result['error']}")
        if self._result.get("state") != state:
            raise SourceAuthError("sign-in response did not match this request (state mismatch)")
        return self._result["code"]

    def __exit__(self, *exc: object) -> None:
        self._server.server_close()


def browser_login(
    http: httpx.Client, base_url: str, store: TokenStore, *,
    notify: Callable[[str], None] = print,
    open_browser: Callable[[str], object] = webbrowser.open,
    timeout: float = 300.0,
) -> Token:
    server = discover_auth_server(http, base_url)
    with _LoopbackReceiver() as receiver:
        client_id = register_client(
            http, server, grant_types=["authorization_code", "refresh_token"], redirect_uris=[receiver.redirect_uri]
        )
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        state = secrets.token_urlsafe(24)
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": receiver.redirect_uri,
            "scope": SCOPES,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if server.resource:
            params["resource"] = server.resource
        url = f"{server.authorization_endpoint}?{urlencode(params)}"
        notify(f"Opening the RegAlert sign-in page. If it does not open, visit:\n{url}")
        open_browser(url)
        code = receiver.wait_for_code(state, timeout)
        token = _exchange(http, server, client_id, {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": receiver.redirect_uri,
            "client_id": client_id,
            "code_verifier": verifier,
        })
    store.save(token)
    return token


def device_login(
    http: httpx.Client, base_url: str, store: TokenStore, *,
    notify: Callable[[str], None] = print,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> Token:
    server = discover_auth_server(http, base_url)
    if not server.device_authorization_endpoint:
        raise SourceAuthError("RegAlert does not offer device sign-in; use the browser method")
    client_id = register_client(http, server, grant_types=[DEVICE_GRANT, "refresh_token"])
    request = {"client_id": client_id, "scope": SCOPES}
    if server.resource:
        request["resource"] = server.resource
    r = http.post(server.device_authorization_endpoint, data=request)
    if r.status_code != 200:
        raise SourceAuthError(f"device sign-in could not start: HTTP {r.status_code} {r.text[:200]}")
    grant = _json(r)
    interval = float(grant.get("interval", 5))
    deadline = clock() + float(grant.get("expires_in", 600))
    where = grant.get("verification_uri_complete") or grant.get("verification_uri")
    notify(f"To sign in to RegAlert, open {where} and enter the code {grant.get('user_code')}")
    while clock() < deadline:
        sleep(interval)
        try:
            token = _exchange(http, server, client_id, {
                "grant_type": DEVICE_GRANT,
                "device_code": grant["device_code"],
                "client_id": client_id,
            })
        except SourceAuthError as e:
            message = str(e)
            if "authorization_pending" in message:
                continue
            if "slow_down" in message:
                interval += 5
                continue
            raise
        store.save(token)
        return token
    raise SourceAuthError("device sign-in expired; run the login again")
