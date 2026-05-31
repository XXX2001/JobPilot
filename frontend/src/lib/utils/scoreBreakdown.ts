/**
 * Pure, testable computation behind the "Why this score" panel on the
 * job-detail page (`routes/jobs/[id]/+page.svelte`).
 *
 * It composes two backend payloads the client already has access to:
 *   - `keyword_hits` from `GET /api/jobs/{job_id}/score`
 *   - `keywords.include` + `salary_min` from `GET /api/settings/search`
 *
 * On the `keyword_hits` shape: the matcher does not currently write the
 * column (`JobMatch.keyword_hits`, `Mapped[Optional[dict]]`), so in practice
 * it is `null`. Its annotation is a dict, while `docs/api-reference.md`
 * documents a flat list of hit strings. To stay correct whichever way it is
 * eventually populated, we normalise BOTH shapes into a set of matched
 * keyword strings.
 */

/** Accepted runtime shapes of the `keyword_hits` payload. */
export type KeywordHits = string[] | Record<string, unknown> | null | undefined;

/** A job's advertised salary range (either bound may be unknown). */
export interface JobSalary {
	min: number | null | undefined;
	max: number | null | undefined;
}

export interface SalaryBreakdown {
	jobMin: number | null;
	jobMax: number | null;
	target: number | null;
	/** `true`/`false` when comparable, `null` when target or job salary unknown. */
	meetsTarget: boolean | null;
}

export interface ScoreBreakdown {
	matched: string[];
	missing: string[];
	salary: SalaryBreakdown;
}

/**
 * Reduce `keyword_hits` to the ordered, de-duplicated list of keywords that
 * actually matched. Dict entries count as a hit only when their value is
 * truthy (a `true` flag or a count greater than zero).
 */
function extractHits(keywordHits: KeywordHits): string[] {
	if (Array.isArray(keywordHits)) {
		return dedupe(keywordHits.filter((k): k is string => typeof k === 'string'));
	}
	if (keywordHits && typeof keywordHits === 'object') {
		return dedupe(
			Object.entries(keywordHits)
				.filter(([, value]) => isHit(value))
				.map(([key]) => key)
		);
	}
	return [];
}

function isHit(value: unknown): boolean {
	if (typeof value === 'number') return value > 0;
	return Boolean(value);
}

function dedupe(values: string[]): string[] {
	const seen = new Set<string>();
	const out: string[] = [];
	for (const value of values) {
		const key = value.toLowerCase();
		if (!seen.has(key)) {
			seen.add(key);
			out.push(value);
		}
	}
	return out;
}

function computeMeetsTarget(
	jobMin: number | null,
	jobMax: number | null,
	target: number | null
): boolean | null {
	if (target == null) return null;
	if (jobMin == null && jobMax == null) return null;
	if (jobMax != null && jobMax >= target) return true;
	if (jobMin != null && jobMin >= target) return true;
	return false;
}

/**
 * Build the score breakdown: which configured keywords matched, which are
 * still missing, and how the job's salary compares to the user's target.
 */
export function computeScoreBreakdown(
	keywordHits: KeywordHits,
	searchKeywords: string[] | null | undefined,
	jobSalary: JobSalary,
	salaryMin: number | null | undefined
): ScoreBreakdown {
	const matched = extractHits(keywordHits);
	const matchedLower = new Set(matched.map((k) => k.toLowerCase()));

	const missing = dedupe(searchKeywords ?? []).filter(
		(kw) => !matchedLower.has(kw.toLowerCase())
	);

	const jobMin = jobSalary.min ?? null;
	const jobMax = jobSalary.max ?? null;
	const target = salaryMin ?? null;

	return {
		matched,
		missing,
		salary: {
			jobMin,
			jobMax,
			target,
			meetsTarget: computeMeetsTarget(jobMin, jobMax, target)
		}
	};
}
