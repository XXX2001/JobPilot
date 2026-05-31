/**
 * Tests for the pending apply-review persistence helpers (N1-T1).
 *
 * These helpers let the queue page recover an in-flight `apply_review` modal
 * after a WebSocket reconnect or a full page reload: the awaiting job ids are
 * mirrored into `sessionStorage`, and the HTTP `review-state` snapshot is
 * mapped back into the modal shape.
 *
 * `sessionStorage` is stubbed with a tiny in-memory object on both `globalThis`
 * and `window` so the SSR-guarded storage paths run under the node test env.
 */

import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import {
	STORAGE_KEY,
	addPendingReviewId,
	loadPendingReviewIds,
	removePendingReviewId,
	reviewStateToModal,
	savePendingReviewIds
} from './pendingReview';

function makeStorageStub(): Storage {
	const store = new Map<string, string>();
	return {
		get length() {
			return store.size;
		},
		clear: () => store.clear(),
		getItem: (key: string) => (store.has(key) ? store.get(key)! : null),
		key: (index: number) => Array.from(store.keys())[index] ?? null,
		removeItem: (key: string) => void store.delete(key),
		setItem: (key: string, value: string) => void store.set(key, String(value))
	};
}

beforeEach(() => {
	const stub = makeStorageStub();
	(globalThis as any).window = globalThis;
	(globalThis as any).sessionStorage = stub;
});

afterEach(() => {
	delete (globalThis as any).sessionStorage;
	delete (globalThis as any).window;
});

describe('loadPendingReviewIds / savePendingReviewIds', () => {
	it('round-trips a saved list', () => {
		savePendingReviewIds([1, 2, 3]);
		expect(loadPendingReviewIds()).toEqual([1, 2, 3]);
	});

	it('returns [] when the key is missing', () => {
		expect(loadPendingReviewIds()).toEqual([]);
	});

	it('returns [] on corrupt JSON', () => {
		sessionStorage.setItem(STORAGE_KEY, '{not valid json');
		expect(loadPendingReviewIds()).toEqual([]);
	});

	it('returns [] when the stored value is not an array', () => {
		sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ foo: 'bar' }));
		expect(loadPendingReviewIds()).toEqual([]);
	});

	it('keeps only finite numbers from the stored array', () => {
		sessionStorage.setItem(STORAGE_KEY, JSON.stringify([1, 'two', null, 3, NaN]));
		expect(loadPendingReviewIds()).toEqual([1, 3]);
	});

	it('persists a de-duplicated, numbers-only array', () => {
		savePendingReviewIds([1, 1, 2, NaN as unknown as number, 2, 3]);
		expect(loadPendingReviewIds()).toEqual([1, 2, 3]);
	});
});

describe('addPendingReviewId / removePendingReviewId', () => {
	it('adds a new id without mutating storage', () => {
		expect(addPendingReviewId([1, 2], 3)).toEqual([1, 2, 3]);
	});

	it('dedups when adding an existing id', () => {
		expect(addPendingReviewId([1, 2], 2)).toEqual([1, 2]);
	});

	it('removes an id', () => {
		expect(removePendingReviewId([1, 2, 3], 2)).toEqual([1, 3]);
	});

	it('is a no-op when removing an absent id', () => {
		expect(removePendingReviewId([1, 2, 3], 9)).toEqual([1, 2, 3]);
	});
});

describe('reviewStateToModal', () => {
	it('maps the HTTP snapshot to the modal shape', () => {
		const modal = reviewStateToModal({
			job_id: 42,
			filled_fields: { name: 'Ada' },
			screenshot_b64: 'abc123'
		});
		expect(modal).toEqual({
			jobId: 42,
			method: 'auto',
			fields: { name: 'Ada' },
			screenshot: 'abc123'
		});
	});

	it('defaults fields to {} when filled_fields is missing', () => {
		const modal = reviewStateToModal({ job_id: 7, screenshot_b64: null });
		expect(modal).toEqual({ jobId: 7, method: 'auto', fields: {}, screenshot: undefined });
	});

	it('leaves screenshot undefined when screenshot_b64 is null', () => {
		const modal = reviewStateToModal({ job_id: 7, filled_fields: {}, screenshot_b64: null });
		expect(modal?.screenshot).toBeUndefined();
	});

	it('returns null for a null payload', () => {
		expect(reviewStateToModal(null)).toBeNull();
	});

	it('returns null for a non-object payload', () => {
		expect(reviewStateToModal('nope')).toBeNull();
	});

	it('returns null when job_id is missing', () => {
		expect(reviewStateToModal({ filled_fields: {}, screenshot_b64: null })).toBeNull();
	});
});
