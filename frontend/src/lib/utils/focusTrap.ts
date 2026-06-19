/**
 * Svelte action: trap focus inside `node` while the modal is open.
 *
 * Behaviour
 * - Records the previously focused element on mount.
 * - Focuses the first focusable descendant (or `node` itself).
 * - Loops Tab / Shift+Tab between the first and last focusable descendants.
 * - On destroy, restores focus to the previously focused element.
 *
 * Use on the dialog root, alongside `role="dialog"` and `aria-modal="true"`:
 *
 *     <div role="dialog" aria-modal="true" tabindex="-1" use:focusTrap> … </div>
 */

const FOCUSABLE_SELECTOR = [
	'a[href]',
	'area[href]',
	'button:not([disabled])',
	'input:not([disabled]):not([type="hidden"])',
	'select:not([disabled])',
	'textarea:not([disabled])',
	'iframe',
	'object',
	'embed',
	'[tabindex]:not([tabindex="-1"])',
	'[contenteditable="true"]'
].join(',');

function getFocusable(root: HTMLElement): HTMLElement[] {
	return Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(
		(el) => !el.hasAttribute('disabled') && el.offsetParent !== null
	);
}

export function focusTrap(node: HTMLElement) {
	const previouslyFocused = document.activeElement as HTMLElement | null;

	// Defer one microtask so children mount before we look for them.
	queueMicrotask(() => {
		const focusables = getFocusable(node);
		(focusables[0] ?? node).focus();
	});

	function onKeydown(e: KeyboardEvent) {
		if (e.key !== 'Tab') return;
		const focusables = getFocusable(node);
		if (focusables.length === 0) {
			e.preventDefault();
			node.focus();
			return;
		}
		const first = focusables[0];
		const last = focusables[focusables.length - 1];
		const active = document.activeElement as HTMLElement | null;
		if (e.shiftKey && (active === first || !node.contains(active))) {
			e.preventDefault();
			last.focus();
		} else if (!e.shiftKey && active === last) {
			e.preventDefault();
			first.focus();
		}
	}

	node.addEventListener('keydown', onKeydown);

	return {
		destroy() {
			node.removeEventListener('keydown', onKeydown);
			// Restore focus to whatever was focused before the trap engaged,
			// but only if it's still in the DOM and focusable.
			if (previouslyFocused && document.contains(previouslyFocused)) {
				try {
					previouslyFocused.focus();
				} catch {
					/* element no longer focusable — silently ignore */
				}
			}
		}
	};
}
