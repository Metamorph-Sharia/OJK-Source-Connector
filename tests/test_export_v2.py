"""`export --format v2` end to end: a backfilled store becomes a bundle the platform can load."""

from __future__ import annotations

from metamorph_contract import Bundle
from test_pipeline import fake, make_pipeline  # noqa: F401  (pytest fixture)

from ojk_connector.export_v2 import export_bundle


def backfilled(tmp_path, fake):  # noqa: F811
    pipeline = make_pipeline(tmp_path, fake)
    for _ in range(3):
        pipeline.run()
    return pipeline


def test_the_backfilled_store_exports_as_a_valid_bundle(tmp_path, fake) -> None:  # noqa: F811
    with backfilled(tmp_path, fake) as pipeline:
        stats = export_bundle(pipeline.store, tmp_path / "bundle", run_id="test-run")

    bundle = Bundle(tmp_path / "bundle")
    entries = list(bundle.entries())
    assert all(entry.is_valid for entry in entries), [entry.problems for entry in entries]
    documents = {entry.document.document.id: entry.document for entry in entries}

    # PADK and POJK 11/2026 stay apart; RegAlert's duplicate POJK 28/2014 is one document.
    assert set(documents) == {"ojk:pojk:2026:1", "ojk:padk:2026:11", "ojk:pojk:2026:11", "ojk:pojk:2014:28"}
    assert (stats.documents, stats.unresolved, stats.without_content) == (4, 0, 0)
    assert bundle.manifest.connector == "ojk" and bundle.manifest.run_id == "test-run"

    pojk = documents["ojk:pojk:2026:1"]
    # This fixture's header stops after the subject (no "DENGAN RAHMAT …"), so RegAlert's title is used.
    assert pojk.document.title == "Use of Foreign Workers and Knowledge Transfer Programs by Commercial Banks"
    assert pojk.document.citation == "POJK 1 Tahun 2026"
    assert [t.language for t in pojk.texts] == ["id"]  # RegAlert's English translation is restricted: never bundled
    assert bundle.blob_path(pojk.texts[0].sha256).read_text().startswith("PERATURAN OTORITAS JASA KEUANGAN")
