/**
 * W04: the review decision becomes an explicit document selection on
 * POST /api/applications/:id/apply — what the user reviewed is what is uploaded.
 */
import { describe, expect, it } from 'vitest';

import { applyRequestBody } from './documentSelection';

describe('applyRequestBody', () => {
	it('sends the reviewed tailored CV id when changes are approved', () => {
		expect(
			applyRequestBody('manual', 'approved', { cvDocumentId: 12, letterDocumentId: 30, attachLetter: true })
		).toEqual({ method: 'manual', cv_document_id: 12, letter_document_id: 30 });
	});

	it('sends use_base_cv and never the tailored id for "Use base CV"', () => {
		expect(
			applyRequestBody('manual', 'base_cv', { cvDocumentId: 12, letterDocumentId: null, attachLetter: true })
		).toEqual({ method: 'manual', use_base_cv: true });
	});

	it('falls back to the base CV when there is no tailored version to approve', () => {
		expect(
			applyRequestBody('auto', 'approved', { cvDocumentId: null, letterDocumentId: null, attachLetter: true })
		).toEqual({ method: 'auto', use_base_cv: true });
	});

	it('omits the letter when the user unticks it', () => {
		expect(
			applyRequestBody('manual', 'approved', { cvDocumentId: 5, letterDocumentId: 9, attachLetter: false })
		).toEqual({ method: 'manual', cv_document_id: 5 });
	});
});

describe('review fixes', () => {
	it('I9: refuses to approve a tailored CV whose review failed to load', () => {
		expect(
			applyRequestBody('manual', 'approved', {
				cvDocumentId: null,
				letterDocumentId: null,
				attachLetter: false,
				loadFailed: true
			})
		).toBeNull();
	});

	it('I9: an explicit "Use base CV" still works when the diff failed to load', () => {
		expect(
			applyRequestBody('manual', 'base_cv', {
				cvDocumentId: null,
				letterDocumentId: null,
				attachLetter: false,
				loadFailed: true
			})
		).toEqual({ method: 'manual', use_base_cv: true });
	});
});
