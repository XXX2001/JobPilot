<script lang="ts">
	import { Link2, Search, X } from 'lucide-svelte';
	import { apiFetch } from '$lib/api';
	import type { Application } from '$lib/types/api';

	// Subset of Application — we only render id / titles / status here.
	type ApplicationOption = Pick<
		Application,
		'id' | 'method' | 'status' | 'applied_at' | 'job_title' | 'company'
	>;

	let {
		open = $bindable(false),
		onLink
	}: {
		open: boolean;
		onLink: (applicationId: number) => Promise<void> | void;
	} = $props();

	let apps = $state<ApplicationOption[]>([]);
	let filter = $state('');
	let loading = $state(false);
	let error = $state<string | null>(null);
	let linking = $state<number | null>(null);

	async function load() {
		loading = true;
		error = null;
		try {
			const body = await apiFetch<{ applications: ApplicationOption[]; total: number }>(
				'/api/applications'
			);
			apps = body.applications ?? [];
		} catch (e) {
			error = (e as Error).message;
		} finally {
			loading = false;
		}
	}

	$effect(() => {
		if (open) {
			filter = '';
			load();
		}
	});

	const filtered = $derived(
		apps.filter((a) => {
			if (!filter) return true;
			const blob = `${a.id} ${a.job_title ?? ''} ${a.company ?? ''} ${a.status}`.toLowerCase();
			return blob.includes(filter.toLowerCase());
		})
	);

	function close() {
		open = false;
	}

	function onBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) close();
	}

	function onKeydown(e: KeyboardEvent) {
		if (open && e.key === 'Escape') close();
	}

	async function pick(appId: number) {
		linking = appId;
		try {
			await onLink(appId);
			open = false;
		} catch (e) {
			error = (e as Error).message;
		} finally {
			linking = null;
		}
	}
</script>

<svelte:window onkeydown={onKeydown} />

{#if open}
	<div
		class="fixed inset-0 z-[100] flex items-center justify-center bg-black/80 backdrop-blur-sm p-4"
		onclick={onBackdropClick}
		role="presentation"
	>
		<div
			class="w-[520px] max-w-[95vw] max-h-[75vh] rounded-2xl border border-border/50 bg-card shadow-2xl flex flex-col"
			role="dialog"
			tabindex="-1"
			aria-modal="true"
			aria-labelledby="link-app-title"
		>
			<header class="flex items-center justify-between gap-3 px-5 py-4 border-b border-border/50">
				<div class="flex items-center gap-3">
					<div class="w-9 h-9 rounded-lg bg-primary/10 text-primary flex items-center justify-center flex-shrink-0">
						<Link2 size={18} />
					</div>
					<div>
						<h2 id="link-app-title" class="text-base font-semibold font-heading text-foreground">
							Link to application
						</h2>
						<p class="text-xs text-muted-foreground mt-0.5">
							Select an existing application to attach this message to.
						</p>
					</div>
				</div>
				<button
					type="button"
					onclick={close}
					aria-label="Close"
					class="p-1.5 rounded-md text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors"
				>
					<X size={16} />
				</button>
			</header>

			<div class="px-5 pt-4 pb-3">
				<div class="relative">
					<Search
						size={14}
						class="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground pointer-events-none"
					/>
					<input
						type="text"
						bind:value={filter}
						placeholder="Filter by title, company, status, or #id…"
						class="w-full pl-9 pr-3 py-2 bg-background border border-border/50 rounded-lg text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/40 transition-colors"
					/>
				</div>
			</div>

			{#if error}
				<div class="mx-5 mb-3 px-3 py-2 rounded-lg bg-red-500/10 text-red-400 border border-red-500/20 text-xs">
					{error}
				</div>
			{/if}

			<div class="flex-1 overflow-y-auto px-3 pb-3">
				{#if loading}
					<div class="px-2 py-6 text-center text-sm text-muted-foreground">Loading applications…</div>
				{:else if filtered.length === 0}
					<div class="px-2 py-6 text-center text-sm text-muted-foreground">
						{apps.length === 0 ? 'No applications yet.' : 'No applications match that filter.'}
					</div>
				{:else}
					<ul class="space-y-1">
						{#each filtered as app (app.id)}
							<li>
								<button
									type="button"
									disabled={linking !== null}
									onclick={() => pick(app.id)}
									class="w-full flex items-center justify-between gap-3 text-left px-3 py-2 rounded-lg hover:bg-accent/50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors group"
								>
									<div class="min-w-0 flex-1">
										<div class="flex items-center gap-2 text-sm">
											<span class="text-muted-foreground font-mono text-xs">#{app.id}</span>
											<span class="text-foreground font-medium truncate">
												{app.job_title ?? '—'}
											</span>
										</div>
										<div class="text-xs text-muted-foreground mt-0.5 truncate">
											{app.company ?? '—'}
										</div>
									</div>
									<span
										class="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded-md bg-muted/40 text-muted-foreground border border-border/50 flex-shrink-0"
									>
										{app.status}
									</span>
								</button>
							</li>
						{/each}
					</ul>
				{/if}
			</div>

			<footer class="flex justify-end gap-2 px-5 py-3 border-t border-border/50">
				<button
					type="button"
					onclick={close}
					class="text-sm font-medium px-4 py-2 rounded-lg bg-muted/40 hover:bg-muted/60 text-foreground transition-colors active:scale-[0.98]"
				>
					Cancel
				</button>
			</footer>
		</div>
	</div>
{/if}
