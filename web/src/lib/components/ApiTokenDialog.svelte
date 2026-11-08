<script lang="ts">
	import type { ApiScope, ApiToken, ProjectSummary, UserRole } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Chip, Dialog, Icon, Select, TextField } from '$lib/m3';
	import { snackbar } from '$lib/snackbar.svelte';

	/** Make a personal API token. The token itself is shown here once, after it's made. */
	interface Props {
		open: boolean;
		role: UserRole;
		oncreated?: (token: ApiToken) => void;
	}
	let { open = $bindable(false), role, oncreated }: Props = $props();

	const RANK: Record<UserRole, number> = { viewer: 0, operator: 1, admin: 2 };
	const EXPIRY = [
		{ value: '30', label: '30 days' },
		{ value: '90', label: '90 days' },
		{ value: '365', label: 'A year' },
		{ value: 'never', label: 'Never' }
	];

	let scopes = $state<ApiScope[]>([]);
	let projects = $state<ProjectSummary[]>([]);
	let name = $state('');
	let preset = $state<'read' | 'all' | 'custom'>('read');
	let picked = $state<string[]>([]);
	let limitProjects = $state(false);
	let projectIds = $state<string[]>([]);
	let expiry = $state('90');
	let creating = $state(false);
	let created = $state<(ApiToken & { token: string }) | null>(null);
	let copied = $state<string | null>(null);
	let openedFor = false;

	$effect(() => {
		if (open === openedFor) return;
		openedFor = open;
		if (!open) return;
		name = '';
		preset = 'read';
		picked = [];
		limitProjects = false;
		projectIds = [];
		expiry = '90';
		created = null;
		copied = null;
		Promise.all([api().apiTokens.scopes(), api().projects.list()])
			.then(([s, p]) => {
				scopes = s;
				projects = p;
			})
			.catch(() => snackbar.error('Could not load the scopes'));
	});

	let usable = $derived(scopes.filter((s) => RANK[role] >= RANK[s.role]));
	let groups = $derived([...new Set(usable.map((s) => s.group))]);
	let chosen = $derived(
		preset === 'read' ? usable.filter((s) => s.name.endsWith(':read')).map((s) => s.name) : preset === 'all' ? usable.map((s) => s.name) : picked
	);
	let origin = $derived(typeof location === 'undefined' ? '' : location.origin);
	let example = $derived(created ? `curl -H "Authorization: Bearer ${created.token}" \\\n  ${origin}/api/projects` : '');

	function toggle(list: string[], value: string): string[] {
		return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
	}
	function toggleScope(s: ApiScope) {
		if (preset !== 'custom') {
			picked = [...chosen];
			preset = 'custom';
		}
		picked = toggle(picked, s.name);
	}

	async function create() {
		creating = true;
		try {
			created = await api().apiTokens.create({
				name: name.trim(),
				scopes: chosen,
				project_ids: limitProjects ? projectIds : null,
				expires_in_days: expiry === 'never' ? null : Number(expiry)
			});
			oncreated?.(created);
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not create the token');
		} finally {
			creating = false;
		}
	}

	async function copy(text: string, what: string) {
		try {
			await navigator.clipboard.writeText(text);
			copied = what;
			setTimeout(() => (copied = copied === what ? null : copied), 2000);
		} catch {
			snackbar.error('Copying needs clipboard access; select the text instead');
		}
	}
</script>

<Dialog bind:open title={created ? `Token “${created.name}”` : 'New API token'} icon="key" size="md">
	<div class="flex flex-col gap-4 pb-2">
		{#if !created}
			<p class="body-medium text-on-surface">
				A token acts as you from scripts and other tools, with only the access you give it here. You can revoke it any time.
			</p>
			<TextField label="Name" bind:value={name} placeholder="e.g. CI pipeline" supporting="What it's for, so you recognise it later." />

			<div class="flex flex-col gap-2">
				<span class="label-large text-on-surface">Access</span>
				<div class="flex flex-wrap gap-2">
					<Chip variant="filter" selected={preset === 'read'} onclick={() => (preset = 'read')}>Read only</Chip>
					<Chip variant="filter" selected={preset === 'all'} onclick={() => (preset = 'all')}>Everything you can do</Chip>
					<Chip variant="filter" selected={preset === 'custom'} onclick={() => ((picked = [...chosen]), (preset = 'custom'))}>Pick scopes</Chip>
				</div>
				<div class="flex flex-col gap-3 rounded-md border border-outline-variant/60 p-3">
					{#each groups as g (g)}
						<div>
							<p class="mb-1.5 label-medium text-on-surface-variant">{g}</p>
							<div class="flex flex-wrap gap-1.5">
								{#each usable.filter((s) => s.group === g) as s (s.name)}
									<Chip variant="filter" selected={chosen.includes(s.name)} title={`${s.name}: ${s.description}`} onclick={() => toggleScope(s)}>{s.label}</Chip>
								{/each}
							</div>
						</div>
					{/each}
				</div>
				<p class="body-small text-on-surface-variant">Your role still applies: a token can never do more than you can.</p>
			</div>

			<div class="flex flex-col gap-2">
				<span class="label-large text-on-surface">Projects</span>
				<div class="flex flex-wrap gap-2">
					<Chip variant="filter" selected={!limitProjects} onclick={() => (limitProjects = false)}>Every project you can see</Chip>
					<Chip variant="filter" selected={limitProjects} onclick={() => (limitProjects = true)}>Only some</Chip>
				</div>
				{#if limitProjects}
					<div class="flex max-h-40 flex-wrap gap-1.5 overflow-auto rounded-md border border-outline-variant/60 p-3">
						{#each projects as p (p.id)}
							<Chip variant="filter" selected={projectIds.includes(p.id)} title={p.name} onclick={() => (projectIds = toggle(projectIds, p.id))}>{p.codename}</Chip>
						{:else}
							<span class="body-small text-on-surface-variant">You can't see any projects.</span>
						{/each}
					</div>
					<p class="body-small text-on-surface-variant">Other projects don't exist as far as the token is concerned, and it can't create projects.</p>
				{/if}
			</div>

			<Select label="Expires" bind:value={expiry} options={EXPIRY} />
		{:else}
			<div class="flex items-start gap-2 rounded-md bg-tertiary-container/50 px-3 py-2 body-small text-on-surface">
				<Icon name="warning" size={18} class="mt-px shrink-0 text-tertiary" />
				<span>Copy this token now: it's shown only once. Castor keeps only a hash of it. Treat it like a password.</span>
			</div>
			<div>
				<div class="mb-1 flex items-center justify-between">
					<span class="label-large text-on-surface">Token</span>
					<Button variant="text" icon={copied === 'token' ? 'check' : 'content_copy'} onclick={() => copy(created!.token, 'token')}>{copied === 'token' ? 'Copied' : 'Copy'}</Button>
				</div>
				<code class="block rounded-sm bg-surface-container-highest px-3 py-2 font-mono text-[12.5px] break-all text-on-surface select-all">{created.token}</code>
			</div>
			<div>
				<div class="mb-1 flex items-center justify-between">
					<span class="label-large text-on-surface">Try it</span>
					<Button variant="text" icon={copied === 'curl' ? 'check' : 'content_copy'} onclick={() => copy(example, 'curl')}>{copied === 'curl' ? 'Copied' : 'Copy'}</Button>
				</div>
				<pre class="overflow-auto rounded-sm bg-surface-container-highest p-3 font-mono text-[12.5px] text-on-surface">{example}</pre>
				<p class="mt-1.5 body-small text-on-surface-variant">
					Every endpoint, and the scope it needs, is in the <a class="text-primary underline" href="/api/docs" target="_blank" rel="noopener">API reference</a>.
				</p>
			</div>
		{/if}
	</div>
	{#snippet actions()}
		{#if created}
			<Button onclick={() => (open = false)}>Done</Button>
		{:else}
			<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
			<Button icon="key" loading={creating} disabled={!name.trim() || !chosen.length || (limitProjects && !projectIds.length)} onclick={create}>Create token</Button>
		{/if}
	{/snippet}
</Dialog>
