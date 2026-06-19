/**
 * Types for GET /api/today response (nx-2 Today dashboard).
 */

export interface MatchBrief {
	id: number;
	job_id: number;
	score: number;
	status: string;
	matched_at: string; // ISO 8601
	job_title: string | null;
	company: string | null;
}

export interface NewMatchesSection {
	since: string; // ISO 8601 baseline timestamp
	high_confidence: MatchBrief[]; // score >= 80
	worth_reviewing: MatchBrief[]; // 60 <= score < 80
	skipped: MatchBrief[]; // score < 60
	total: number;
}

export interface BlockedAction {
	kind: string; // "broken_session" | "pending_application" | "stale_manual"
	count: number;
	label: string;
	href: string;
}

export interface BlockedActionsSection {
	actions: BlockedAction[];
}

export interface WeekStatsSection {
	applications_submitted: number;
	daily_limit_used: number;
	daily_limit_total: number;
	response_rate: string;
}

export interface TodayResponse {
	new_matches: NewMatchesSection;
	blocked_actions: BlockedActionsSection;
	week_stats: WeekStatsSection;
}
