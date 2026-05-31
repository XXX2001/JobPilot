/**
 * Tests for the `/onboarding` gating helpers (M2-T4).
 *
 * These cover the two pieces of non-trivial logic extracted from the stepper
 * page: which step a returning user should resume on (`firstIncompleteStep`)
 * and whether the root dashboard should auto-redirect to onboarding
 * (`shouldAutoRedirect`). Node env, mirrors `src/lib/api.test.ts`.
 */

import { describe, expect, it } from 'vitest';

import type { SetupStatus } from '$lib/types/api';
import { firstIncompleteStep, shouldAutoRedirect } from './onboarding';

function makeStatus(overrides: Partial<SetupStatus> = {}): SetupStatus {
	return {
		gemini_key_set: false,
		adzuna_key_set: false,
		tectonic_found: false,
		base_cv_uploaded: false,
		setup_complete: false,
		...overrides
	};
}

describe('firstIncompleteStep', () => {
	it('returns step 1 when the Gemini key is missing', () => {
		expect(firstIncompleteStep(makeStatus({ gemini_key_set: false, tectonic_found: true }))).toBe(
			1
		);
	});

	it('returns step 1 when tectonic is not found', () => {
		expect(firstIncompleteStep(makeStatus({ gemini_key_set: true, tectonic_found: false }))).toBe(
			1
		);
	});

	it('returns step 2 when keys are set but the CV is not uploaded', () => {
		expect(
			firstIncompleteStep(
				makeStatus({ gemini_key_set: true, tectonic_found: true, base_cv_uploaded: false })
			)
		).toBe(2);
	});

	it('falls through to step 3 once all status-backed prerequisites are met', () => {
		expect(
			firstIncompleteStep(
				makeStatus({ gemini_key_set: true, tectonic_found: true, base_cv_uploaded: true })
			)
		).toBe(3);
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
