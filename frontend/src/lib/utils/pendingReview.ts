/**
 * Pending apply-review persistence (N1-T1).
 *
 * When an auto-apply pauses awaiting confirmation, the backend emits an
 * `apply_review` WS message and the queue page opens a confirm modal. If the
 * client then loses its WebSocket (reconnect) or reloads the page, that
 * in-flight review is silently lost. To recover it we mirror the awaiting job
 * ids into `sessionStorage` and, on (re)connect/mount, re-fetch the engine
 * snapshot via `GET /api/applications/{job_id}/review-state`.
 *
 * This module is intentionally small and pure: storage access is isolated to
 * `loadPendingReviewIds` / `savePendingReviewIds`; the add/remove helpers are
 * pure read-modify-return functions, and `reviewStateToModal` is a pure mapper.
 */

export const STORAGE_KEY = 'jobpilot:pending-reviews';

/** Modal shape consumed by the queue page's `confirmModal` state. */
export interface ReviewModal {
	jobId: number;
	method: string;
	fields: Record<string, string>;
	screenshot?: string;
}

/** Resolve `sessionStorage` only when running in a browser context. */
function getStorage(): Storage | null {
	if (typeof window === 'undefined') return null;
	try {
		return window.sessionStorage ?? null;
	} catch {
		// Accessing storage can throw (e.g. disabled cookies / privacy mode).
		return null;
	}
}

/** Keep only finite numbers, de-duplicated, order-preserving. */
function sanitize(ids: unknown): number[] {
	if (!Array.isArray(ids)) return [];
	const out: number[] = [];
	for (const value of ids) {
		if (typeof value === 'number' && Number.isFinite(value) && !out.includes(value)) {
			out.push(value);
		}
	}
	return out;
}

/**
 * Read the persisted pending-review ids. Tolerates a missing key, corrupt
 * JSON, or a non-array value by returning `[]`. SSR-safe.
 */
export function loadPendingReviewIds(): number[] {
	const storage = getStorage();
	if (!storage) return [];
	const raw = storage.getItem(STORAGE_KEY);
	if (raw === null) return [];
	try {
		return sanitize(JSON.parse(raw));
	} catch {
		return [];
	}
}

/** Persist a de-duplicated, numbers-only array as JSON. SSR-safe no-op. */
export function savePendingReviewIds(ids: number[]): void {
	const storage = getStorage();
	if (!storage) return;
	storage.setItem(STORAGE_KEY, JSON.stringify(sanitize(ids)));
}

/** Return a new array with `id` appended, de-duplicated. Does not touch storage. */
export function addPendingReviewId(ids: number[], id: number): number[] {
	return ids.includes(id) ? ids.slice() : [...ids, id];
}

/** Return a new array with `id` removed. Does not touch storage. */
export function removePendingReviewId(ids: number[], id: number): number[] {
	return ids.filter((existing) => existing !== id);
}

/**
 * Map the HTTP `review-state` snapshot
 * `{ job_id, filled_fields, screenshot_b64 }` to the modal shape.
 *
 * Note the key difference: the HTTP snapshot uses `screenshot_b64`, whereas the
 * WS `apply_review` message uses `screenshot_base64`. Returns `null` when the
 * payload is null, not an object, or missing `job_id`.
 */
export function reviewStateToModal(payload: unknown): ReviewModal | null {
	if (payload === null || typeof payload !== 'object') return null;
	const snapshot = payload as {
		job_id?: unknown;
		filled_fields?: unknown;
		screenshot_b64?: unknown;
	};
	if (typeof snapshot.job_id !== 'number') return null;
	const fields =
		typeof snapshot.filled_fields === 'object' && snapshot.filled_fields !== null
			? (snapshot.filled_fields as Record<string, string>)
			: {};
	return {
		jobId: snapshot.job_id,
		method: 'auto',
		fields,
		screenshot: (snapshot.screenshot_b64 as string | null) ?? undefined
	};
}
