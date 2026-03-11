import { writable, type Readable } from 'svelte/store';

export type WsStatus = 'connected' | 'disconnected' | 'reconnecting';

export const wsStatus = writable<WsStatus>('disconnected');

const _messages = writable<any[]>([]);
export const messages: Readable<any[]> = { subscribe: _messages.subscribe };

let ws: WebSocket | null = null;
let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

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
    };

    ws.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        _messages.update(msgs => [...msgs.slice(-199), data]);
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

export function send(data: any) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(typeof data === 'string' ? data : JSON.stringify(data));
  }
}

if (typeof window !== 'undefined') {
  connectWs();
}
