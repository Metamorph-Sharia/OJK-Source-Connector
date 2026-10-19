"""HTTP client for JDIH OJK: pacing, retries, error mapping.

JDIH sits behind an F5 firewall that has rejected some clients ("Request Rejected", seen 2026-10-12).
A rejection is a refusal, never something to work around: it becomes ``SourceForbidden``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

from ojk_connector.sources.base import PermanentSourceError, SourceError, SourceForbidden

log = logging.getLogger(__name__)

FIREWALL_NOTICE = b"The requested URL was rejected"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024


class JdihClient:
    def __init__(
        self,
        *,
        user_agent: str,
        requests_per_second: float = 1.0,
        max_retries: int = 3,
        timeout: float = 120.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=True,
                                  headers={"User-Agent": user_agent})
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

    def get(self, url: str) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            last_try = attempt == self._max_retries
            self._throttle()
            try:
                response = self._http.get(url)
            except httpx.TransportError as e:
                if last_try:
                    raise SourceError(f"JDIH OJK {url}: {e}") from e
                self._sleep(2**attempt)
                continue

            log.debug("JDIH OJK %s -> HTTP %s", url, response.status_code)
            if response.status_code == 403 or FIREWALL_NOTICE in response.content[:4096]:
                raise SourceForbidden(f"JDIH OJK {url}: access refused by the site's firewall")
            if response.status_code == 200:
                if len(response.content) > MAX_DOWNLOAD_BYTES:
                    raise PermanentSourceError(f"JDIH OJK {url}: {len(response.content)} bytes is over the limit")
                return response
            if response.status_code == 404:
                raise PermanentSourceError(f"JDIH OJK {url}: not found")
            if (response.status_code >= 500 or response.status_code == 429) and not last_try:
                self._sleep(2**attempt)
                continue
            raise SourceError(f"JDIH OJK {url}: HTTP {response.status_code}")
        raise SourceError(f"JDIH OJK {url}: retries exhausted")  # pragma: no cover
