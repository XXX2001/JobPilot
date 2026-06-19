/**
 * Minimal global toast queue.
 *
 * Phase 1 design: a small writable list with a default auto-dismiss timer.
 * Components subscribe via `toasts` and call `pushToast()` to add. The
 * rendered toast UI lives in `+layout.svelte` so every route surface
 * picks up notifications (e.g. inbound Gmail).
 */
import { writable } from 'svelte/store';

export type ToastKind = 'info' | 'success' | 'warning' | 'error';

export interface Toast {
	id: number;
	message: string;
	kind: ToastKind;
	href?: string;
	hrefLabel?: string;
}

const _toasts = writable<Toast[]>([]);
export const toasts = { subscribe: _toasts.subscribe };

let _nextId = 1;

export function pushToast(
	message: string,
	options: { kind?: ToastKind; duration?: number; href?: string; hrefLabel?: string } = {}
): number {
	const id = _nextId++;
	const kind = options.kind ?? 'info';
	const duration = options.duration ?? 5000;
	_toasts.update((list) => [...list, { id, message, kind, href: options.href, hrefLabel: options.hrefLabel }]);
	if (duration > 0 && typeof window !== 'undefined') {
		setTimeout(() => dismissToast(id), duration);
	}
	return id;
}

export function dismissToast(id: number): void {
	_toasts.update((list) => list.filter((t) => t.id !== id));
}
