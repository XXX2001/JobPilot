# Code review

How to review changes to JobPilot before they merge. This page describes the quality gates a change must clear and what reviewers look for; it does not track individual findings.

## Quality gates

Run these locally before opening or approving a change. All must pass.

| Check | Command | Expected |
| --- | --- | --- |
| Backend tests | `uv run pytest` | Green (no failures) |
| Backend lint | `uv run ruff check backend/ tests/` | Clean |
| Frontend types/lint | `cd frontend && npm run check` | 0 errors / 0 warnings |
| Frontend tests | `cd frontend && npm run test` | Green (Vitest) |

## What reviewers look for

- **Correctness** — the change does what it claims; edge cases and error paths are handled rather than silently swallowed.
- **Security** — user-controlled input that reaches the filesystem, a subprocess, or an LLM prompt is validated/sanitized; credentials stay encrypted at rest; CORS and bind-host settings stay restrictive by default. See [Security](modules/security.md) for the prompt-injection sanitizer and credential-encryption details.
- **Resource safety** — browser contexts, Playwright instances, subprocesses, and DB sessions are always closed (use context managers or `finally` blocks); long-running calls have timeouts.
- **Async hygiene** — no blocking I/O on the event loop without an executor; background tasks are tracked so their exceptions surface.
- **Provider neutrality** — LLM call sites depend on the factory/protocol layer, not a concrete provider. See [LLM providers](llm-providers.md).
- **API contracts** — request/response models are typed and validated at the boundary; status fields use `Literal`/enum types, not free-form strings.
- **Tests** — new behavior is covered; bug fixes add a regression test.

## Related

- [Architecture](architecture.md)
- [Security](modules/security.md)
- [Configuration](configuration.md)
- [Development / Contributing](../CONTRIBUTING.md)
- [Docs index](index.md)
</content>
</invoke>
