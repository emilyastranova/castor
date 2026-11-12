<script lang="ts">
	import type { UUID } from '$lib/api';
	import { Button, MenuItem } from '$lib/m3';
	import { pluginActions, type ActionPlace } from '$lib/plugins.svelte';

	/** The actions of the plugins enabled in a project, for one place: the project (Assets page),
	 *  a card (its menu) or an asset (its dialog). Renders nothing when there are none. */
	interface Props {
		projectId: UUID;
		place: ActionPlace;
		taskId?: UUID;
		assetId?: UUID;
		assetType?: string;
		/** Buttons (a toolbar), or menu items (inside a Menu). */
		as?: 'buttons' | 'menu';
		disabled?: boolean;
	}
	let { projectId, place, taskId, assetId, assetType, as = 'buttons', disabled = false }: Props = $props();

	$effect(() => void pluginActions.load(projectId));
	let actions = $derived(pluginActions.for(projectId, place, assetType));
	const run = (a: (typeof actions)[number]) =>
		pluginActions.start(a, { project_id: projectId, task_id: taskId, asset_id: assetId });
	const title = (a: (typeof actions)[number]) =>
		`${a.description || a.label} (plugin: ${a.plugin.name}${a.plugin.runtime === 'unrestricted' ? ', unrestricted: full access to the server' : ''})`;
</script>

{#each actions as a (`${a.plugin.id}:${a.id}`)}
	{#if as === 'menu'}
		<MenuItem icon={a.icon} disabled={disabled} onclick={() => run(a)}>{a.label}</MenuItem>
	{:else}
		<Button variant="text" icon={a.icon} {disabled} title={title(a)} onclick={() => run(a)}>{a.label}</Button>
	{/if}
{/each}
