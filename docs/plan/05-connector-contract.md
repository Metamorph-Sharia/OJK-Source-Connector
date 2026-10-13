# 05 — Connector interface

> **Status:** team interface version 1.0 (2026-10-13), implemented by `OJKConnector`.

The interface every team connector implements is in [contract/CONNECTOR_INTERFACE.md](../../contract/CONNECTOR_INTERFACE.md): one class with two methods, `discover(cursor)` and `fetch(item, store)`. That document is what we send to the team. This repository's copy of the code is [src/ojk_connector/source_connector.py](../../src/ojk_connector/source_connector.py), and a test fails if it ever differs from the document.

## How `OJKConnector` implements it

[src/ojk_connector/connector.py](../../src/ojk_connector/connector.py) adapts our sources to the interface:

| Interface | OJK implementation |
|---|---|
| `name`, `max_concurrency` | `"ojk"`; `1`, because RegAlert's full-text reads are rationed |
| `discover(cursor)` | Lists every configured source. The cursor is the date of the last run; an incremental listing looks back `lookback_days` (45) from it, because RegAlert sometimes dates documents late |
| `Discovered.source_id` | `"<source>:<id>"`, e.g. `regalert:fe2ab…`, so IDs stay unique once the fallback source is added |
| `Discovered.version` | A hash of the fields that signal new content (title, identifier, file URL…) |
| `Discovered.metadata` | `source`, `id`, `title`, `published`, `official_url`, and `fields`: the source's own record, unchanged. It's enough for `fetch` to run in a fresh process |
| `fetch(item, store)` | Saves every raw API page and every text through `store.put()`. `Fetched.metadata["texts"]` gives each text's language, origin (`source_text`, `source_translation`) and rights (`public`, `restricted`) |
| Errors | Quota or fair-use pause → `SourceUnavailable`; bad or missing sign-in → `AuthFailed`; a missing document → `ItemFailed(retryable=False)`; other source errors → `ItemFailed` |

## What stays inside this repository

Canonical IDs, deduplication, standing checks and the corpus export run in the standalone pipeline (`ojk-connector run`), not through the interface. The interface asks connectors to pass source fields through unchanged and leave identity and merging to shared stages. When the team designs those shared stages, our identity rules and dedup are candidates to move there.
