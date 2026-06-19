<script lang="ts">
	import { onMount } from 'svelte';
	import { apiFetch } from '$lib/api';
	import { getProfileStatus } from '$lib/utils/easterEggs';
	import { AlertCircle, CheckCircle2, Code, Save } from 'lucide-svelte';

	interface Profile {
		id: number;
		full_name: string;
		email: string;
		phone?: string;
		location?: string;
		linkedin_url?: string;
		driver_license?: string;
		mobility?: string;
		base_cv_path?: string;
		base_letter_path?: string;
		additional_info?: Record<string, unknown>;
	}

	let { error = $bindable(''), successMsg = $bindable('') }: { error: string; successMsg: string } =
		$props();

	let saving = $state(false);

	let profileForm = $state({ full_name: '', email: '', phone: '', location: '', linkedin_url: '', driver_license: '', mobility: '', additional_info_json: '' });
	const profileEasterEgg = $derived(getProfileStatus(profileForm));
	let profileLoading = $state(true);

	// Compile-test ("Test template") state
	let compileTesting = $state(false);
	let compileOk = $state<boolean | null>(null);
	let compileErrorLog = $state('');

	async function loadProfile() {
		profileLoading = true;
		try {
			const p = await apiFetch<Profile>('/api/settings/profile');
			profileForm = {
				full_name: p.full_name ?? '',
				email: p.email ?? '',
				phone: p.phone ?? '',
				location: p.location ?? '',
				linkedin_url: p.linkedin_url ?? '',
				driver_license: p.driver_license ?? '',
				mobility: p.mobility ?? '',
				additional_info_json: p.additional_info ? JSON.stringify(p.additional_info, null, 2) : ''
			};
		} catch {
			// profile may not exist yet
		} finally {
			profileLoading = false;
		}
	}

	async function saveProfile() {
		saving = true;
		error = '';
		successMsg = '';
		let additional_info: Record<string, unknown> | undefined;
		if (profileForm.additional_info_json.trim()) {
			try {
				additional_info = JSON.parse(profileForm.additional_info_json);
			} catch {
				error = 'Additional info is not valid JSON';
				saving = false;
				return;
			}
		}
		try {
			await apiFetch('/api/settings/profile', {
				method: 'PUT',
				body: JSON.stringify({
					full_name: profileForm.full_name,
					email: profileForm.email,
					phone: profileForm.phone || null,
					location: profileForm.location || null,
					linkedin_url: profileForm.linkedin_url || null,
					driver_license: profileForm.driver_license || null,
					mobility: profileForm.mobility || null,
					additional_info: additional_info ?? null
				})
			});
			successMsg = 'Profile saved.';
		} catch (e: any) {
			error = e.message ?? 'Save failed';
		} finally {
			saving = false;
		}
	}

	async function testTemplate() {
		compileTesting = true;
		compileOk = null;
		compileErrorLog = '';
		try {
			const res = await apiFetch<{ ok: boolean; error_log: string | null }>(
				'/api/documents/compile-test',
				{ method: 'POST' }
			);
			compileOk = res.ok;
			compileErrorLog = res.error_log ?? '';
		} catch (e: any) {
			compileOk = false;
			compileErrorLog = e.message ?? 'Template test failed';
		} finally {
			compileTesting = false;
		}
	}

	onMount(() => {
		loadProfile();
	});
</script>

<style>
	@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&display=swap');

	.font-heading {
		font-family: 'Outfit', sans-serif;
	}
</style>

{#if profileLoading}
	<div class="space-y-4 animate-pulse">
		<div class="h-64 bg-card/50 border border-border/30 rounded-2xl"></div>
	</div>
{:else}
	<form onsubmit={(e) => { e.preventDefault(); saveProfile(); }} class="bg-card/40 border border-border/50 rounded-2xl p-6 md:p-8 shadow-sm space-y-6">
		<div class="space-y-1 mb-2">
			<h2 class="text-xl font-semibold font-heading">Personal Information</h2>
			<p class="text-xs text-muted-foreground">This information is used to automatically fill job application forms.</p>
			<p class="text-xs italic text-muted-foreground/70 mt-1">
				{profileEasterEgg.emoji} {profileEasterEgg.message}
			</p>
		</div>
		
		<div class="grid grid-cols-1 md:grid-cols-2 gap-5">
			<div class="space-y-1.5">
				<label class="text-sm font-medium text-foreground/90" for="full_name">Full name</label>
				<input id="full_name" type="text" bind:value={profileForm.full_name} placeholder="Jean Dupont"
					class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
			</div>
			<div class="space-y-1.5">
				<label class="text-sm font-medium text-foreground/90" for="email">Email address</label>
				<input id="email" type="email" bind:value={profileForm.email} placeholder="jean@exemple.fr"
					class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
			</div>
			<div class="space-y-1.5">
				<label class="text-sm font-medium text-foreground/90" for="phone">Phone number</label>
				<input id="phone" type="tel" bind:value={profileForm.phone} placeholder="+33 6 00 00 00 00"
					class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
			</div>
			<div class="space-y-1.5">
				<label class="text-sm font-medium text-foreground/90" for="location">Location</label>
				<input id="location" type="text" bind:value={profileForm.location} placeholder="Paris, France"
					class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
			</div>
			<div class="space-y-1.5">
				<label class="text-sm font-medium text-foreground/90" for="linkedin_url">LinkedIn URL</label>
				<input id="linkedin_url" type="url" bind:value={profileForm.linkedin_url} placeholder="https://www.linkedin.com/in/yourprofile"
					class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
			</div>
			<div class="space-y-1.5">
				<label class="text-sm font-medium text-foreground/90" for="driver_license">Driver license</label>
				<input id="driver_license" type="text" bind:value={profileForm.driver_license} placeholder="ex. B (voiture), A (moto)"
					class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
			</div>
			<div class="space-y-1.5">
				<label class="text-sm font-medium text-foreground/90" for="mobility">Mobility / Relocation</label>
				<input id="mobility" type="text" bind:value={profileForm.mobility} placeholder="ex. Île-de-France, ouvert à la mobilité"
					class="w-full text-sm px-3.5 py-2.5 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all placeholder:text-muted-foreground/40 shadow-sm" />
			</div>
		</div>
		
		<div class="space-y-1.5 pt-2">
			<div class="flex items-center gap-2">
				<label class="text-sm font-medium text-foreground/90" for="additional_info">Additional answers</label>
				<span class="text-[10px] uppercase tracking-wider font-semibold text-muted-foreground bg-muted px-2 py-0.5 rounded-full">JSON</span>
			</div>
			<p class="text-xs text-muted-foreground mb-2">Pre-defined answers to common application questions.</p>
			<textarea id="additional_info" rows={5} bind:value={profileForm.additional_info_json} placeholder={'{ "visa_required": "no" }'}
				class="w-full text-sm font-mono px-3.5 py-3 bg-background/50 border border-border/60 rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/20 focus:border-primary/50 transition-all resize-y shadow-sm placeholder:text-muted-foreground/30"
			></textarea>
		</div>
		
		{#if compileOk === true}
			<div class="flex items-center gap-2 text-sm font-medium text-emerald-600 bg-emerald-500/10 border border-emerald-500/20 rounded-lg px-4 py-3">
				<CheckCircle2 size={16} />
				Template compiles ✓
			</div>
		{:else if compileOk === false}
			<div class="space-y-2 bg-destructive/10 border border-destructive/20 rounded-lg px-4 py-3">
				<div class="flex items-center gap-2 text-sm font-medium text-destructive">
					<AlertCircle size={16} />
					Template failed to compile
				</div>
				{#if compileErrorLog}
					<pre class="text-xs font-mono text-destructive/90 whitespace-pre-wrap break-words max-h-64 overflow-auto bg-background/40 rounded-md p-3">{compileErrorLog}</pre>
				{/if}
			</div>
		{/if}

		<div class="pt-4 border-t border-border/30 flex flex-wrap justify-end gap-3">
			<button type="button" onclick={testTemplate} disabled={compileTesting}
				class="flex items-center gap-2 text-sm font-medium px-5 py-2.5 rounded-lg border border-border/60 bg-background/50 text-foreground hover:bg-muted/60 transition-all shadow-sm disabled:opacity-50 active:scale-[0.98]">
				<Code size={16} />
				{compileTesting ? 'Testing template...' : 'Test template'}
			</button>
			<button type="submit" disabled={saving}
				class="flex items-center gap-2 text-sm font-medium px-5 py-2.5 rounded-lg bg-primary text-primary-foreground hover:bg-primary/90 transition-all shadow-sm hover:shadow disabled:opacity-50 active:scale-[0.98]">
				<Save size={16} />
				{saving ? 'Saving Profile...' : 'Save Profile'}
			</button>
		</div>
	</form>
{/if}
