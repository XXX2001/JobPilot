/**
 * Review I10: what an apply response actually means for the user. HTTP 200
 * carries the domain outcome; only `confirmed` is an application the site
 * accepted, and `awaiting_user_submission` still needs the user to submit.
 */
export interface ApplyResultLike {
	status: string;
	method: string;
	message?: string;
	next_action_url?: string;
}

export function describeApplyResult(res: ApplyResultLike): { ok: boolean; text: string; url?: string } {
	const why = res.message ? ` ${res.message}` : '';
	switch (res.status) {
		case 'confirmed':
			return { ok: true, text: `Submitted and confirmed by the site.${why}` };
		case 'awaiting_user_submission':
			return {
				ok: true,
				text: `Prepared — now submit it on the job site, then mark it submitted in the tracker.${why}`,
				url: res.next_action_url || undefined
			};
		case 'needs_verification':
			return { ok: false, text: `Submission could not be verified — check the job site before retrying.${why}` };
		case 'cancelled':
			return { ok: false, text: `Cancelled.${why}` };
		default:
			return { ok: false, text: `Not submitted.${why}` };
	}
}
