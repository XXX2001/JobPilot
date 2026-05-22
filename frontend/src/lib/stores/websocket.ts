import { writable, type Readable } from 'svelte/store';
import type { ClientMessage } from '$lib/types/ws';
// NOTE: the messages store is intentionally typed as `any[]` for now.
// Tightening to `WSMessage[]` surfaces real pre-existing vocabulary drift
// (FE-01 in docs/reports/2026-05-22-audit/04-frontend-audit.md) — see e.g.
// StatusBar.svelte handling `scraping_progress` / `matching_progress` /
// `tailoring_progress` which the backend never emits. Fix those separately,
// then change this back to `WSMessage[]` to lock in the contract.

export type WsStatus = 'connected' | 'disconnected' | 'reconnecting';

export const wsStatus = writable<WsStatus>('disconnected');

const _messages = writable<any[]>([]);
export const messages: Readable<any[]> = { subscribe: _messages.subscribe };

export const loginPrompt = writable<{ site: string; text: string } | null>(null);

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

// Callbacks invoked after a successful (re)connection
const _onConnectCallbacks: Array<() => void> = [];

/** Register a callback for WS (re)connections. Returns an unsubscribe function. */
export function onWsConnect(cb: () => void): () => void {
  _onConnectCallbacks.push(cb);
  return () => {
    const idx = _onConnectCallbacks.indexOf(cb);
    if (idx !== -1) _onConnectCallbacks.splice(idx, 1);
  };
}

export function connectWs() {
  if (typeof window === 'undefined') return;

  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }

  wsStatus.set('reconnecting');

  const baseUrl = import.meta.env.VITE_API_BASE_URL ?? '';
  let wsUrl = 'ws://localhost:8000/ws';

  if (baseUrl) {
    wsUrl = baseUrl.replace(/^http/, 'ws') + '/ws';
  } else if (typeof window !== 'undefined' && window.location.protocol) {
    wsUrl = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;
  }

  try {
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      wsStatus.set('connected');
      if (reconnectTimer) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      // Notify listeners (e.g. to re-fetch batch status)
      for (const cb of _onConnectCallbacks) {
        try { cb(); } catch {}
      }
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        _messages.update(msgs => [...msgs.slice(-199), data]);
        if (data.type === 'login_required') {
          loginPrompt.set({
            site: data.site,
            text: data.browser_window_title || `Please log into ${data.site} in the browser window, then click 'Done'.`
          });
        }
      } catch (e) {
        _messages.update(msgs => [...msgs.slice(-199), event.data]);
      }
    };

    ws.onclose = () => {
      wsStatus.set('disconnected');
      scheduleReconnect();
    };

    ws.onerror = () => {
      ws?.close();
    };
  } catch (e) {
    scheduleReconnect();
  }
}

function scheduleReconnect() {
  if (reconnectTimer) return;
  reconnectTimer = setTimeout(() => {
    reconnectTimer = null;
    connectWs();
  }, 3000);
}

export function send(data: ClientMessage | string) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(typeof data === 'string' ? data : JSON.stringify(data));
  }
}

if (typeof window !== 'undefined') {
  connectWs();
}
