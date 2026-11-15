<script lang="ts">
	import { apiFetch } from '$lib/api';
	import { Globe, CheckCircle2 } from 'lucide-svelte';

	interface SiteItem {
		name: string;
		display_name: string;
		type: string;
		requires_login: boolean;
		base_url: string;
		enabled: boolean;
		has_session: boolean;
	}

	interface JobSiteAccount {
		key: string;
		method: string;
		email: string;
		status: string;
		created_at: string;
		last_used_at: string | null;
		login_url: string;
		note: string;
	}

	// `refreshKey` is bumped by the shell after a credential session is cleared,
	// so the session badges here stay in sync even while this tab is mounted-but-hidden.
	let { error = $bindable(''), refreshKey = 0 }: { error: string; refreshKey?: number } = $props();

	let sitesList = $state<SiteItem[]>([]);
	let sitesLoading = $state(true);
	let siteTogglingMap = $state<Record<string, boolean>>({});
	let accounts = $state<JobSiteAccount[]>([]);
	let accountsLoading = $state(true);
	let accountDeleting = $state<Record<string, boolean>>({});

	async function loadAccounts() {
		accountsLoading = true;
		try {
			accounts = await apiFetch<JobSiteAccount[]>('/api/settings/job-site-accounts');
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not load job-site accounts';
		} finally {
			accountsLoading = false;
		}
	}

	async function deleteAccount(key: string) {
		if (!confirm(`Remove ${key} from the list? The account on the site itself is not deleted.`)) return;
		accountDeleting = { ...accountDeleting, [key]: true };
		try {
			await apiFetch<void>(`/api/settings/job-site-accounts/${encodeURIComponent(key)}`, { method: 'DELETE' });
			accounts = accounts.filter((account) => account.key !== key);
		} catch (e) {
			error = e instanceof Error ? e.message : 'Could not delete job-site account';
		} finally {
			accountDeleting = { ...accountDeleting, [key]: false };
		}
	}

	function loginHref(value: string): string | null {
		try {
			const url = new URL(value);
			return ['https:', 'http:'].includes(url.protocol) ? url.href : null;
		} catch {
			return null;
		}
	}

	function accountDate(value: string | null): string {
		if (!value) return '—';
		// Registry timestamps are stored in UTC without a timezone suffix.
		const utc = /(?:Z|[+-]\d{2}:\d{2})$/i.test(value) ? value : `${value}Z`;
		return new Date(utc).toLocaleString();
	}

	async function loadSites() {
		sitesLoading = true;
		try {
			sitesList = await apiFetch<SiteItem[]>('/api/settings/sites');
		} catch {
			//
		} finally {
			sitesLoading = false;
		}
	}

	async function toggleSite(site: SiteItem) {
		siteTogglingMap = { ...siteTogglingMap, [site.name]: true };
		try {
			await apiFetch(`/api/settings/sites/${site.name}`, {
				method: 'PUT',
				body: JSON.stringify({ enabled: !site.enabled })
			});
			sitesList = sitesList.map((s) => s.name === site.name ? { ...s, enabled: !s.enabled } : s);
		} catch (e: any) {
			error = e.message ?? 'Toggle failed';
		} finally {
			siteTogglingMap = { ...siteTogglingMap, [site.name]: false };
		}
	}

	// Runs once on mount and again whenever `refreshKey` changes.
	$effect(() => {
		void refreshKey;
		loadSites();
		loadAccounts();
	});
</script>

<style>
	@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap');

	.font-heading {
		font-family: 'Outfit', sans-serif;
	}
</style>

{#if sitesLoading}
	<div class="space-y-3 animate-pulse">
		{#each Array(4) as _}<div class="h-20 bg-card/50 border border-border/30 rounded-xl"></div>{/each}
	</div>
{:else}
	<div class="space-y-4">
		<div class="mb-6">
			<h2 class="text-xl font-semibold font-heading mb-1">Job Sources</h2>
			<p class="text-sm text-muted-foreground">Enable or disable job source sites. Disabled sites will be skipped during the scraping batch.</p>
		</div>
		
		<div class="grid grid-cols-1 md:grid-cols-2 gap-4">
			{#each sitesList as site}
				<div class="group flex items-center justify-between p-4 bg-card/40 border border-border/50 rounded-xl hover:bg-card/80 hover:border-border transition-all shadow-sm hover:shadow-md">
					<div class="flex items-center gap-4 overflow-hidden">
						<div class="w-10 h-10 rounded-full bg-background flex items-center justify-center border border-border/60 shadow-inner flex-shrink-0">
							<Globe size={18} class="text-muted-foreground group-hover:text-primary transition-colors duration-300" />
						</div>
						<div class="flex flex-col min-w-0">
							<div class="flex items-center gap-2">
								<p class="text-sm font-semibold text-foreground truncate">{site.display_name}</p>
								{#if site.has_session}
									<span class="flex items-center gap-1 text-[9px] uppercase tracking-wider font-bold text-green-400 bg-green-500/10 border border-green-500/20 px-1.5 py-0.5 rounded-md flex-shrink-0">
						<CheckCircle2 size={10} />Session</span>
								{/if}
							</div>
							<p class="text-xs text-muted-foreground truncate">{site.base_url || site.type}</p>
						</div>
					</div>
					<button
						type="button" role="switch" aria-checked={site.enabled} aria-label="Toggle {site.display_name}"
						onclick={() => toggleSite(site)}
						disabled={siteTogglingMap[site.name]}
						class="relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 ml-4 disabled:opacity-50 {site.enabled ? 'bg-primary' : 'bg-muted-foreground/30'}"
					>
						<span class="pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow-lg ring-0 transition duration-200 ease-in-out {site.enabled ? 'translate-x-5' : 'translate-x-0'}"></span>
					</button>
				</div>
			{/each}
		</div>
	</div>
{/if}

<section class="mt-8 rounded-xl border border-border/50 bg-card/40 p-5">
	<h2 class="mb-4 text-xl font-semibold font-heading">Job-site accounts</h2>
	{#if accountsLoading}
		<p class="text-sm text-muted-foreground">Loading accounts…</p>
	{:else if accounts.length === 0}
		<p class="text-sm text-muted-foreground">JobPilot lists accounts here when it signs up or signs in on a job site.</p>
	{:else}
		<div class="overflow-x-auto">
			<table class="w-full text-left text-sm">
				<thead class="text-xs text-muted-foreground">
					<tr>
						{#each ['Site', 'Method', 'Email', 'Status', 'Created', 'Last used', 'Login', 'Actions'] as heading}
							<th scope="col" class="px-3 pb-3 font-medium">{heading}</th>
						{/each}
					</tr>
				</thead>
				<tbody>
					{#each accounts as account (account.key)}
						{@const href = loginHref(account.login_url)}
						<tr class="border-t border-border/40">
							<td class="px-3 py-3 font-medium">{account.key}</td>
							<td class="px-3 py-3 capitalize">{account.method}</td>
							<td class="px-3 py-3">{account.email}</td>
							<td class="px-3 py-3">{account.status}</td>
							<td class="whitespace-nowrap px-3 py-3">{accountDate(account.created_at)}</td>
							<td class="whitespace-nowrap px-3 py-3">{accountDate(account.last_used_at)}</td>
							<td class="px-3 py-3">
								{#if href}<a href={href} target="_blank" rel="noopener noreferrer" class="text-primary hover:underline">Sign in</a>{:else}—{/if}
							</td>
							<td class="px-3 py-3">
								<button type="button" class="text-destructive hover:underline disabled:opacity-50" aria-label="Delete account for {account.key}" disabled={accountDeleting[account.key]} onclick={() => deleteAccount(account.key)}>
									{accountDeleting[account.key] ? 'Deleting…' : 'Delete'}
								</button>
							</td>
						</tr>
					{/each}
				</tbody>
			</table>
		</div>
	{/if}
</section>
