/**
 * W07: approve or cancel the review currently awaiting a decision via
 * POST /api/applications/:jobId/review-decision. The modal stays open until
 * this resolves; a network failure is retried with the same idempotency key,
 * so a lost response cannot apply the decision twice. HTTP errors (409 stale
 * or already decided, 401…) are not retried — the caller shows them.
 */
import { ApiError, apiFetch } from '$lib/api';
import { reviewStateToModal, type ReviewModal } from '$lib/utils/pendingReview';

export interface ReviewIdentity {
	jobId: number;
	reviewId: string;
	revision: number;
}

export interface ReviewAck {
	job_id: number;
	review_id: string;
	revision: number;
	decision: 'approve' | 'cancel';
	accepted_at: string;
}

export async function submitReviewDecision(
	review: ReviewIdentity,
	decision: 'approve' | 'cancel',
	fields: Record<string, string>,
	{ attempts = 3, delayMs = 500 }: { attempts?: number; delayMs?: number } = {}
): Promise<ReviewAck> {
	const body = JSON.stringify({
		review_id: review.reviewId,
		revision: review.revision,
		decision,
		fields,
		idempotency_key: crypto.randomUUID()
	});
	for (let i = 1; ; i++) {
		try {
			return await apiFetch<ReviewAck>(`/api/applications/${review.jobId}/review-decision`, {
				method: 'POST',
				body
			});
		} catch (e) {
			if (e instanceof ApiError || i >= attempts) throw e;
			await new Promise((r) => setTimeout(r, delayMs * i));
		}
	}
}

/**
 * After a 409: the review that is waiting now, or null when none is — the
 * modal must then close instead of offering buttons that can only fail (I8).
 */
export async function reviewAfterConflict(jobId: number): Promise<ReviewModal | null> {
	try {
		return reviewStateToModal(await apiFetch(`/api/applications/${jobId}/review-state`));
	} catch {
		return null;
	}
}
