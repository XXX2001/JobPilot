/**
 * WebSocket protocol types — mirrors `backend/api/ws_models.py`.
 *
 * Source of truth: the Pydantic `WSMessage` discriminated union in
 * `backend/api/ws_models.py`. If the backend file changes, update this file
 * in lockstep — there is no codegen yet (FE-02 is still open).
 *
 * Each message has a string literal `type` discriminator so a switch on
 * `msg.type` narrows the union for the rest of the block:
 *
 *   import type { WSMessage } from "$lib/types/ws";
 *   if (msg.type === "apply_review") {
 *     // msg is narrowed to ApplyReviewMsg
 *     msg.filled_fields; // ok
 *   }
 */

// ─── Server → Client ─────────────────────────────────────────────────────────

/** Generic batch-progress narration (Phase 1/2/3). progress ∈ [0,1]; <0 = error. */
export interface StatusMsg {
	type: 'status';
	message: string;
	progress: number;
}

/** Per-job fit assessment broadcast after the matching/grading step. */
export interface JobAssessmentMsg {
	type: 'job_progress';
	match_id: number;
	ats_score: number;
	gap_severity: number;
	decision: string;
	covered: string[];
	gaps: Array<{ skill: string; criticality: number }>;
}

export interface ScrapingStatusMsg {
	type: 'scraping_status';
	message: string;
	source: string;
	progress: number;
}

export interface MatchingStatusMsg {
	type: 'matching_status';
	count: number;
}

export interface TailoringStatusMsg {
	type: 'tailoring_status';
	job_id: number;
	progress: number;
}

export interface ApplyReviewMsg {
	type: 'apply_review';
	job_id: number;
	filled_fields: Record<string, string>;
	screenshot_base64: string | null;
}

export interface ApplyResultMsg {
	type: 'apply_result';
	job_id: number;
	status: string;
	method: string;
}

export interface LoginRequiredMsg {
	type: 'login_required';
	site: string;
	browser_window_title: string;
}

export interface LoginConfirmedMsg {
	type: 'login_confirmed';
	site: string;
}

/** Scraper or applier hit a CAPTCHA / block page. */
export interface CaptchaDetectedMsg {
	type: 'captcha_detected';
	site: string;
	job_id: number | null;
	message: string;
}

/** User cleared the CAPTCHA (or it timed out). */
export interface CaptchaResolvedMsg {
	type: 'captcha_resolved';
	job_id: number | null;
}

/** Reply to a client `ping`. */
export interface PongMsg {
	type: 'pong';
}

export interface ErrorMsg {
	type: 'error';
	message: string;
	code: string;
}

/** Gmail polling progress narration emitted by gm-6/gm-8. */
export interface GmailSyncStatusMsg {
	type: 'gmail_sync_status';
	last_history_id: string | null;
	messages_synced: number;
	progress: number;
}

/** Per-message broadcast when a new inbound Gmail message is ingested. */
export interface GmailMessageReceivedMsg {
	type: 'gmail_message_received';
	gmail_message_id: string;
	from_address: string;
	subject: string | null;
	category: string | null;
	category_confidence: number | null;
	linked_application_id: number | null;
	link_confidence: number | null;
}

export type WSMessage =
	| StatusMsg
	| JobAssessmentMsg
	| ScrapingStatusMsg
	| MatchingStatusMsg
	| TailoringStatusMsg
	| ApplyReviewMsg
	| ApplyResultMsg
	| LoginRequiredMsg
	| LoginConfirmedMsg
	| CaptchaDetectedMsg
	| CaptchaResolvedMsg
	| PongMsg
	| ErrorMsg
	| GmailSyncStatusMsg
	| GmailMessageReceivedMsg;

export type WSMessageType = WSMessage['type'];

// ─── Client → Server ─────────────────────────────────────────────────────────

export interface ConfirmSubmitMsg {
	type: 'confirm_submit';
	job_id: number;
}

export interface CancelApplyMsg {
	type: 'cancel_apply';
	job_id: number;
}

/** User edits to mis-filled review fields (selector→new value) before submit. */
export interface PatchFieldsMsg {
	type: 'patch_fields';
	job_id: number;
	fields: Record<string, string>;
}

export interface LoginDoneMsg {
	type: 'login_done';
	site: string;
}

export interface LoginCancelMsg {
	type: 'login_cancel';
	site: string;
}

export type ClientMessage =
	| ConfirmSubmitMsg
	| CancelApplyMsg
	| PatchFieldsMsg
	| LoginDoneMsg
	| LoginCancelMsg;

// ─── Narrow helper ───────────────────────────────────────────────────────────

/**
 * Narrow an unknown JSON-parsed value to a `WSMessage` by checking that it has
 * a known `type` discriminator. Returns `null` if the value is not a recognised
 * message — caller can log it as a dead/unknown handler.
 */
export function asWSMessage(value: unknown): WSMessage | null {
	if (!value || typeof value !== 'object') return null;
	const t = (value as { type?: unknown }).type;
	if (typeof t !== 'string') return null;
	switch (t) {
		case 'status':
		case 'job_progress':
		case 'scraping_status':
		case 'matching_status':
		case 'tailoring_status':
		case 'apply_review':
		case 'apply_result':
		case 'login_required':
		case 'login_confirmed':
		case 'captcha_detected':
		case 'captcha_resolved':
		case 'pong':
		case 'error':
		case 'gmail_sync_status':
		case 'gmail_message_received':
			return value as WSMessage;
		default:
			return null;
	}
}
