<script lang="ts">
	import { loginPrompt, send } from '$lib/stores/websocket';
	import { LogIn, X, Check } from 'lucide-svelte';

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
</script>

{#if $loginPrompt}
	<div class="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm">
		<div class="w-[450px] max-w-[90vw] rounded-xl border border-border bg-background p-6 shadow-2xl flex flex-col gap-6">
			<div class="flex items-center gap-4">
				<div class="flex h-12 w-12 shrink-0 items-center justify-center rounded-full bg-accent text-foreground">
					<LogIn size={24} />
				</div>
				<div>
					<h2 class="text-xl font-semibold tracking-tight text-foreground">
						Login Required
					</h2>
					<p class="text-sm text-muted-foreground">
						{capitalize($loginPrompt.site)} wants to make sure it's you.
					</p>
				</div>
			</div>

			<div class="rounded-lg bg-accent/30 p-4 border border-border/50">
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
