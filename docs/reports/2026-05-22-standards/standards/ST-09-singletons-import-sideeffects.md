# ST-09 — Move the `.env`-writing bootstrap out of import time

> Category: structure · Effort: M · Risk: medium · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
`config.py` performs **file I/O at import**: it instantiates `settings`, auto-generates `CREDENTIAL_KEY`, and **writes `.env`** — a side effect that runs on every import, including test collection and read-only deploys. Several module-level singletons also exist (`manager = ConnectionManager()`, `health_monitor = SourceHealthMonitor()`, `_cached_path` in `browser_path`).

## Why it matters (ship)
Surprise filesystem writes during import are fragile (read-only containers, parallel test workers) and make startup ordering implicit. Singletons aren't multi-worker safe — worth documenting before scaling out.

## Locations
- `backend/config.py:65-91` (instantiates `settings`, writes `.env`, resolves `DATA_DIR` at import)
- `backend/api/ws.py:144` (`manager`); `backend/utils/source_health.py:175` (`health_monitor`); `backend/utils/browser_path.py:46` (`global _cached_path, _resolved`)
- `backend/applier/captcha_handler.py:39-40`, `scraping/session_manager.py:78-79` (path constants from a module-level `_settings`)

## Proposed change
Move the credential-key bootstrap into an explicit `ensure_credential_key()` called from `start.py` / the FastAPI lifespan — not at import. Keep the singletons but add a one-line note that `manager`/`health_monitor` are single-process (not multi-worker safe).

## Acceptance criteria
- [ ] Importing `backend.config` performs no filesystem writes
- [ ] First-launch key generation still happens before any encrypt/decrypt call (verify ordering)
- [ ] Tests can import config without creating `.env`
- [ ] Singleton multi-worker caveat documented

## Blast radius & risk
Medium — moving the credential bootstrap changes startup ordering; ensure key generation precedes the first credential operation.

## Dependencies
Coordinate with ST-02/ST-03 (credential handling) so the bootstrap and the `SecretStr`/helper changes land coherently.
