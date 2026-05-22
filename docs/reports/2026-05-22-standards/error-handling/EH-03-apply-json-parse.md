# EH-03 — Log credential/profile JSON parse failures in the apply endpoint

> Category: error-handling · Effort: S · Risk: very low · Ship-blocker: no (high priority)
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
Two `except Exception` blocks silently coerce malformed profile JSON to `""` / `{}`. If a user's stored `additional_info` is corrupt or non-serialisable, their LinkedIn URL, driver-license, etc. are silently dropped from the application — the apply proceeds with missing data and nobody knows why.

## Why it matters (ship)
Applications submitted with silently-missing answers, with no diagnostic trail.

## Locations
- `backend/api/applications.py:484-485` (`additional_answers = ""`)
- `backend/api/applications.py:493-494` (`answers_dict = {}`)

## Proposed change
Narrow the catch to `(TypeError, ValueError, json.JSONDecodeError)` and `logger.warning(..., exc_info=True)` including the user/profile id before falling back to the default.

## Acceptance criteria
- [ ] Malformed JSON is logged with the profile/user id and stack
- [ ] Catch is narrowed (no bare `except Exception`)
- [ ] Fallback behavior on bad data unchanged (still degrades, but visibly)

## Blast radius & risk
Very low — logging + narrower except; behavior on bad data unchanged.

## Dependencies
None.
