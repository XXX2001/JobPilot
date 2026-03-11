export interface DiffSpan {
	type: 'same' | 'added' | 'removed';
	text: string;
}

/**
 * Compute a word-level diff between two strings using LCS.
 * Returns an array of DiffSpan with type 'same', 'added', or 'removed'.
 * Whitespace between words is preserved.
 */
export function wordDiff(original: string, edited: string): DiffSpan[] {
	const origWords = tokenize(original);
	const editWords = tokenize(edited);

	// Build LCS table
	const m = origWords.length;
	const n = editWords.length;
	const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));

	for (let i = 1; i <= m; i++) {
		for (let j = 1; j <= n; j++) {
			if (origWords[i - 1] === editWords[j - 1]) {
				dp[i][j] = dp[i - 1][j - 1] + 1;
			} else {
				dp[i][j] = Math.max(dp[i - 1][j], dp[i][j - 1]);
			}
		}
	}

	// Backtrack to build raw spans
	const raw: DiffSpan[] = [];
	let i = m;
	let j = n;

	while (i > 0 || j > 0) {
		if (i > 0 && j > 0 && origWords[i - 1] === editWords[j - 1]) {
			raw.push({ type: 'same', text: origWords[i - 1] });
			i--;
			j--;
		} else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
			raw.push({ type: 'added', text: editWords[j - 1] });
			j--;
		} else {
			raw.push({ type: 'removed', text: origWords[i - 1] });
			i--;
		}
	}

	raw.reverse();

	// Merge consecutive spans of the same type, inserting spaces between tokens
	const merged: DiffSpan[] = [];
	for (const span of raw) {
		const prev = merged[merged.length - 1];
		if (prev && prev.type === span.type) {
			prev.text += ' ' + span.text;
		} else {
			merged.push({ type: span.type, text: span.text });
		}
	}

	return merged;
}

/** Split a string into non-whitespace word tokens. */
function tokenize(text: string): string[] {
	return text.trim().split(/\s+/).filter((w) => w.length > 0);
}
