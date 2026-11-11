<script lang="ts">
	import type { Client, Project } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Button, Dialog, TextField, Select, Switch } from '$lib/m3';
	import { aiStatus } from '$lib/ai.svelte';
	import { snackbar } from '$lib/snackbar.svelte';

	interface Props {
		open?: boolean;
		clients: Client[];
		project?: Project | null;
		oncreated?: (project: Project) => void;
		onsaved?: (project: Project) => void;
	}
	let { open = $bindable(false), clients, project = null, oncreated, onsaved }: Props = $props();

	let codename = $state('');
	let name = $state('');
	let kind = $state('pipeline');
	let clientId = $state('');
	let newClient = $state('');
	let description = $state('');
	let summaries = $state(false);
	let mentions = $state(true);
	let pictures = $state(false);
	$effect(() => void aiStatus.load());
	let busy = $state(false);

	$effect(() => {
		if (open) {
			codename = project?.codename ?? '';
			name = project?.name ?? '';
			kind = project?.kind ?? 'pipeline';
			clientId = project?.client_id ?? '';
			description = project?.description ?? '';
			summaries = !!project?.settings?.ai_summaries;
			mentions = project?.settings?.asset_mentions !== false;
			pictures = !!project?.settings?.ai_read_pictures;
			newClient = '';
		}
	});

	const kinds = ['pipeline', 'automation', 'dataflow', 'infrastructure', 'scheduled', 'internal', 'external', 'other'];

	async function submit(e: SubmitEvent) {
		e.preventDefault();
		busy = true;
		try {
			let client_id: string | null = clientId || null;
			if (clientId === '__new' && newClient.trim()) {
				const c = await api().clients.create({ name: newClient.trim() });
				client_id = c.id;
			}
			if (project) {
				const settings = { ...project.settings, ai_summaries: summaries, asset_mentions: mentions, ai_read_pictures: pictures };
				const saved = await api().projects.update(project.id, { codename, name, kind, client_id, description, settings });
				onsaved?.(saved);
			} else {
				const created = await api().projects.create({ codename, name, kind, client_id, description, settings: { ai_summaries: summaries, asset_mentions: mentions, ai_read_pictures: pictures } });
				oncreated?.(created);
			}
			open = false;
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not save project');
		} finally {
			busy = false;
		}
	}
</script>

<Dialog bind:open title={project ? 'Edit project' : 'New project'} icon="view_kanban">
	<form id="project-form" class="flex flex-col gap-5 pt-2 pb-2" onsubmit={submit}>
		<div class="grid gap-4 sm:grid-cols-[1fr_2fr]">
			<TextField label="Codename" bind:value={codename} required placeholder="ACME-26-001" autofocus />
			<TextField label="Name" bind:value={name} required placeholder="ACME external perimeter" />
		</div>
		<div class="grid gap-4 sm:grid-cols-2">
			<Select label="Type" bind:value={kind} options={kinds.map((k) => ({ value: k, label: k }))} />
			<Select
				label="Client"
				bind:value={clientId}
				options={[{ value: '', label: 'No client' }, ...clients.map((c) => ({ value: c.id, label: c.name })), { value: '__new', label: '+ New client…' }]}
			/>
		</div>
		{#if clientId === '__new'}
			<TextField label="Client name" bind:value={newClient} required />
		{/if}
		<TextField label="Description" bind:value={description} multiline rows={3} />
		<div>
			<Switch bind:checked={mentions} label="Suggest assets that are mentioned" />
			<p class="mt-1 body-small text-on-surface-variant">
				Addresses, names, URLs and CVEs in comments, posts, card descriptions, notes and output no parser reads show up under Assets → Suggested to confirm. Read on this server; nothing is sent anywhere.
			</p>
		</div>
		<div>
			<Switch bind:checked={pictures} label="Read pictures for assets with AI" />
			<p class="mt-1 body-small text-on-surface-variant">
				Screenshots and other pictures added to the project are sent to the AI model, which suggests the assets it can read in them. {aiStatus.enabled ? 'Needs a model that reads images.' : 'Needs an AI model (Settings → AI).'}
			</p>
		</div>
		<div>
			<Switch bind:checked={summaries} label="AI summaries of runs" />
			<p class="mt-1 body-small text-on-surface-variant">
				When a command or playbook run finishes, or a manual card reaches Done, the AI writes a few lines on its card: what was found, what failed and what to look at next.
				{aiStatus.enabled ? 'It reads the parsed results and the end of the output, or a manual card\'s description and comments (secrets stay masked).' : 'Needs an AI model (Settings → AI).'}
			</p>
		</div>
	</form>
	{#snippet actions()}
		<Button variant="text" onclick={() => (open = false)}>Cancel</Button>
		<Button type="submit" form="project-form" loading={busy}>{project ? 'Save' : 'Create'}</Button>
	{/snippet}
</Dialog>
