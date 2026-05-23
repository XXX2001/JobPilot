/**
 * Global hotkey dispatcher.
 *
 * Usage:
 *   const handle = dispatcher.register('/', { j: () => ..., k: () => ... });
 *   dispatcher.deregister(handle);
 *
 * Rules:
 * - Single-key bindings only. No leader-key / mode system.
 * - When any <input>, <textarea>, or [contenteditable] is focused, all keys are
 *   ignored EXCEPT Escape, which blurs the focused element.
 * - Per-route: bindings fire only when `currentRoute` matches the routeId passed
 *   to register().  Pass '*' to fire on every route.
 * - '?' always opens the HotkeyHelp modal regardless of route.
 */

import { writable, get } from 'svelte/store';

// ── Types ──────────────────────────────────────────────────────────────────

export interface Binding {
  /** Human-readable description shown in the ? help modal. */
  label: string;
  /** Function to call when the key fires. */
  action: () => void;
}

export type BindingMap = Record<string, Binding>;

export interface RegisterOptions {
  /** Optional group name for the help modal. Defaults to routeId. */
  group?: string;
}

export interface BindingHandle {
  readonly id: number;
}

interface Registration {
  id: number;
  routeId: string;
  bindings: BindingMap;
  group: string;
}

// ── Internal state ──────────────────────────────────────────────────────────

let nextId = 1;
const registrations: Registration[] = [];

/** The current SvelteKit route id — updated by +layout.svelte. */
let _currentRoute = '/';

/** Svelte store: true when the ? help modal should be visible. */
export const helpOpen = writable(false);

/** Svelte store: snapshot of active bindings for the current route (for the modal). */
export const activeBindings = writable<{ group: string; key: string; label: string }[]>([]);

// ── Public API ──────────────────────────────────────────────────────────────

/**
 * Register a set of hotkey bindings for a route.
 *
 * @param routeId  SvelteKit route.id (e.g. '/') or '*' for all routes.
 * @param bindings Map of key → { label, action }.
 * @param options  Optional group name for the help modal.
 * @returns A handle to pass to deregister().
 */
export function register(
  routeId: string,
  bindings: BindingMap,
  options: RegisterOptions = {}
): BindingHandle {
  const id = nextId++;
  const group = options.group ?? routeId;
  registrations.push({ id, routeId, bindings, group });
  _rebuildActiveBindings();
  return { id };
}

/**
 * Remove a previously registered set of bindings.
 */
export function deregister(handle: BindingHandle): void {
  const idx = registrations.findIndex((r) => r.id === handle.id);
  if (idx !== -1) {
    registrations.splice(idx, 1);
    _rebuildActiveBindings();
  }
}

/**
 * Called by +layout.svelte whenever the route changes.
 */
export function setCurrentRoute(routeId: string): void {
  _currentRoute = routeId;
  _rebuildActiveBindings();
}

/**
 * The global keydown handler. Wire this to <svelte:window onkeydown={handle}>.
 */
export function handle(event: KeyboardEvent): void {
  const key = event.key;

  // Always let modifier combos through (Ctrl+C, Alt+F4, etc.)
  if (event.ctrlKey || event.altKey || event.metaKey) return;

  const focused = document.activeElement;
  const inInput =
    focused instanceof HTMLInputElement ||
    focused instanceof HTMLTextAreaElement ||
    (focused instanceof HTMLElement && focused.isContentEditable);

  // In an input field, only Escape is intercepted (to blur the field).
  if (inInput) {
    if (key === 'Escape') {
      (focused as HTMLElement).blur();
      event.preventDefault();
    }
    return;
  }

  // '?' toggles the help modal (universal, any route).
  if (key === '?') {
    helpOpen.update((v) => !v);
    event.preventDefault();
    return;
  }

  // Escape closes the help modal if open.
  if (key === 'Escape') {
    if (get(helpOpen)) {
      helpOpen.set(false);
      event.preventDefault();
      return;
    }
  }

  // Route-aware dispatch.
  for (const reg of registrations) {
    if (reg.routeId !== '*' && reg.routeId !== _currentRoute) continue;
    const binding = reg.bindings[key];
    if (binding) {
      binding.action();
      event.preventDefault();
      return; // First match wins.
    }
  }
}

// ── Internal helpers ────────────────────────────────────────────────────────

function _rebuildActiveBindings(): void {
  const rows: { group: string; key: string; label: string }[] = [];
  for (const reg of registrations) {
    if (reg.routeId !== '*' && reg.routeId !== _currentRoute) continue;
    for (const [key, binding] of Object.entries(reg.bindings)) {
      rows.push({ group: reg.group, key, label: binding.label });
    }
  }
  // Always include '?' as a universal binding.
  rows.push({ group: 'Global', key: '?', label: 'Show keyboard shortcuts' });
  activeBindings.set(rows);
}
