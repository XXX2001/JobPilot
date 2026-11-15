/**
 * Tests for the `/onboarding` gating helpers (M2-T4).
 *
 * These cover the two pieces of non-trivial logic extracted from the stepper
 * page: which step a returning user should resume on (`firstIncompleteStep`)
 * and whether the root dashboard should auto-redirect to onboarding
 * (`shouldAutoRedirect`). Node env, mirrors `src/lib/api.test.ts`.
 */

import { describe, expect, it } from 'vitest';

import { ApiError } from '$lib/api';
import type { SetupStatus } from '$lib/types/api';
import { firstIncompleteStep, passwordSaveError, shouldAutoRedirect } from './onboarding';

describe('passwordSaveError', () => {
	it.each([400, 422])('shows length guidance for a validation error (%i)', (status) => {
		expect(passwordSaveError(new ApiError(status, 'sensitive response body'))).toBe(
			'Enter 8–128 characters'
		);
	});

	it.each([401, 403, 404, 409, 429, 500, 503])(
		'shows retry guidance for other API failures (%i) without exposing the body',
		(status) => {
			expect(passwordSaveError(new ApiError(status, 'sensitive response body'))).toBe(
				'Could not save the password; try again.'
			);
		}
	);

	it.each([new TypeError('Failed to fetch'), new Error('API error 422: sensitive value'), null])(
		'shows retry guidance for network or unrecognized failures',
		(error) => {
			expect(passwordSaveError(error)).toBe('Could not save the password; try again.');
		}
	);
});

function makeStatus(overrides: Partial<SetupStatus> = {}): SetupStatus {
	return {
		llm_key_set: false,
		adzuna_key_set: false,
		tectonic_found: false,
		base_cv_uploaded: false,
		setup_complete: false,
		auto_submit_enabled: false,
		...overrides
	};
}

describe('firstIncompleteStep', () => {
	const profile = { full_name: 'Alex Applicant', email: 'alex@example.com' };
	const toolsReady = makeStatus({ llm_key_set: true, tectonic_found: true });

	it.each([
		{ ...profile, full_name: '' },
		{ ...profile, full_name: '   ' },
		{ ...profile, email: '' },
		{ ...profile, email: ' \t ' },
		null
	])('returns step 1 when name or email is missing: %j', (incompleteProfile) => {
		expect(firstIncompleteStep(toolsReady, incompleteProfile)).toBe(1);
	});

	it('returns step 1 when the LLM key is missing', () => {
		expect(
			firstIncompleteStep(makeStatus({ llm_key_set: false, tectonic_found: true }), profile)
		).toBe(1);
	});

	it('returns step 1 when tectonic is not found', () => {
		expect(
			firstIncompleteStep(makeStatus({ llm_key_set: true, tectonic_found: false }), profile)
		).toBe(1);
	});

	it('returns step 2 when profile and tools are ready but the CV is not uploaded', () => {
		expect(
			firstIncompleteStep(
				makeStatus({ llm_key_set: true, tectonic_found: true, base_cv_uploaded: false }),
				profile
			)
		).toBe(2);
	});

	it('falls through to step 3 once all status-backed prerequisites are met', () => {
		expect(
			firstIncompleteStep(
				makeStatus({ llm_key_set: true, tectonic_found: true, base_cv_uploaded: true }),
				profile
			)
		).toBe(3);
	});

	it('does not require optional profile fields, Adzuna, a password, or browser sign-ins', () => {
		expect(firstIncompleteStep(toolsReady, profile)).toBe(2);
	});
});

describe('shouldAutoRedirect', () => {
	it('redirects when setup is incomplete and not yet dismissed', () => {
		expect(shouldAutoRedirect(makeStatus({ setup_complete: false }), false)).toBe(true);
	});

	it('does not redirect once dismissed this session', () => {
		expect(shouldAutoRedirect(makeStatus({ setup_complete: false }), true)).toBe(false);
	});

	it('does not redirect when setup is already complete', () => {
		expect(shouldAutoRedirect(makeStatus({ setup_complete: true }), false)).toBe(false);
	});

	it('does not redirect when complete even if somehow dismissed', () => {
		expect(shouldAutoRedirect(makeStatus({ setup_complete: true }), true)).toBe(false);
	});
});
