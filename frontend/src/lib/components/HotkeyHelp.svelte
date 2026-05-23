<script lang="ts">
	import { helpOpen, activeBindings } from '$lib/utils/hotkeys';

	// Group bindings by their group label.
	const grouped = $derived(
		Object.entries(
			($activeBindings ?? []).reduce<Record<string, { key: string; label: string }[]>>(
				(acc, row) => {
					if (!acc[row.group]) acc[row.group] = [];
					acc[row.group].push({ key: row.key, label: row.label });
					return acc;
				},
				{}
			)
		)
	);

	function close() {
		helpOpen.set(false);
	}

	function onBackdropKeydown(event: KeyboardEvent) {
		if (event.key === 'Escape') close();
	}

	/** Svelte action: focus the node on mount and trap focus within it. */
	function trapFocus(node: HTMLElement) {
		// Focus the dialog so keyboard events are captured immediately.
		node.focus();

		function onFocusOut(event: FocusEvent) {
			const relatedTarget = event.relatedTarget as Node | null;
			// If focus moved outside the dialog, pull it back.
			if (relatedTarget && !node.contains(relatedTarget)) {
				node.focus();
			}
		}

		node.addEventListener('focusout', onFocusOut);
		return {
			destroy() {
				node.removeEventListener('focusout', onFocusOut);
			}
		};
	}
</script>

{#if $helpOpen}
	<!-- Backdrop -->
	<!-- svelte-ignore a11y_no_noninteractive_element_interactions -->
	<div
		role="dialog"
		aria-modal="true"
		aria-label="Keyboard shortcuts"
		tabindex="-1"
		class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm"
		onkeydown={onBackdropKeydown}
		use:trapFocus
	>
		<!-- svelte-ignore a11y_click_events_have_key_events -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="absolute inset-0" onclick={close}></div>

		<div
			class="bg-card border-border relative z-10 mx-4 w-full max-w-sm overflow-hidden rounded-xl border shadow-2xl"
		>
			<!-- Header -->
			<div class="border-border flex items-center justify-between border-b px-5 py-4">
				<h2 class="text-sm font-semibold">Keyboard Shortcuts</h2>
				<button
					onclick={close}
					class="text-muted-foreground hover:text-foreground text-lg leading-none transition-colors"
					aria-label="Close"
				>
					✕
				</button>
			</div>

			<!-- Body -->
			<div class="max-h-[60vh] overflow-y-auto px-5 py-4 space-y-5">
				{#each grouped as [group, bindings]}
					<div>
						<p class="text-muted-foreground mb-2 text-xs font-semibold uppercase tracking-wide">
							{group === '/' ? 'Job Queue' : group}
						</p>
						<div class="space-y-1.5">
							{#each bindings as { key, label }}
								<div class="flex items-center justify-between gap-4">
									<span class="text-xs text-muted-foreground">{label}</span>
									<kbd
										class="border-border bg-muted rounded border px-1.5 py-0.5 font-mono text-xs leading-none"
									>
										{key === 'ArrowLeft' ? '←' : key === 'ArrowRight' ? '→' : key === 'Enter' ? '↵' : key}
									</kbd>
								</div>
							{/each}
						</div>
					</div>
				{/each}
			</div>

			<!-- Footer hint -->
			<div class="border-border border-t px-5 py-3">
				<p class="text-muted-foreground text-center text-xs">
					Press <kbd class="border-border bg-muted rounded border px-1 py-0.5 font-mono text-xs">?</kbd> to toggle
				</p>
			</div>
		</div>
	</div>
{/if}
