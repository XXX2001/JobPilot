/**
 * Tracker column for an application status (W06).
 *
 * Only `applied` (and the recruiting states after it) are applications that
 * reached the employer. Prepared (`awaiting_submission`), possibly-submitted
 * (`needs_verification`) and in-flight (`pending`) rows wait for the user;
 * unknown statuses are never shown as Applied.
 */
const DIRECT = new Set(['applied', 'heard_back', 'interview', 'offer', 'rejected']);

export function columnFor(status: string): string {
	const s = status.toLowerCase().replace(' ', '_');
	if (DIRECT.has(s)) return s;
	if (s === 'failed' || s === 'cancelled') return 'not_submitted';
	return 'to_submit';
}

/** Rows the user can confirm with "Mark submitted" (POST .../confirm-submitted). */
export function needsUserAction(status: string): boolean {
	return status === 'awaiting_submission' || status === 'needs_verification';
}

/** Status a card takes when dropped on a column. */
export function statusForColumn(column: string): string {
	if (column === 'to_submit') return 'awaiting_submission';
	if (column === 'not_submitted') return 'cancelled';
	return column;
}
