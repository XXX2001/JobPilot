# JobPilot Documentation

**AI-powered local job application assistant** — scrapes jobs, tailors your LaTeX CV surgically via Gemini, and helps you apply at scale.

---

## Documentation Index

| Document | Audience | Description |
|---|---|---|
| [Overview](overview.md) | All | Project goals, how it works, key design decisions |
| [Architecture](architecture.md) | Developers | Technical deep-dive: modules, data flow, DB schema |
| [Developer Guide](developer-guide.md) | Developers | Codebase onboarding, module map, extending the system |
| [API Overview](api-overview.md) | Developers / Integrators | All REST endpoints + WebSocket protocol |
| [Operations](operations.md) | Users / Operators | Install, configure, run, daily usage |
| [Troubleshooting](troubleshooting.md) | All | Common issues, error messages, fixes |
| [Verification & Gap Analysis](verification-gap-analysis.md) | Project / QA | Plan coverage, test evidence, identified gaps |

---

## Quick Navigation

### "I want to install and run JobPilot"
→ [Operations Guide](operations.md)

### "I want to understand how it works"
→ [Overview](overview.md) → [Architecture](architecture.md)

### "I want to extend or contribute to the codebase"
→ [Developer Guide](developer-guide.md) → [API Overview](api-overview.md)

### "Something is broken"
→ [Troubleshooting](troubleshooting.md)

### "I want to verify the implementation against the spec"
→ [Verification & Gap Analysis](verification-gap-analysis.md)

---

## Project at a Glance

```
JobPilot v0.1.0
├── Backend  FastAPI + SQLAlchemy + APScheduler  (Python 3.12+)
├── Frontend SvelteKit + TailwindCSS             (Node 18+)
├── LLM      Gemini 2.0 Flash (free tier)
├── LaTeX    Tectonic cross-platform engine
├── Browser  browser-use + Playwright Chromium
└── DB       SQLite (async, via aiosqlite)
```

**Test coverage**: 127 tests passing, 2 skipped (require live API keys), 0 failing.

**License**: MIT
