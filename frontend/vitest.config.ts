import { sveltekit } from '@sveltejs/kit/vite';
import { defineConfig } from 'vitest/config';

/**
 * Vitest configuration for the JobPilot frontend (T8 — first frontend tests).
 *
 * Notes:
 * - Uses `happy-dom`-free defaults. The first spec (`api.test.ts`) only needs
 *   a stub `fetch` plus the global `FormData`, both of which are available in
 *   Node 18+ without a DOM.
 * - Component tests (planned post-T8) should switch to `environment: 'jsdom'`
 *   and add `@testing-library/svelte` once a real Svelte component is under
 *   test.
 * - `globals: true` lets specs use `describe` / `it` / `expect` without
 *   importing them — matches the Pytest ergonomics of the backend suite.
 */
export default defineConfig({
	plugins: [sveltekit()],
	test: {
		include: ['src/**/*.{test,spec}.{js,ts}'],
		globals: true,
		environment: 'node'
	}
});
