/**
 * Tests for `apiFetch` — the single fetch wrapper used by every page.
 *
 * The cardinal regression test here is T1a / PG-PRE: when the request body
 * is a `FormData` instance, `apiFetch` MUST NOT set a `Content-Type` header.
 * The browser (and undici on Node) generate `multipart/form-data; boundary=…`
 * themselves; overriding with `application/json` corrupts CV upload and
 * every other multipart endpoint silently.
 *
 * See `frontend/src/lib/api.ts` for the implementation. See
 * `docs/reports/2026-05-23-codebase-deep-dive/INDEX.md` (cross-cut #1) for
 * the history of this bug — it was claimed shipped twice before T1a
 * finally landed.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { apiFetch } from './api';

type CapturedCall = {
	url: string;
	init: RequestInit | undefined;
};

function installFetchStub(responseBody: unknown = {}): {
	calls: CapturedCall[];
	restore: () => void;
} {
	const calls: CapturedCall[] = [];
	const originalFetch = globalThis.fetch;

	globalThis.fetch = vi.fn(async (input: Request | string | URL, init?: RequestInit) => {
		calls.push({ url: String(input), init });
		return new Response(JSON.stringify(responseBody), {
			status: 200,
			headers: { 'Content-Type': 'application/json' }
		});
	}) as typeof fetch;

	return {
		calls,
		restore: () => {
			globalThis.fetch = originalFetch;
		}
	};
}

function headersToObject(headers: HeadersInit | undefined): Record<string, string> {
	if (!headers) return {};
	if (headers instanceof Headers) {
		const out: Record<string, string> = {};
		headers.forEach((v, k) => {
			out[k.toLowerCase()] = v;
		});
		return out;
	}
	if (Array.isArray(headers)) {
		return Object.fromEntries(headers.map(([k, v]) => [k.toLowerCase(), v]));
	}
	return Object.fromEntries(
		Object.entries(headers).map(([k, v]) => [k.toLowerCase(), String(v)])
	);
}

describe('apiFetch — Content-Type handling', () => {
	let stub: ReturnType<typeof installFetchStub>;

	beforeEach(() => {
		stub = installFetchStub({ ok: true });
	});

	afterEach(() => {
		stub.restore();
		vi.restoreAllMocks();
	});

	it('sets Content-Type: application/json for JSON bodies', async () => {
		await apiFetch('/api/foo', {
			method: 'POST',
			body: JSON.stringify({ x: 1 })
		});

		expect(stub.calls).toHaveLength(1);
		const headers = headersToObject(stub.calls[0].init?.headers);
		expect(headers['content-type']).toBe('application/json');
	});

	it('REGRESSION (T1a / PG-PRE): does NOT set Content-Type when body is FormData', async () => {
		// This is the bug the deep-dive flagged as a 4-month-standing CRIT
		// regression: hardcoding `Content-Type: application/json` for every
		// request corrupted multipart uploads (the browser needs to set the
		// boundary). The fix is to omit the header for FormData bodies and
		// let the runtime fill it in.
		const fd = new FormData();
		fd.append('file', new Blob(['hello'], { type: 'text/plain' }), 'cv.tex');

		await apiFetch('/api/settings/profile/cv-upload', {
			method: 'POST',
			body: fd
		});

		expect(stub.calls).toHaveLength(1);
		const headers = headersToObject(stub.calls[0].init?.headers);

		// The critical assertion: no Content-Type header at all. Either case is wrong:
		// - 'application/json' → the original bug (boundary missing, payload unparseable)
		// - 'multipart/form-data' without ;boundary= → also fails on the server
		expect(headers['content-type']).toBeUndefined();
	});

	it('preserves caller-provided headers alongside the default Content-Type', async () => {
		await apiFetch('/api/foo', {
			method: 'POST',
			headers: { 'X-Trace-Id': 'abc' },
			body: JSON.stringify({})
		});

		const headers = headersToObject(stub.calls[0].init?.headers);
		expect(headers['x-trace-id']).toBe('abc');
		expect(headers['content-type']).toBe('application/json');
	});

	it('preserves caller-provided headers when body is FormData', async () => {
		const fd = new FormData();
		fd.append('field', 'value');

		await apiFetch('/api/foo', {
			method: 'POST',
			headers: { 'X-Trace-Id': 'xyz' },
			body: fd
		});

		const headers = headersToObject(stub.calls[0].init?.headers);
		expect(headers['x-trace-id']).toBe('xyz');
		expect(headers['content-type']).toBeUndefined();
	});

	it('throws on non-OK response with status and body in the message', async () => {
		stub.restore();
		const originalFetch = globalThis.fetch;
		globalThis.fetch = vi.fn(async () => new Response('boom', { status: 500 })) as typeof fetch;

		try {
			await expect(apiFetch('/api/bad')).rejects.toThrow(/500/);
		} finally {
			globalThis.fetch = originalFetch;
		}
	});
});
