"""HTTP client for RegAlert's REST API (``/v1``): pacing, retries, error mapping."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping
from typing import Any

import httpx

from regingest.connectors.base import PermanentSourceError, QuotaExhausted, SourceAuthError, SourceError

log = logging.getLogger(__name__)


def _problem(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        return float(response.headers.get("Retry-After", default))
    except ValueError:
        return default


class RegAlertClient:
    def __init__(
        self,
        base_url: str,
        *,
        auth: httpx.Auth | None,
        user_agent: str,
        requests_per_second: float = 1.0,
        max_retries: int = 3,
        timeout: float = 60.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx.Client(
            base_url=base_url,
            auth=auth,
            transport=transport,
            timeout=timeout,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        self._min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0.0
        self._max_retries = max_retries
        self._sleep = sleep
        self._clock = clock
        self._last_request: float | None = None

    def close(self) -> None:
        self._http.close()

    def _throttle(self) -> None:
        if self._last_request is not None and self._min_interval:
            wait = self._min_interval - (self._clock() - self._last_request)
            if wait > 0:
                self._sleep(wait)
        self._last_request = self._clock()

    def get(self, path: str, params: Mapping[str, Any] | None = None) -> tuple[dict[str, Any], bytes]:
        """GET a JSON endpoint; returns the parsed body and the raw bytes."""
        query = {
            k: ("true" if v is True else "false" if v is False else v)
            for k, v in (params or {}).items()
            if v is not None
        }
        for attempt in range(self._max_retries + 1):
            last_try = attempt == self._max_retries
            self._throttle()
            try:
                response = self._http.get(path, params=query)
            except httpx.TransportError as e:
                if last_try:
                    raise SourceError(f"RegAlert {path}: {e}") from e
                self._sleep(2**attempt)
                continue

            if response.status_code == 200:
                body = response.json()
                if not isinstance(body, dict):
                    raise SourceError(f"RegAlert {path}: expected a JSON object")
                return body, response.content

            problem = _problem(response)
            detail = f"{problem.get('error', '')}: {problem.get('message', response.text[:200])}".strip(": ")
            log.debug("RegAlert %s -> HTTP %s %s", path, response.status_code, detail)
            if response.status_code == 401:
                raise SourceAuthError(f"RegAlert rejected the credentials ({detail})")
            if response.status_code == 429:
                if problem.get("error") == "fair_use":
                    raise QuotaExhausted(problem.get("message") or "RegAlert fair-use pause; resumes next day (UTC)")
                if last_try:
                    raise SourceError(f"RegAlert {path}: rate limited ({detail})")
                self._sleep(min(_retry_after(response, 5.0), 60.0))
                continue
            if response.status_code == 404:
                raise PermanentSourceError(f"RegAlert {path}: not found ({detail})")
            if response.status_code >= 500 and not last_try:
                self._sleep(2**attempt)
                continue
            raise SourceError(f"RegAlert {path}: HTTP {response.status_code} ({detail})")
        raise SourceError(f"RegAlert {path}: retries exhausted")  # pragma: no cover
