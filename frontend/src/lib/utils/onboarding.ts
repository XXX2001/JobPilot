/**
 * Pure helpers powering the `/onboarding` first-run stepper (M2-T4).
 *
 * The onboarding flow has four steps:
 *   1. You           — profile name/email + `llm_key_set` + `tectonic_found`
 *   2. CV upload     — driven by `base_cv_uploaded`
 *   3. Keywords      — no `SetupStatus` flag (UI-only saving)
 *   4. Source + run  — no `SetupStatus` flag (UI-only action)
 *
 * Only steps 1 and 2 are reflected in `SetupStatus`, so the "first incomplete
 * step" derivation falls through to step 3 once the status-backed prerequisites
 * are met. These functions are extracted from the Svelte page so the gating /
 * step-resume logic stays unit-testable in a Node env (mirrors `api.test.ts`).
 */

import { ApiError } from '$lib/api';
import type { ProfileDetails, SetupStatus } from '$lib/types/api';

/** sessionStorage key recording that the user dismissed / saw onboarding. */
export const ONBOARDING_DISMISSED_KEY = 'jobpilot_onboarding_dismissed';

export const ONBOARDING_TOTAL_STEPS = 4;

/** Safe password-save feedback: never expose an API body that could contain the value. */
export function passwordSaveError(error: unknown): string {
	return error instanceof ApiError && (error.status === 400 || error.status === 422)
		? 'Enter 8–128 characters'
		: 'Could not save the password; try again.';
}

/**
 * Given setup status and profile details, return the first step that still
 * needs the user's attention. Steps 3 (keywords) and 4 (source/run) are not
 * encoded in `SetupStatus`, so when both status-backed prerequisites are
 * satisfied we resume at step 3 — the first actionable UI-only step.
 */
export function firstIncompleteStep(
	status: SetupStatus,
	profile: Pick<ProfileDetails, 'full_name' | 'email'> | null
): number {
	if (
		!profile?.full_name.trim() ||
		!profile.email.trim() ||
		!status.llm_key_set ||
		!status.tectonic_found
	)
		return 1;
	if (!status.base_cv_uploaded) return 2;
	return 3;
}

/**
 * Whether the root page should auto-redirect a new user to `/onboarding`.
 *
 * Returns true only when setup is incomplete AND the user has not already been
 * sent to (or dismissed) onboarding this session. The caller is responsible for
 * setting the `dismissed` flag at redirect time so the gate fires at most once
 * per session — preventing a redirect loop and respecting "do this later".
 */
export function shouldAutoRedirect(status: SetupStatus, dismissed: boolean): boolean {
	return !status.setup_complete && !dismissed;
}
