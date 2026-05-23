/**
 * Daily application limit store.
 *
 * Fetches GET /api/applications/limit-status on subscribe and refreshes every
 * 60 seconds. Also refreshes immediately whenever the WebSocket delivers an
 * `apply_result` message — this keeps the pill in sync without waiting for the
 * next poll tick after an application is submitted.
 *
 * Colour thresholds (hardcoded, aesthetic only):
 *   gray   ≤ 6
 *   amber  7–8
 *   red    9–10
 */

import { writable, type Readable } from 'svelte/store';
import { apiFetch } from '$lib/api';
import { messages } from '$lib/stores/websocket';
import type { WSMessage } from '$lib/types/ws';

export interface DailyLimitStatus {
	used: number;
	limit: number;
	resets_at: string;
}

const POLL_INTERVAL_MS = 60_000;

function createDailyLimitStore(): Readable<DailyLimitStatus | null> {
	const { subscribe, set } = writable<DailyLimitStatus | null>(null);

	let pollTimer: ReturnType<typeof setInterval> | null = null;
	let wsUnsub: (() => void) | null = null;
	let refCount = 0;

	async function refresh(): Promise<void> {
		try {
			const data = await apiFetch<DailyLimitStatus>('/api/applications/limit-status');
			set(data);
		} catch {
			// Leave the previous value in place on network error
		}
	}

	function start(): void {
		refresh();
		pollTimer = setInterval(refresh, POLL_INTERVAL_MS);

		// Invalidate on every apply_result WS message
		wsUnsub = messages.subscribe((msgs: WSMessage[]) => {
			const last = msgs.at(-1);
			if (last && last.type === 'apply_result') {
				void refresh();
			}
		});
	}

	function stop(): void {
		if (pollTimer !== null) {
			clearInterval(pollTimer);
			pollTimer = null;
		}
		if (wsUnsub !== null) {
			wsUnsub();
			wsUnsub = null;
		}
	}

	return {
		subscribe(run, invalidate?) {
			if (refCount === 0) start();
			refCount++;
			const unsub = subscribe(run, invalidate);
			return () => {
				unsub();
				refCount--;
				if (refCount === 0) stop();
			};
		}
	};
}

export const dailyLimit = createDailyLimitStore();

/**
 * Map a `used` count to a Tailwind text-colour class.
 * Thresholds: gray ≤ 6, amber 7–8, red 9+.
 */
export function limitColour(used: number): string {
	if (used >= 9) return 'text-red-500';
	if (used >= 7) return 'text-amber-500';
	return 'text-muted-foreground';
}
