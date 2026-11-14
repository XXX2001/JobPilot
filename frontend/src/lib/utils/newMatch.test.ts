import { describe, expect, it } from 'vitest';

import type { MatchBrief, NewMatchesSection } from '$lib/types/today';
import { asWSMessage, type NewMatchMsg } from '$lib/types/ws';

import { prependNewMatch } from './newMatch';

const NOW = new Date('2026-11-14T09:00:00Z');

function brief(id: number, score: number): MatchBrief {
	return {
		id,
		job_id: id * 10,
		score,
		status: 'new',
		matched_at: '2026-11-14T08:00:00Z',
		job_title: `Job ${id}`,
		company: 'Acme'
	};
}

function section(): NewMatchesSection {
	return {
		since: '2026-11-13T09:00:00Z',
		high_confidence: [brief(1, 90)],
		worth_reviewing: [brief(2, 70)],
		skipped: [brief(3, 40)],
		total: 3
	};
}

function frame(over: Partial<NewMatchMsg> = {}): NewMatchMsg {
	return {
		type: 'new_match',
		match_id: 9,
		job_id: 99,
		title: 'Robotics Engineer',
		company: 'Mirakl',
		location: 'Paris',
		score: 87.4,
		source: 'greenhouse:mirakl',
		posted_at: '2026-11-14T08:30:00',
		url: 'https://example.test/jobs/99',
		...over
	};
}

describe('prependNewMatch', () => {
	it('prepends a new_match to its score bucket and bumps the total', () => {
		const before = section();
		const after = prependNewMatch(before, frame(), NOW);

		expect(after.high_confidence.map((m) => m.id)).toEqual([9, 1]);
		expect(after.high_confidence[0]).toEqual({
			id: 9,
			job_id: 99,
			score: 87.4,
			status: 'new',
			matched_at: '2026-11-14T09:00:00.000Z',
			job_title: 'Robotics Engineer',
			company: 'Mirakl'
		});
		expect(after.total).toBe(4);
		expect(after.worth_reviewing).toBe(before.worth_reviewing);
		expect(after.since).toBe(before.since);
		// the input is not mutated: Svelte needs a new object to react
		expect(before.high_confidence.map((m) => m.id)).toEqual([1]);
		expect(before.total).toBe(3);
	});

	it('uses the same score thresholds as the Today API', () => {
		expect(prependNewMatch(section(), frame({ score: 80 }), NOW).high_confidence[0].id).toBe(9);
		expect(prependNewMatch(section(), frame({ score: 79.9 }), NOW).worth_reviewing[0].id).toBe(9);
		expect(prependNewMatch(section(), frame({ score: 60 }), NOW).worth_reviewing[0].id).toBe(9);
		expect(prependNewMatch(section(), frame({ score: 59.9 }), NOW).skipped[0].id).toBe(9);
	});

	it('ignores a match that is already listed', () => {
		const before = section();
		expect(prependNewMatch(before, frame({ match_id: 2 }), NOW)).toBe(before);
		const once = prependNewMatch(before, frame(), NOW);
		expect(prependNewMatch(once, frame(), NOW)).toBe(once);
	});
});

describe('asWSMessage', () => {
	it('accepts a new_match frame', () => {
		expect(asWSMessage(frame())).toEqual(frame());
	});
});
