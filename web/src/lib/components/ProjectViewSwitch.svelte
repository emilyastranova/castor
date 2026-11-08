<script lang="ts">
	import { Icon } from '$lib/m3';

	/** Board | Dashboard | Assets for a project. */
	interface Props {
		projectId: string;
		current: 'board' | 'dashboard' | 'assets';
	}
	let { projectId, current }: Props = $props();

	const views = [
		{ id: 'board', label: 'Board', icon: 'view_kanban', href: (id: string) => `/projects/${id}` },
		{ id: 'dashboard', label: 'Dashboard', icon: 'space_dashboard', href: (id: string) => `/projects/${id}/dashboard` },
		{ id: 'assets', label: 'Assets', icon: 'radar', href: (id: string) => `/projects/${id}/assets` }
	] as const;
</script>

<div class="flex h-10 overflow-hidden rounded-full border border-outline" role="group" aria-label="Project view">
	{#each views as v, i (v.id)}
		<a
			href={v.href(projectId)}
			aria-current={current === v.id ? 'page' : undefined}
			class="state-layer inline-flex items-center gap-1.5 px-3 label-large transition-colors sm:px-4 {i ? 'border-l border-outline' : ''} {current === v.id
				? 'bg-secondary-container text-on-secondary-container'
				: 'text-on-surface'}"
		>
			<!-- Small screens: only the selected view keeps its icon (the check), so all three fit at 320px. -->
			{#if current === v.id}
				<Icon name="check" size={18} />
			{:else}
				<span class="hidden sm:contents"><Icon name={v.icon} size={18} /></span>
			{/if}{v.label}
		</a>
	{/each}
</div>
