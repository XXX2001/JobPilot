# Store patterns

This directory contains Svelte stores. Two patterns are in use. Pick the right one
before adding a third store.

---

## Pattern 1 — Ref-counted writable

**Canonical example:** `frontend/src/lib/stores/dailyLimit.ts`

A factory function calls `writable()` internally and wraps its `subscribe` method
to count active subscribers. When the count goes from 0 → 1, side effects start
(polling timer, WS subscription). When it drops back to 0, they stop. The exported
value is a `Readable`, so consumers can never call `.set()` directly.

```ts
function createFooStore(): Readable<Foo | null> {
  const { subscribe, set } = writable<Foo | null>(null);
  let refCount = 0;
  let timer: ReturnType<typeof setInterval> | null = null;

  function start() {
    refresh();
    timer = setInterval(refresh, INTERVAL);
  }
  function stop() {
    if (timer) { clearInterval(timer); timer = null; }
  }

  return {
    subscribe(run, invalidate?) {
      if (refCount === 0) start();
      refCount++;
      const unsub = subscribe(run, invalidate);
      return () => { unsub(); if (--refCount === 0) stop(); };
    }
  };
}

export const foo = createFooStore();
```

### When to use this pattern

- The store fetches data from an API endpoint on a timer or in response to events.
- You want polling to stop automatically when no component is mounted (e.g. the user
  navigated away), saving network and CPU.
- The store reacts to WS messages (like `dailyLimit.ts` subscribing to `messages`
  and calling `refresh()` on `apply_result`) — that subscription also needs cleanup.

Rule of thumb: if your store calls `setInterval`, `setTimeout`, or subscribes to
another store, it belongs in this pattern.

---

## Pattern 2 — Module-level state

**Canonical example:** `frontend/src/lib/utils/hotkeys.ts`

State lives at module scope: plain mutable variables (`registrations`, `_currentRoute`,
`nextId`) plus a handful of `writable` stores for the pieces that Svelte components
need to react to (`helpOpen`, `activeBindings`). Lifecycle is not managed — the
module is loaded once for the lifetime of the page and stays alive forever. Public
API is a set of exported functions (`register`, `deregister`, `setCurrentRoute`,
`handle`) rather than a single store value.

```ts
// module-level state — lives for the whole page lifetime
const registrations: Registration[] = [];
let _currentRoute = '/';

export const helpOpen = writable(false);

export function register(routeId: string, bindings: BindingMap): Handle {
  // mutates registrations directly
}
export function deregister(handle: Handle): void { ... }
```

### When to use this pattern

- The state is a true singleton: a global event registry, a key→handler map, a
  callback list, a WebSocket connection.
- There is no meaningful "nobody is subscribed right now" state — the dispatcher
  still needs to exist even when no component is on screen.
- The "store" is really a service with imperative API calls, not a reactive data
  source. Subscribers (if any) are secondary.

Rule of thumb: if you are building a dispatcher, a registry, or an event bus,
use module-level state.

---

## Pattern 3 — Module-level writable with imperative connect (hybrid)

**Canonical example:** `frontend/src/lib/stores/websocket.ts`

This is the original pre-sprint WS module. It sits between the two patterns above.
`wsStatus`, `messages`, and `loginPrompt` are plain `writable` stores exported
directly (no factory, no ref-counting). The socket lifecycle is managed by the
module-level `connectWs()` / `scheduleReconnect()` functions and a module-level
`ws` variable. `connectWs()` is called once at module load (`if (typeof window !==
'undefined') connectWs()`), and the reconnect loop runs forever.

```ts
// module-level socket state
let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

export const wsStatus = writable<WsStatus>('disconnected');
const _messages = writable<any[]>([]);
export const messages: Readable<any[]> = { subscribe: _messages.subscribe };

export function connectWs() { ... }   // called once at startup
export function send(data: ClientMessage): void { ... }
```

Use this hybrid when the connection is a true singleton (only one WS per page) and
cleanup on last-unsubscribe would be wrong — you never want the socket to close just
because the user navigated between pages. In every other way it resembles the
module-level pattern: imperative API, process-wide lifetime.

---

## What NOT to do

**Do not put `setInterval` inside a component.** Intervals created in `onMount`
without a matching `clearInterval` in `onDestroy` leak across navigations in
SvelteKit's client-side router. Move the interval into a ref-counted store instead.

```svelte
<!-- BAD: timer leaks when the component unmounts -->
<script>
  onMount(() => {
    setInterval(fetchData, 60_000); // no cleanup
  });
</script>
```

**Do not store derived/computed data in module-level variables when ref-counting
would let you clean up.** If you find yourself writing a module-level `let cache =
null` that you populate from an API call and never invalidate, that data belongs in
a ref-counted writable — then it refreshes when the next subscriber arrives after an
idle period.

**Do not export the inner `writable` from a ref-counted store.** The whole point is
that only the store's own `start`/`stop` logic can call `set()`. Export a `Readable`
so callers cannot bypass the lifecycle.
