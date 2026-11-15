/**
 * Today page: fold a live `new_match` WebSocket frame into the "What's new"
 * section. Pure, so the page handler stays a one-liner and this is testable.
 */

import type { MatchBrief, NewMatchesSection } from '$lib/types/today';
import type { NewMatchMsg } from '$lib/types/ws';

type Bucket = 'high_confidence' | 'worth_reviewing' | 'skipped';

/** Same thresholds as `GET /api/today` (backend/api/today.py). */
function bucketFor(score: number): Bucket {
	if (score >= 80) return 'high_confidence';
	if (score >= 60) return 'worth_reviewing';
	return 'skipped';
}

/**
 * Return the section with the match prepended to its score bucket. A match
 * already listed (the page loaded it, or the frame was replayed) changes nothing.
 */
export function prependNewMatch(
	section: NewMatchesSection,
	msg: NewMatchMsg,
	now: Date = new Date()
): NewMatchesSection {
	const listed = [...section.high_confidence, ...section.worth_reviewing, ...section.skipped];
	if (listed.some((m) => m.id === msg.match_id)) return section;

	const brief: MatchBrief = {
		id: msg.match_id,
		job_id: msg.job_id,
		score: msg.score,
		status: 'new',
		matched_at: now.toISOString(),
		job_title: msg.title,
		company: msg.company
	};
	const bucket = bucketFor(msg.score);
	return { ...section, [bucket]: [brief, ...section[bucket]], total: section.total + 1 };
}
