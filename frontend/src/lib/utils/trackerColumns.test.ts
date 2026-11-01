import { describe, expect, it } from 'vitest';

import { columnFor, needsUserAction } from './trackerColumns';

describe('columnFor (W06: preparation is not an application)', () => {
	it.each([
		['awaiting_submission', 'to_submit'],
		['needs_verification', 'to_submit'],
		['pending', 'to_submit'],
		['applied', 'applied'],
		['interview', 'interview'],
		['offer', 'offer'],
		['rejected', 'rejected'],
		['failed', 'not_submitted'],
		['cancelled', 'not_submitted']
	])('%s → %s', (status, column) => {
		expect(columnFor(status)).toBe(column);
	});

	it('never files an unknown status under Applied', () => {
		expect(columnFor('something_new')).toBe('to_submit');
	});

	it('offers "Mark submitted" only for prepared or unverified rows', () => {
		expect(needsUserAction('awaiting_submission')).toBe(true);
		expect(needsUserAction('needs_verification')).toBe(true);
		expect(needsUserAction('applied')).toBe(false);
		expect(needsUserAction('pending')).toBe(false);
	});
});
