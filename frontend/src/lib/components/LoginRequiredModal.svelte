<script lang="ts">
	import { loginPrompt, send } from '$lib/stores/websocket';
	import { LogIn, X, Check } from 'lucide-svelte';
	import { focusTrap } from '$lib/utils/focusTrap';

	function handleDone() {
		send({ type: 'login_done', site: $loginPrompt!.site });
		loginPrompt.set(null);
	}

	function handleCancel() {
		send({ type: 'login_cancel', site: $loginPrompt!.site });
		loginPrompt.set(null);
	}

	function capitalize(str: string) {
		if (!str) return '';
		return str.charAt(0).toUpperCase() + str.slice(1);
	}

	function onBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) handleCancel();
	}

	function onKeydown(e: KeyboardEvent) {
		if ($loginPrompt && e.key === 'Escape') {
			e.preventDefault();
			handleCancel();
		}
	}
</script>

<svelte:window onkeydown={onKeydown} />

{#if $loginPrompt}
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div
		class="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm"
		onclick={onBackdropClick}
	>
		<div
			class="w-[450px] max-w-[90vw] rounded-xl border border-border bg-background p-6 shadow-2xl flex flex-col gap-6"
			role="dialog"
			tabindex="-1"
			aria-modal="true"
			aria-labelledby="login-required-title"
			aria-describedby="login-required-desc"
			use:focusTrap
		>
			<div class="flex items-center gap-4">
				<div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-accent text-foreground">
					<LogIn size={24} />
				</div>
				<div>
					<h2 id="login-required-title" class="text-xl font-semibold tracking-tight text-foreground">
						Login Required
					</h2>
					<p class="text-sm text-muted-foreground">
						{capitalize($loginPrompt.site)} wants to make sure it's you.
					</p>
				</div>
			</div>

			<div id="login-required-desc" class="rounded-lg bg-accent/30 p-4 border border-border/50">
				<p class="text-sm text-foreground leading-relaxed">
					{$loginPrompt.text}
				</p>
			</div>

			<div class="flex justify-end gap-3 pt-2">
				<button
					onclick={handleCancel}
					class="flex items-center gap-2 rounded-md border border-border bg-transparent px-4 py-2 text-sm font-medium text-foreground hover:bg-accent/50 transition-colors"
				>
					<X size={16} />
					Cancel
				</button>
				<button
					onclick={handleDone}
					class="flex items-center gap-2 rounded-md bg-foreground px-4 py-2 text-sm font-medium text-background hover:bg-foreground/90 transition-colors"
				>
					<Check size={16} />
					Done
				</button>
			</div>
		</div>
	</div>
{/if}
