/**
 * W07: a review approval is an acknowledged HTTP command naming the reviewed
 * snapshot; a lost response is retried with the SAME idempotency key.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

import { submitReviewDecision } from './reviewDecision';

const original = globalThis.fetch;
afterEach(() => {
	globalThis.fetch = original;
});

function stub(responses: Array<() => Response | Promise<Response>>) {
	const bodies: any[] = [];
	globalThis.fetch = vi.fn(async (input: Request | string | URL, init?: RequestInit) => {
		if (String(input).endsWith('/api/session')) return new Response('{}');
		bodies.push(JSON.parse(String(init?.body)));
		const next = responses.shift();
		if (!next) throw new Error('unexpected call');
		return next();
	}) as typeof fetch;
	return bodies;
}

const review = { jobId: 7, reviewId: 'r1', revision: 2 };

describe('submitReviewDecision', () => {
	it('sends review identity, decision and corrections, and returns the ack', async () => {
		const bodies = stub([() => new Response(JSON.stringify({ decision: 'approve', revision: 2 }))]);
		const ack = await submitReviewDecision(review, 'approve', { '#n': 'x' }, { delayMs: 0 });
		expect(ack.decision).toBe('approve');
		expect(bodies[0]).toMatchObject({ review_id: 'r1', revision: 2, decision: 'approve', fields: { '#n': 'x' } });
		expect(bodies[0].idempotency_key.length).toBeGreaterThanOrEqual(8);
	});

	it('retries a network failure with the same idempotency key', async () => {
		const bodies = stub([
			() => Promise.reject(new TypeError('Failed to fetch')),
			() => new Response(JSON.stringify({ decision: 'approve' }))
		]);
		await submitReviewDecision(review, 'approve', {}, { delayMs: 0 });
		expect(bodies).toHaveLength(2);
		expect(bodies[1].idempotency_key).toBe(bodies[0].idempotency_key);
	});

	it('does not retry a conflict (stale or already-decided review)', async () => {
		const bodies = stub([() => new Response('{"detail":"out of date"}', { status: 409 })]);
		await expect(submitReviewDecision(review, 'approve', {}, { delayMs: 0 })).rejects.toMatchObject({ status: 409 });
		expect(bodies).toHaveLength(1);
	});
});
