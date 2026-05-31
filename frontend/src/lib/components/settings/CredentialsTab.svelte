<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { Key, CheckCircle2, Save } from 'lucide-svelte';

	interface CredentialItem {
		site_name: string;
		display_name: string;
		masked_email: string | null;
		has_session: boolean;
	}

	// `onSessionCleared` lets the shell refresh the (always-mounted) Sites tab
	// so its session badges update after a credential session is cleared here.
	let {
		error = $bindable(''),
		successMsg = $bindable(''),
		onSessionCleared
	}: { error: string; successMsg: string; onSessionCleared?: () => void } = $props();

	let credentialsList = $state<CredentialItem[]>([]);
	let credentialsLoading = $state(true);
	let expandedCredential = $state<string | null>(null);
	let credFormMap = $state<Record<string, { email: string; password: string }>>({});
	let credSavingMap = $state<Record<string, boolean>>({});
	let sessionClearingMap = $state<Record<string, boolean>>({});

	async function loadCredentials() {
		credentialsLoading = true;
		try {
			credentialsList = await apiFetch<CredentialItem[]>('/api/settings/credentials');
		} catch {
			//
		} finally {
			credentialsLoading = false;
		}
	}

	async function saveCredential(siteName: string) {
		const form = credFormMap[siteName];
		if (!form?.email || !form?.password) return;
		credSavingMap = { ...credSavingMap, [siteName]: true };
		try {
			await apiFetch(`/api/settings/credentials/${siteName}`, {
				method: 'PUT',
				body: JSON.stringify({ email: form.email, password: form.password })
			});
			successMsg = `Credentials saved for ${siteName}.`;
			expandedCredential = null;
			await loadCredentials();
		} catch (e: any) {
			error = e.message ?? 'Save failed';
		} finally {
			credSavingMap = { ...credSavingMap, [siteName]: false };
		}
	}

	async function clearSession(siteName: string) {
		sessionClearingMap = { ...sessionClearingMap, [siteName]: true };
		try {
			await apiFetch(`/api/settings/credentials/${siteName}/session`, { method: 'DELETE' });
			successMsg = `Session cleared for ${siteName}.`;
			await loadCredentials();
			onSessionCleared?.();
		} catch (e: any) {
			error = e.message ?? 'Clear failed';
		} finally {
			sessionClearingMap = { ...sessionClearingMap, [siteName]: false };
		}
	}

	onMount(() => {
		loadCredentials();
	});
</script>

<style>
	@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap');

	.font-heading {
		font-family: 'Outfit', sans-serif;
	}
</style>

{#if credentialsLoading}
	<div class="space-y-4 animate-pulse">
		{#each Array(2) as _}<div class="h-24 bg-card/50 border border-border/30 rounded-xl"></div>{/each}
	</div>
{:else if credentialsList.length === 0}
	<div class="flex flex-col items-center justify-center p-12 text-center bg-card/30 border border-border/40 rounded-2xl border-dashed">
		<div class="w-12 h-12 bg-muted rounded-full flex items-center justify-center mb-4">
			<Key size={24} class="text-muted-foreground" />
		</div>
		<p class="text-base font-medium">No credentials required</p>
		<p class="text-sm text-muted-foreground mt-1 max-w-sm">None of your enabled sites currently require stored login credentials.</p>
	</div>
{:else}
	<div class="space-y-4">
		<div class="mb-6">
			<h2 class="text-xl font-semibold font-heading mb-1">Encrypted Credentials</h2>
			<p class="text-sm text-muted-foreground">Store login credentials for sites that require authentication. Securely encrypted using Fernet keys.</p>
		</div>

		<div class="space-y-4">
			{#each credentialsList as cred}
				<div class="group rounded-xl border transition-all duration-300 overflow-hidden {expandedCredential === cred.site_name ? 'border-primary/40 bg-primary/[0.02] shadow-md' : 'border-border/50 bg-card/40 hover:border-border/80 shadow-sm'}">
					<div class="flex items-center gap-4 p-4 md:p-5">
						<div class="w-10 h-10 rounded-full bg-background flex items-center justify-center border border-border/60 shadow-inner flex-shrink-0 {expandedCredential === cred.site_name ? 'border-primary/30' : ''}">
							<Key size={18} class="{expandedCredential === cred.site_name ? 'text-primary' : 'text-muted-foreground'} transition-colors" />
						</div>
						<div class="flex-1 min-w-0">
							<div class="flex items-center gap-2">
								<p class="text-sm font-semibold text-foreground">{cred.display_name}</p>
								{#if cred.has_session}
									<span class="flex items-center gap-1 text-[9px] uppercase tracking-wider font-bold text-green-400 bg-green-500/10 border border-green-500/20 px-1.5 py-0.5 rounded-md">
										<CheckCircle2 size={10} /> Active Session
									</span>
								{/if}
							</div>
							{#if cred.masked_email}
								<p class="text-xs text-muted-foreground mt-0.5 font-mono bg-muted/50 w-fit px-1.5 py-0.5 rounded">{cred.masked_email}</p>
							{:else}
								<p class="text-xs text-yellow-500/80 mt-0.5">No credentials stored</p>
							{/if}
						</div>
						<div class="flex items-center gap-2 flex-shrink-0">
							{#if cred.has_session}
								<button type="button" onclick={() => clearSession(cred.site_name)} disabled={sessionClearingMap[cred.site_name]}
									class="text-xs font-medium px-3 py-1.5 rounded-lg text-red-400 bg-red-500/10 hover:bg-red-500/20 transition-colors disabled:opacity-50">
									{sessionClearingMap[cred.site_name] ? 'Clearing...' : 'Clear Session'}
								</button>
							{/if}
							<button type="button" onclick={() => {
								if (expandedCredential === cred.site_name) { expandedCredential = null; } 
								else { expandedCredential = cred.site_name; if (!credFormMap[cred.site_name]) { credFormMap = { ...credFormMap, [cred.site_name]: { email: '', password: '' } }; } }
							}} class="text-xs font-medium px-3 py-1.5 rounded-lg transition-colors {expandedCredential === cred.site_name ? 'bg-muted text-foreground' : 'bg-primary/10 text-primary hover:bg-primary/20'}">
								{expandedCredential === cred.site_name ? 'Cancel' : (cred.masked_email ? 'Update' : 'Add')}
							</button>
						</div>
					</div>
					
					{#if expandedCredential === cred.site_name}
						<div class="border-t border-primary/10 bg-background/40 p-4 md:p-5 animate-in slide-in-from-top-2 duration-200">
							<form onsubmit={(e) => { e.preventDefault(); saveCredential(cred.site_name); }} class="flex flex-col md:flex-row gap-3">
								<div class="flex-1 space-y-1">
									<label class="text-xs font-medium text-foreground/80 pl-1" for="email-{cred.site_name}">Email</label>
									<input id="email-{cred.site_name}" type="email" placeholder="account@example.com" bind:value={credFormMap[cred.site_name].email} required
										class="w-full text-sm px-3.5 py-2.5 bg-background border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm" />
								</div>
								<div class="flex-1 space-y-1">
									<label class="text-xs font-medium text-foreground/80 pl-1" for="password-{cred.site_name}">Password</label>
									<input id="password-{cred.site_name}" type="password" placeholder="••••••••" bind:value={credFormMap[cred.site_name].password} required
										class="w-full text-sm px-3.5 py-2.5 bg-background border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all shadow-sm" />
								</div>
								<div class="flex items-end pb-0.5">
									<button type="submit" disabled={credSavingMap[cred.site_name]}
										class="w-full md:w-auto flex items-center justify-center gap-2 text-sm font-medium px-5 py-2.5 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-all shadow-sm disabled:opacity-50">
										<Save size={16} />
										{credSavingMap[cred.site_name] ? 'Saving...' : 'Save'}
									</button>
								</div>
							</form>
						</div>
					{/if}
				</div>
			{/each}
		</div>
	</div>
{/if}
