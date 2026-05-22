# EH-01 — Stop swallowing DB-commit failures when recording an application

> Category: error-handling · Effort: S · Risk: medium · **Ship-blocker: YES**
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
After a job application is actually submitted, `record_application` persists the `Application` row and flips `JobMatch` status to "applied". If the commit fails it logs `error` (no `exc_info`), rolls back, and **returns normally** — so the application went out but the system has no record, the job stays in the queue, and it can be re-applied to. The caller never learns the write failed.

## Why it matters (ship)
Silent data-integrity loss on the most important write in the product (proof an application was sent). Causes duplicate applications.

## Locations
- `backend/applier/engine.py:295-297`
  ```python
  except Exception as exc:
      logger.error("Failed to record application: %s", exc)
      await db.rollback()
  ```

## Proposed change
Log with `exc_info=True`, then **re-raise** (or surface a typed `ApplicationRecordError`) after rollback so the caller can mark the inconsistency / alert the user that the apply succeeded but tracking failed.

## Acceptance criteria
- [ ] Commit failure is logged with stack and propagated (not swallowed)
- [ ] Caller path handles the raised error (no false success)
- [ ] Added/updated test asserts the exception surfaces

## Blast radius & risk
Callers of `record_application` must handle the raised error — but the current silent path is strictly worse for integrity. Verify the apply flow in `engine.py`/`auto_apply.py` reports the failure to the UI.

## Dependencies
Consider the typed-exception approach from EH-07.
