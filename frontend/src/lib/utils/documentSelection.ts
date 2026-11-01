/**
 * W04: turn a CV-review decision into the apply request's explicit document
 * selection. The backend uploads exactly these documents (or refuses with 409).
 */

export type ReviewDecision = 'approved' | 'base_cv';

export interface ReviewedDocuments {
	/** `document_id` from GET /api/documents/:matchId/diff, null if no tailored CV exists. */
	cvDocumentId: number | null;
	/** `letter_document_id` from the same response. */
	letterDocumentId: number | null;
	attachLetter: boolean;
}

export interface ApplyBody {
	method: string;
	cv_document_id?: number;
	use_base_cv?: true;
	letter_document_id?: number;
}

export function applyRequestBody(method: string, decision: ReviewDecision, docs: ReviewedDocuments): ApplyBody {
	const body: ApplyBody = { method };
	// No tailored version means the review showed "base CV will be used as-is".
	if (decision === 'approved' && docs.cvDocumentId !== null) body.cv_document_id = docs.cvDocumentId;
	else body.use_base_cv = true;
	if (docs.attachLetter && docs.letterDocumentId !== null) body.letter_document_id = docs.letterDocumentId;
	return body;
}
