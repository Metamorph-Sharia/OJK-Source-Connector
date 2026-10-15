"""An in-memory stand-in for RegAlert's REST API, for tests.

Response shapes follow https://mcp.regalert.today/openapi.json (``total``/``has_more``/
``documents``; ``document`` with ``text`` and ``text_next_offset``; problem bodies with
``error``). The original-text field name is an assumption (see ORIGINAL_TEXT_KEYS).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

import httpx


@dataclass
class FakeRegAlert:
    rows: list[dict] = field(default_factory=list)
    texts: dict[str, tuple[str, str]] = field(default_factory=dict)  # uid -> (english, original)
    incoming: dict[str, list[dict]] = field(default_factory=dict)  # uid -> incoming lineage edges
    full_text_reads: str = "available"
    fair_use_after: int | None = None  # reads allowed before 429 fair_use
    report_paused_after: int | None = None  # after this many reads, responses report the account as paused
    charged_usd: str = "$0.00"
    token: str = "good-token"
    forbidden: bool = False  # RegAlert refuses every request (HTTP 403), as it did on 2026-10-15
    calls: list[httpx.Request] = field(default_factory=list)
    reads: int = 0
    lineage_calls: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"error": "unauthorized", "message": "no"})
        if self.forbidden:
            return httpx.Response(403, json={"error": {"code": "403", "message": "Forbidden"}})
        path, q = request.url.path, request.url.params
        if path == "/v1/account":
            return httpx.Response(200, json={"access": "free", "full_text_reads": self.full_text_reads})
        if path == "/v1/recent":
            assert q["regulator"] == "OJK"
            since, until = date.fromisoformat(q["since"]), date.fromisoformat(q["until"])
            rows = sorted(
                (r for r in self.rows if since <= date.fromisoformat(r["published"]) <= until),
                key=lambda r: r["published"], reverse=True,
            )
            limit, offset = int(q["limit"]), int(q.get("offset", 0))
            page = rows[offset: offset + limit]
            return httpx.Response(200, json={"total": len(rows), "has_more": offset + limit < len(rows), "documents": page})
        if path.startswith("/v1/documents/") and path.endswith("/lineage"):
            uid = path.split("/")[3]
            if not any(r["uid"] == uid for r in self.rows):
                return httpx.Response(404, json={"error": "not_found", "message": "no such document"})
            self.lineage_calls += 1
            assert q["direction"] == "incoming"
            edges = [e for e in self.incoming.get(uid, []) if e["relation"] == q["relation"]]
            return httpx.Response(200, json={"total": len(edges), "has_more": False, "edges": edges})
        if path.startswith("/v1/documents/"):
            uid = path.rsplit("/", 1)[-1]
            if uid not in self.texts:
                return httpx.Response(404, json={"error": "not_found", "message": "no such document"})
            if self.fair_use_after is not None and self.reads >= self.fair_use_after:
                return httpx.Response(429, json={"error": "fair_use", "message": "Full-text reads pause until tomorrow (UTC)."})
            self.reads += 1
            english, original = self.texts[uid]
            offset, size = int(q.get("offset", 0)), int(q["max_chars"])

            def part(key: str, text: str) -> dict:
                # Shape as seen live (2026-10-12): each text pages independently.
                return {key: text[offset: offset + size], f"{key}_offset": offset, f"{key}_total_chars": len(text),
                        f"{key}_truncated": offset + size < len(text),
                        f"{key}_next_offset": offset + size if offset + size < len(text) else None}

            document = {"uid": uid, "language": "id", **part("text", english),
                        "lineage": {"status": "active", "status_label": "In force", "amends": [], "supersedes": []}}
            if q.get("include_original") == "true":
                document |= {"original_text_included": True, **part("original_text", original)}
            reads_state = "paused" if self.report_paused_after is not None and self.reads >= self.report_paused_after \
                else self.full_text_reads
            body = {"document": document, "charged_usd": self.charged_usd, "charged_as": "free",
                    "account": {"access": "free", "full_text_reads": reads_state}}
            return httpx.Response(200, content=json.dumps(body).encode())
        return httpx.Response(404, json={"error": "not_found", "message": path})


def row(uid: str, published: str, slug: str, title: str = "", **extra) -> dict:
    return {
        "uid": uid, "title": title, "published": published, "regulator": "OJK", "language": "id",
        "source": f"https://www.ojk.go.id/id/regulasi/Pages/{slug}.aspx",
        "url": f"https://regalert.today/document/{uid}", **extra,
    }
