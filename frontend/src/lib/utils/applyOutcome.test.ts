/** Review I10: render the domain outcome, not "HTTP 200 means success". */
import { describe, expect, it } from 'vitest';

import { describeApplyResult } from './applyOutcome';

describe('describeApplyResult', () => {
	it('confirmed is a success', () => {
		expect(describeApplyResult({ status: 'confirmed', method: 'auto', message: 'ok' }).ok).toBe(true);
	});

	it('awaiting the user is not "applied" and links to the job', () => {
		const d = describeApplyResult({
			status: 'awaiting_user_submission',
			method: 'manual',
			message: 'Opened',
			next_action_url: 'https://jobs.example.com/1'
		});
		expect(d.ok).toBe(true);
		expect(d.text).toMatch(/submit/i);
		expect(d.url).toBe('https://jobs.example.com/1');
	});

	it.each(['failed', 'cancelled', 'needs_verification'])('%s is reported as not done', (status) => {
		const d = describeApplyResult({ status, method: 'auto', message: 'why' });
		expect(d.ok).toBe(false);
		expect(d.text).toContain('why');
	});
});
