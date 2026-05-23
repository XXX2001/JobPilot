# ST-01 — Lock down permissive CORS

> Category: security · Effort: S · Risk: medium · **Ship-blocker: YES**
> Part of: [Naming & Standards backlog](../INDEX.md) · Recurs as code-review **CR-01** (CORS portion)

## Problem
`allow_origins=["*"]` combined with `allow_credentials=True` is a security anti-pattern (browsers actually reject the combination), and shipping wide-open CORS exposes the API to any origin. The comment says "Allow all origins for development."

## Why it matters (ship)
Basic pre-production hardening. (Note: the API also has **no authentication at all** — see code-review CR-01; that broader gap is tracked separately.)

## Locations
- `backend/main.py:216-223` (`allow_origins=["*"]`, `allow_credentials=True`, `allow_methods=["*"]`, `allow_headers=["*"]`)
- Docstring `backend/main.py:6`

## Proposed change
Add a `CORS_ALLOW_ORIGINS` (comma-separated) field to `Settings` defaulting to the local UI origin; parse into a list and pass to the middleware. Tighten `allow_methods`/`allow_headers` to the actually-used set, or gate the wildcard behind a debug flag. Document the origin in `.env.example`.

## Acceptance criteria
- [ ] No `allow_origins=["*"]` in the shipped default
- [ ] Origins are config-driven; `.env.example` documents the value
- [ ] Frontend still works against the configured origin
- [ ] `allow_credentials`/wildcard combination resolved

## Blast radius & risk
Medium — a too-strict list breaks the frontend until configured. Confirm the real UI origin.

## Dependencies
None. (Full auth is a separate, larger task — see code-review CR-01.)
