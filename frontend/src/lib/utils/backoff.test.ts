/**
 * Tests for `nextBackoffDelay` — the pure function driving the WebSocket
 * client's reconnect schedule (M3-T2).
 *
 * The WS client previously reconnected with a FIXED 3000 ms delay, which
 * hammers the server during an outage. This function computes an exponential
 * backoff with a maximum cap and optional "full jitter" (random in
 * `[0, capped]`) to spread reconnection storms across many clients.
 *
 * Jitter uses an injectable RNG (`opts.random`, default `Math.random`) so the
 * randomized branch is deterministic under test.
 */

import { describe, expect, it } from 'vitest';

import { nextBackoffDelay } from './backoff';

describe('nextBackoffDelay — exponential backoff', () => {
	it('returns ~base for attempt 0 (jitter disabled)', () => {
		expect(nextBackoffDelay(0, { jitter: false })).toBe(1000);
	});

	it('uses an explicit base for attempt 0 (jitter disabled)', () => {
		expect(nextBackoffDelay(0, { baseMs: 500, jitter: false })).toBe(500);
	});

	it('grows exponentially as the attempt increases (jitter disabled)', () => {
		expect(nextBackoffDelay(1, { jitter: false })).toBe(2000);
		expect(nextBackoffDelay(2, { jitter: false })).toBe(4000);
		expect(nextBackoffDelay(3, { jitter: false })).toBe(8000);
		expect(nextBackoffDelay(4, { jitter: false })).toBe(16000);
	});

	it('caps the delay at maxMs for large attempts (jitter disabled)', () => {
		// 1000 * 2**5 = 32000 > 30000 → capped
		expect(nextBackoffDelay(5, { jitter: false })).toBe(30000);
		expect(nextBackoffDelay(50, { jitter: false })).toBe(30000);
	});

	it('respects a custom maxMs cap (jitter disabled)', () => {
		expect(nextBackoffDelay(10, { maxMs: 5000, jitter: false })).toBe(5000);
	});
});

describe('nextBackoffDelay — full jitter', () => {
	it('returns random*capped with an injected RNG', () => {
		// capped at attempt 2 with base 1000 = 4000; random 0.5 → 2000
		expect(
			nextBackoffDelay(2, { jitter: true, random: () => 0.5 })
		).toBe(2000);
	});

	it('returns 0 when the RNG returns 0', () => {
		expect(nextBackoffDelay(3, { jitter: true, random: () => 0 })).toBe(0);
	});

	it('jitters within the capped band [0, capped] when capped', () => {
		// attempt 10 → exponential overflows, capped at 30000.
		// random 0.999... → just under 30000.
		const delay = nextBackoffDelay(10, {
			jitter: true,
			random: () => 0.9999
		});
		expect(delay).toBeGreaterThanOrEqual(0);
		expect(delay).toBeLessThanOrEqual(30000);
		expect(delay).toBeCloseTo(29997, 0);
	});

	it('defaults to jitter:true, keeping delays within [0, capped]', () => {
		for (let attempt = 0; attempt < 8; attempt++) {
			const delay = nextBackoffDelay(attempt);
			const capped = Math.min(1000 * 2 ** attempt, 30000);
			expect(delay).toBeGreaterThanOrEqual(0);
			expect(delay).toBeLessThanOrEqual(capped);
		}
	});
});
