/**
 * Exponential reconnect backoff for the WebSocket client (M3-T2).
 *
 * Pure function — no timers, no module state — so it is trivially testable and
 * reusable. The WS store owns the attempt counter and the actual scheduling.
 */

export interface BackoffOptions {
	/** Delay for attempt 0, before exponential growth. Default 1000 ms. */
	baseMs?: number;
	/** Hard ceiling applied to the exponential value. Default 30000 ms. */
	maxMs?: number;
	/** Whether to apply full jitter. Default true. */
	jitter?: boolean;
	/**
	 * Injectable RNG returning a value in `[0, 1)`. Defaults to `Math.random`.
	 * Exposed so tests can make the jittered branch deterministic.
	 */
	random?: () => number;
}

/**
 * Compute the delay (ms) before the next reconnect attempt.
 *
 * The raw delay is exponential — `baseMs * 2 ** attempt` — clamped to `maxMs`.
 *
 * Jitter strategy: **full jitter**. When enabled, the returned value is a
 * uniform random pick in `[0, capped]` (`capped * random()`). This is the AWS
 * "Exponential Backoff And Jitter" full-jitter variant, chosen because it
 * spreads reconnection storms most evenly across many clients.
 */
export function nextBackoffDelay(attempt: number, opts: BackoffOptions = {}): number {
	const {
		baseMs = 1000,
		maxMs = 30000,
		jitter = true,
		random = Math.random
	} = opts;

	// Guard against negative attempts and 2 ** large overflow (Infinity * 0 = NaN).
	const safeAttempt = Math.max(0, attempt);
	const exponential = baseMs * 2 ** safeAttempt;
	const capped = Math.min(exponential, maxMs);

	if (!jitter) return capped;

	return capped * random();
}
