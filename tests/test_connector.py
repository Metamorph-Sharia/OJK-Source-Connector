"""The OJK connector through the team's interface, as the orchestrator will use it."""

from __future__ import annotations

import hashlib
import re
from datetime import date
from pathlib import Path

import pytest
from fake_regalert import FakeRegAlert, row

from ojk_connector import OJKConnector
from ojk_connector.source_connector import AuthFailed, Discovered, ItemFailed, SourceUnavailable, StoredFile
from ojk_connector.sources import SourceContext
from ojk_connector.sources.regalert.auth import Token, TokenStore
from ojk_connector.sources.regalert.source import RegAlertSource

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 12)
POJK_1_2026 = "PERATURAN OTORITAS JASA KEUANGAN\nNOMOR 1 TAHUN 2026\nTENTANG\n..."


class MemoryStore:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}

    def put(self, data: bytes, *, media_type: str, source_url: str | None = None) -> StoredFile:
        sha = hashlib.sha256(data).hexdigest()
        self.files[sha] = data
        return StoredFile(sha256=sha, media_type=media_type, source_url=source_url)


@pytest.fixture
def fake() -> FakeRegAlert:
    return FakeRegAlert(
        rows=[row("pojk-1-2026", "2026-02-23", "POJK-1-Tahun-2026-Tenaga-Kerja-Asing", "Use of Foreign Workers")],
        texts={"pojk-1-2026": ("FINANCIAL SERVICES AUTHORITY REGULATION NUMBER 1 OF 2026", POJK_1_2026)},
    )


def make_connector(tmp_path: Path, fake: FakeRegAlert, *, signed_in: bool = True) -> OJKConnector:
    config = tmp_path / "pipeline.toml"
    config.write_text('[pipeline]\ncredentials_dir = "creds"\n[sources.regalert]\nrequests_per_second = 0\n')
    if signed_in:
        TokenStore(tmp_path / "creds/regalert.json").save(Token("good-token", "r", None, "c", "https://a/t"))
    regalert = RegAlertSource({"requests_per_second": 0}, SourceContext("regalert", tmp_path / "creds", "test"),
                              transport=fake.transport(), sleep=lambda s: None)
    return OJKConnector({"config": str(config)}, sources={"regalert": regalert}, today=lambda: TODAY)


def test_discover_lists_every_document_and_returns_a_cursor(tmp_path, fake) -> None:
    discovery = make_connector(tmp_path, fake).discover(cursor=None)
    assert discovery.cursor == "2026-10-12"
    [item] = discovery.items
    assert item.source_id == "regalert:pojk-1-2026"
    assert item.metadata["title"] == "Use of Foreign Workers"
    assert item.metadata["fields"]["uid"] == "pojk-1-2026"  # the source's own fields, unchanged


def test_incremental_discovery_looks_back_from_the_cursor(tmp_path, fake) -> None:
    make_connector(tmp_path, fake).discover(cursor="2026-10-01")
    since = {r.url.params["since"] for r in fake.calls if r.url.path == "/v1/recent"}
    assert since == {"2026-08-17"}  # 45 days before the cursor


def test_fetch_stores_raw_payloads_and_texts_and_works_from_the_item_alone(tmp_path, fake) -> None:
    item = make_connector(tmp_path, fake).discover(cursor=None).items[0]
    # As in Airflow: fetch runs in another process, with only the serialized item.
    fresh = make_connector(tmp_path, fake)
    store = MemoryStore()
    fetched = fresh.fetch(Discovered(**vars(item)), store)

    media = sorted(f.media_type for f in fetched.files)
    assert media == ["application/json", "text/plain; charset=utf-8", "text/plain; charset=utf-8"]
    assert all(f.sha256 in store.files for f in fetched.files)
    indonesian = next(t for t in fetched.metadata["texts"] if t["language"] == "id")
    assert store.files[indonesian["sha256"]].decode().startswith("PERATURAN OTORITAS JASA KEUANGAN")
    assert indonesian["rights"] == "public"
    english = next(t for t in fetched.metadata["texts"] if t["language"] == "en")
    assert english["rights"] == "restricted"  # RegAlert's translation, until licensed


def test_errors_become_the_interface_errors(tmp_path, fake) -> None:
    item = make_connector(tmp_path, fake).discover(cursor=None).items[0]

    fake.fair_use_after = 0
    with pytest.raises(SourceUnavailable):
        make_connector(tmp_path, fake).fetch(item, MemoryStore())

    fake.fair_use_after = None
    del fake.texts["pojk-1-2026"]
    with pytest.raises(ItemFailed) as failed:
        make_connector(tmp_path, fake).fetch(item, MemoryStore())
    assert failed.value.retryable is False

    (tmp_path / "creds/regalert.json").unlink()
    with pytest.raises(AuthFailed):
        make_connector(tmp_path, fake, signed_in=False).discover(cursor=None)


def test_vendored_interface_matches_the_team_document() -> None:
    doc = (ROOT / "contract/CONNECTOR_INTERFACE.md").read_text()
    published = re.findall(r"```python\n(.*?)```", doc, re.S)[0]
    assert (ROOT / "src/ojk_connector/source_connector.py").read_text() == published
