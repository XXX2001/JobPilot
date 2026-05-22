# NM-05 — Stop calling private `ScraplingFetcher._clean_html` cross-module

> Category: naming (encapsulation) · Effort: S · Risk: low · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
`api/queue.py:309` calls `fetcher._clean_html(html)` — a leading-underscore "private" method invoked from another module. The `_` prefix now lies about the method's visibility.

## Why it matters (ship)
Reaching into another object's private API is a maintainability hazard (the owner can't safely change `_clean_html`) and signals a missing public boundary.

## Locations
- Definition: `backend/scraping/scrapling_fetcher.py:308` `_clean_html`
- Internal call: `scrapling_fetcher.py:120`
- **Cross-module call**: `backend/api/queue.py:309`
- Doc reference: `applier/form_filler.py:397`

## Proposed change
Promote to a public `clean_html()` method (if it's a legitimate public utility), or add a thin public wrapper and keep `_clean_html` internal. Update the `api/queue.py` call site.

## Acceptance criteria
- [ ] No cross-module access to a `_`-prefixed method remains
- [ ] `api/queue.py` enrichment path still produces the same cleaned HTML
- [ ] Tests pass

## Blast radius & risk
1 def + 2 call sites. No external contract. Small.

## Dependencies
None.
