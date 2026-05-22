# EH-02 — Don't report success when the assisted-apply agent throws

> Category: error-handling · Effort: S · Risk: low-medium · **Ship-blocker: YES**
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
When the browser-use agent raises, the code logs an error, stops the browser, then **falls through and returns** `ApplicationResult(status="assisted", message="Form pre-filled via Gemini agent. Please review…")`. The user is told the form is pre-filled and waiting in a browser that has actually been stopped.

## Why it matters (ship)
Misleading success on a real failure — the user goes looking for a filled form that doesn't exist. Erodes trust in the apply flow.

## Locations
- `backend/applier/assisted_apply.py:216-233`
  ```python
  except Exception as exc:
      logger.error("Assisted apply agent failed for %s: %s", apply_url, exc)
      try: await browser.stop()
      except Exception: pass
  # ...falls through to:
  return ApplicationResult(status="assisted", method="assisted", message="Form pre-filled…")
  ```

## Proposed change
On exception, return a failure result (`status="cancelled"` or `"error"`, `message="Assisted apply failed: …"`) and log with `exc_info=True`. Only return the "assisted/review" result on the **success** path.

## Acceptance criteria
- [ ] Failure path returns a non-success status
- [ ] Log includes stack (`exc_info=True`)
- [ ] UI handles the non-"assisted" status (verify frontend mapping)

## Blast radius & risk
Changes the result contract on the failure path only. Confirm the frontend renders a non-`assisted` status sensibly.

## Dependencies
None.
