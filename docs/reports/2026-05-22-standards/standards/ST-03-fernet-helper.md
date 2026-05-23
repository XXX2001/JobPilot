# ST-03 — Extract duplicated Fernet credential logic into one helper

> Category: structure · Effort: M · Risk: low-medium · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
The `Fernet(settings.CREDENTIAL_KEY.encode())` construction — plus the surrounding "no key → return masked/None" guard and broad-except handling — is copy-pasted across three files with subtly different error handling. Each also does a local `from cryptography.fernet import Fernet`.

## Why it matters (ship)
Centralizing removes drift risk in security-sensitive code and makes the `SecretStr` migration (ST-02) a single-site change.

## Locations
- `backend/api/settings.py:607-617` (decrypt for masking), `:651-655` (encrypt on save)
- `backend/scraping/session_manager.py:339-350` (decrypt for login)

## Proposed change
Add `backend/security/credentials.py` (or extend the existing `security/` package) with `encrypt(value) -> str`, `decrypt(value) -> str | None`, and `is_configured() -> bool`. Replace the three call sites. Preserve each site's exact failure semantics (they differ in what they return on failure — reconcile deliberately).

## Acceptance criteria
- [ ] Single helper module owns Fernet construction + encrypt/decrypt
- [ ] All three sites use it; no local `Fernet(...)` construction remains
- [ ] Failure semantics preserved (or intentionally unified, documented)
- [ ] Credential save/load tests pass (`tests/test_sanitizer.py`/session tests as applicable)

## Blast radius & risk
Low-medium — touches credential save/load; behavior must be preserved exactly.

## Dependencies
**Do before ST-02.** Pairs with EH-04 (session login error visibility).
