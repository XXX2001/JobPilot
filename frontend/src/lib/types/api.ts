/**
 * Shared REST response types for the JobPilot SvelteKit frontend.
 *
 * These were previously redefined per-component (3× `Job`, 3× `QueueMatch`,
 * 2× `DiffEntry`, 2× `Application`, 3× `SetupStatus`, 1× `Document`),
 * which caused subtle drift between, e.g., `KanbanBoard.Application` and
 * `tracker/+page.svelte`'s local copy. Centralising here gives a single
 * source of truth that mirrors the backend Pydantic models.
 *
 * NOTE: there is still no codegen from `backend/api/*_models.py` —
 * keep this file in lockstep with the FastAPI route shapes.
 */

// ─── Job & QueueMatch ───────────────────────────────────────────────────────

/**
 * A scraped job listing. Mirrors `backend.models.Job` minus internal
 * scraping bookkeeping. Optional fields are nullable in DB or only
 * populated after enrichment.
 */
export interface Job {
	id: number;
	title: string;
	company: string;
	location?: string;
	url: string;
	apply_url?: string;
	apply_method?: string;
	salary_min?: number;
	salary_max?: number;
	description?: string;
	posted_at?: string;
}

/**
 * One row of the discovery queue — a job paired with the score & status
 * the matcher assigned. Mirrors `GET /api/queue` items and `GET /api/queue/:id`.
 */
export interface QueueMatch {
	id: number;
	job_id: number;
	score: number;
	status: string;
	batch_date?: string;
	matched_at: string;
	job: Job;
}

// ─── CV diff ────────────────────────────────────────────────────────────────

/**
 * One section's worth of LaTeX CV tailoring changes.
 * Mirrors `GET /api/documents/:matchId/diff` payload entries.
 */
export interface DiffEntry {
	section: string;
	original_text: string;
	edited_text: string;
	change_description: string;
}

// ─── Applications ───────────────────────────────────────────────────────────

/**
 * A single application event (status transition, note, follow-up, etc.).
 * Mirrors `backend.models.ApplicationEvent`.
 */
export interface ApplicationEvent {
	id: number;
	application_id: number;
	event_type: string;
	details?: string;
	event_date: string;
}

/**
 * An application — i.e. a job the user (or auto-apply) actually submitted.
 * Mirrors `GET /api/applications` items.
 *
 * `events` is optional because the backend sometimes returns `null`/omits it
 * for list endpoints; the Kanban view falls back to `[]`.
 */
export interface Application {
	id: number;
	job_match_id?: number | null;
	method: string;
	status: string;
	applied_at?: string | null;
	notes?: string;
	error_log?: string;
	created_at: string;
	events?: ApplicationEvent[];
	// Denormalised fields for display (injected by parent components).
	job_title?: string | null;
	company?: string | null;
}

// ─── Settings ───────────────────────────────────────────────────────────────

/**
 * Onboarding readiness flags from `GET /api/settings/status`.
 * Used by SetupWizard, the analytics page (to gate the wizard), and the
 * settings page (System tab status row).
 */
export interface SetupStatus {
	gemini_key_set: boolean;
	adzuna_key_set: boolean;
	tectonic_found: boolean;
	base_cv_uploaded: boolean;
	setup_complete: boolean;
}

// ─── Documents ──────────────────────────────────────────────────────────────

/**
 * A generated artifact (tailored CV PDF, cover letter, etc.).
 * Mirrors `GET /api/documents` items.
 */
export interface Document {
	id: number;
	job_match_id?: number;
	doc_type: string;
	tex_path?: string;
	pdf_path?: string;
	diff_json?: unknown;
	created_at: string;
}

/**
 * Response from `POST /api/documents/:matchId/letter/regenerate`.
 * Mirrors the backend `LetterRegenerateResponse` shape.
 */
export interface LetterRegenerateResponse {
	match_id: number;
	doc_id: number;
	doc_type: 'letter';
	tex_path: string;
	pdf_path: string;
	status: 'regenerated';
}
