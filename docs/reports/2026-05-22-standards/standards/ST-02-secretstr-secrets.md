# ST-02 — Type API secrets as Pydantic `SecretStr`

> Category: security · Effort: M · Risk: medium · **Ship-blocker: YES**
> Part of: [Naming & Standards backlog](../INDEX.md) · Recurs as code-review **HR-03**

## Problem
`GOOGLE_API_KEY`, `ADZUNA_APP_KEY`, `SERPAPI_KEY`, `CREDENTIAL_KEY` are plain `str`, so they render in `repr()`, logs, and tracebacks. `SecretStr` masks them by default. (Grep confirms no `SecretStr` anywhere in `backend/`.)

## Why it matters (ship)
Cheap, high-value hardening: prevents the Fernet key + provider keys from leaking via any accidental log/serialization/trace.

## Locations
- `backend/config.py:40-46` (`GOOGLE_API_KEY`, `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `SERPAPI_KEY`, `CREDENTIAL_KEY`)
- Read sites: `api/settings.py:610,653` and `scraping/session_manager.py:343` (`Fernet(settings.CREDENTIAL_KEY.encode())`); the genai/Adzuna client construction sites

## Proposed change
Convert the key fields to `SecretStr`; update read sites to call `.get_secret_value()` (then `.encode()`). Update the `config.py` bootstrap that writes `CREDENTIAL_KEY` to `.env`.

## Acceptance criteria
- [ ] All four key fields are `SecretStr`
- [ ] `repr(settings)` / logging shows `**********` for keys
- [ ] All read sites use `.get_secret_value()`; encrypt/decrypt + provider clients still work
- [ ] `.env` bootstrap still generates/persists the key correctly

## Blast radius & risk
Medium — every consumer of the raw string must add `.get_secret_value()`. **Do ST-03 first** (centralize Fernet) so this becomes a one-line change at one helper.

## Dependencies
Best after **ST-03**.
