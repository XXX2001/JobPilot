/**
 * Tests for `computeScoreBreakdown` — the pure function powering the
 * "Why this score" panel on the job-detail page.
 *
 * Background on `keyword_hits`: the column is `JobMatch.keyword_hits`
 * (`Mapped[Optional[dict]]`) and is surfaced verbatim by
 * `GET /api/jobs/{job_id}/score`. The matcher does not currently populate it,
 * and the documented example (`docs/api-reference.md`) shows a flat list of
 * hit strings (`["python", "fastapi"]`), while the column annotation is a
 * dict. So the function must accept BOTH shapes robustly:
 *   - `string[]`            → every entry is a matched keyword
 *   - `Record<string, *>`   → keys whose value is truthy / count > 0 matched
 *   - `null` / `undefined`  → nothing matched
 *
 * "missing" is always derived from the configured include-keywords
 * (`SearchSettings.keywords.include`) that are absent from the hits.
 */

import { describe, expect, it } from 'vitest';

import { computeScoreBreakdown } from './scoreBreakdown';

describe('computeScoreBreakdown — keyword matching', () => {
	it('reports all keywords matched when every include-keyword is hit', () => {
		const result = computeScoreBreakdown(
			['python', 'fastapi', 'svelte'],
			['python', 'fastapi', 'svelte'],
			{ min: 50000, max: 70000 },
			40000
		);
		expect(result.matched).toEqual(['python', 'fastapi', 'svelte']);
		expect(result.missing).toEqual([]);
	});

	it('reports the missing keywords when only some are hit', () => {
		const result = computeScoreBreakdown(
			['python', 'fastapi'],
			['python', 'fastapi', 'svelte', 'docker'],
			{ min: null, max: null },
			null
		);
		expect(result.matched).toEqual(['python', 'fastapi']);
		expect(result.missing).toEqual(['svelte', 'docker']);
	});

	it('treats a dict of keyword -> truthy/count as the hit set', () => {
		const result = computeScoreBreakdown(
			{ python: true, react: false, docker: 3, kubernetes: 0 },
			['python', 'react', 'docker', 'kubernetes'],
			{ min: null, max: null },
			null
		);
		expect(result.matched).toEqual(['python', 'docker']);
		expect(result.missing).toEqual(['react', 'kubernetes']);
	});

	it('compares keywords case-insensitively', () => {
		const result = computeScoreBreakdown(
			['Python', 'FastAPI'],
			['python', 'fastapi', 'Svelte'],
			{ min: null, max: null },
			null
		);
		expect(result.missing).toEqual(['Svelte']);
	});

	it('treats null keyword_hits as nothing matched', () => {
		const result = computeScoreBreakdown(null, ['python', 'svelte'], { min: null, max: null }, null);
		expect(result.matched).toEqual([]);
		expect(result.missing).toEqual(['python', 'svelte']);
	});
});

describe('computeScoreBreakdown — salary comparison', () => {
	it('flags salary as meeting target when the job max reaches the target', () => {
		const result = computeScoreBreakdown([], [], { min: 50000, max: 70000 }, 60000);
		expect(result.salary).toEqual({
			jobMin: 50000,
			jobMax: 70000,
			target: 60000,
			meetsTarget: true
		});
	});

	it('flags salary as meeting target when only the job min reaches the target', () => {
		const result = computeScoreBreakdown([], [], { min: 65000, max: null }, 60000);
		expect(result.salary.meetsTarget).toBe(true);
	});

	it('flags salary as below target when the whole range is under it', () => {
		const result = computeScoreBreakdown([], [], { min: 30000, max: 45000 }, 60000);
		expect(result.salary).toEqual({
			jobMin: 30000,
			jobMax: 45000,
			target: 60000,
			meetsTarget: false
		});
	});

	it('returns unknown (null) when the job salary is missing', () => {
		const result = computeScoreBreakdown([], [], { min: null, max: null }, 60000);
		expect(result.salary).toEqual({
			jobMin: null,
			jobMax: null,
			target: 60000,
			meetsTarget: null
		});
	});

	it('returns unknown (null) when no target salary is configured', () => {
		const result = computeScoreBreakdown([], [], { min: 50000, max: 70000 }, null);
		expect(result.salary.meetsTarget).toBeNull();
	});
});
