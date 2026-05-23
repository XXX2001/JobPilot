# EH-06 — Tighten the WebSocket receive loop (no swallow-and-spin)

> Category: error-handling · Effort: M · Risk: low-medium · Ship-blocker: no
> Part of: [Naming & Standards backlog](../INDEX.md)

## Problem
In the websocket loop, `receive_text()` failures other than `WebSocketDisconnect` hit `except Exception: continue`, which can busy-loop on a persistently failing socket. The outer message-processing `except Exception: pass` and the status-replay `except Exception: pass` discard everything; handler errors are logged only at `debug`.

## Why it matters (ship)
A wedged socket can spin a CPU; dropped client messages are undiagnosable.

## Locations
- `backend/api/ws.py:172-173` (`except Exception: continue`)
- `:187-188` (`except Exception: pass`), `:164-165` (`except Exception: pass`)
- `:185-186` (`logger.debug`)
- `ConnectionManager` serialization fallbacks `:113-114`, `:134-135`

## Proposed change
On the receive `except`, `logger.debug` then `break` (or count failures and break after N) instead of unconditional `continue`. Bump serialization-fallback to `logger.warning`; bump handler-failure log to `warning`.

## Acceptance criteria
- [ ] No unconditional `continue` on a persistently failing receive
- [ ] Serialization/handler failures are logged at `warning`
- [ ] Reconnect behavior verified on the client

## Blast radius & risk
Changing `continue`→`break` alters loop lifetime — verify the frontend reconnects cleanly.

## Dependencies
None.
