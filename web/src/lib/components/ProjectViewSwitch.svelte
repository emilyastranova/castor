<script lang="ts">
	import { Icon } from '$lib/m3';

	/** Board | Dashboard for a project. */
	interface Props {
		projectId: string;
		current: 'board' | 'dashboard';
	}
	let { projectId, current }: Props = $props();

	const views = [
		{ id: 'board', label: 'Board', icon: 'view_kanban', href: (id: string) => `/projects/${id}` },
		{ id: 'dashboard', label: 'Dashboard', icon: 'space_dashboard', href: (id: string) => `/projects/${id}/dashboard` }
	] as const;
</script>

<div class="flex h-10 overflow-hidden rounded-full border border-outline" role="group" aria-label="Project view">
	{#each views as v, i (v.id)}
		<a
			href={v.href(projectId)}
			aria-current={current === v.id ? 'page' : undefined}
			class="state-layer inline-flex items-center gap-1.5 px-4 label-large transition-colors {i ? 'border-l border-outline' : ''} {current === v.id
				? 'bg-secondary-container text-on-secondary-container'
				: 'text-on-surface'}"
		>
			<Icon name={current === v.id ? 'check' : v.icon} size={18} />{v.label}
		</a>
	{/each}
</div>
