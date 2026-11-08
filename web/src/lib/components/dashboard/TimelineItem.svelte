<script lang="ts">
	import type { FeedItem } from '$lib/api';
	import { Avatar, Icon } from '$lib/m3';
	import RichText from '../RichText.svelte';
	import { absoluteTime } from '$lib/format';
	import { me } from '$lib/session';

	/** One timeline entry; how it looks depends on its kind. */
	interface Props {
		item: FeedItem;
		/** Show which project it's in (on the all-projects dashboard). */
		showProject?: boolean;
	}
	let { item, showProject = true }: Props = $props();
	let myId = $derived(me()?.id);

	const KIND: Record<string, { icon: string; tone: string }> = {
		comment: { icon: 'chat_bubble', tone: 'bg-secondary-container text-on-secondary-container' },
		job: { icon: 'terminal', tone: 'bg-primary-container text-on-primary-container' },
		card: { icon: 'note_stack_add', tone: 'bg-tertiary-container text-on-tertiary-container' },
		move: { icon: 'move_item', tone: 'bg-surface-container-highest text-on-surface-variant' },
		file: { icon: 'attach_file', tone: 'bg-surface-container-highest text-on-surface-variant' },
		playbook: { icon: 'account_tree', tone: 'bg-primary-container text-on-primary-container' },
		agent: { icon: 'dns', tone: 'bg-surface-container-highest text-on-surface-variant' },
		note: { icon: 'push_pin', tone: 'bg-tertiary-container text-on-tertiary-container' },
		asset: { icon: 'radar', tone: 'bg-primary-container text-on-primary-container' },
		admin: { icon: 'shield_person', tone: 'bg-surface-container-highest text-on-surface-variant' }
	};
	const JOB: Record<string, { icon: string; text: string; label: string }> = {
		succeeded: { icon: 'check_circle', text: 'text-primary', label: 'Succeeded' },
		failed: { icon: 'error', text: 'text-error', label: 'Failed' },
		lost: { icon: 'link_off', text: 'text-error', label: 'Lost' },
		cancelled: { icon: 'cancel', text: 'text-on-surface-variant', label: 'Cancelled' },
		running: { icon: 'progress_activity', text: 'text-primary', label: 'Running' },
		queued: { icon: 'hourglass_empty', text: 'text-tertiary', label: 'Queued' },
		dispatched: { icon: 'hourglass_empty', text: 'text-tertiary', label: 'Queued' }
	};

	let meta = $derived(KIND[item.kind] ?? KIND.admin);
	let link = $derived(
		item.run_id
			? `/playbooks?run=${item.run_id}${item.step_id ? `&step=${item.step_id}` : ''}`
			: item.task && item.project
				? `/projects/${item.project.id}?task=${item.task.id}`
				: item.project
					? `/projects/${item.project.id}${item.kind === 'note' ? '/dashboard' : item.kind === 'asset' ? '/assets' : ''}`
					: null
	);
	// The job outcome is the event itself ("job.failed"), not the job's state now.
	let outcome = $derived(item.kind === 'job' ? JOB[item.type.split('.')[1]] : undefined);
	let time = $derived(new Date(item.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }));
	const secs = (s: number) => (s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`);
</script>

<div class="relative flex gap-3 pb-5">
	<!-- the rail of the timeline -->
	<span class="absolute top-10 bottom-0 left-[19px] w-px bg-outline-variant/60" aria-hidden="true"></span>
	{#if item.kind === 'comment' && item.actor.type === 'user'}
		{#if item.actor.username}
			<a href="/people/{item.actor.username}" class="shrink-0 rounded-full" aria-label="{item.actor.name}'s page"><Avatar name={item.actor.name} src={item.actor.avatar_url} size={40} tone="secondary" /></a>
		{:else}
			<Avatar name={item.actor.name} src={item.actor.avatar_url} size={40} tone="secondary" />
		{/if}
	{:else}
		<span class="flex size-10 shrink-0 items-center justify-center rounded-full {meta.tone}">
			<Icon name={outcome?.icon ?? meta.icon} size={20} />
		</span>
	{/if}
	<div class="min-w-0 flex-1 pt-0.5">
		<div class="flex flex-wrap items-baseline gap-x-2 body-small text-on-surface-variant">
			{#if item.actor.username}
				<a href="/people/{item.actor.username}" class="label-large text-on-surface hover:underline">{item.actor.name}</a>
			{:else}
				<span class="label-large text-on-surface">{item.actor.name}</span>
			{/if}
			{#if item.via === 'mcp'}<span title="Done through an AI assistant connected over MCP">via MCP</span>{/if}
			{#if item.kind === 'comment'}
				<span>commented on</span>
			{/if}
			{#if item.task}
				{#if link}<a href={link} class="min-w-0 truncate font-medium text-primary hover:underline">{item.task.name}</a>{:else}<span class="truncate">{item.task.name}</span>{/if}
			{/if}
			{#if showProject && item.project}
				<span class="rounded bg-surface-container-highest px-1.5 font-mono text-[11px]" title={item.project.name}>{item.project.codename}</span>
			{/if}
			<span class="ml-auto shrink-0" title={absoluteTime(item.created_at)}>{time}</span>
		</div>

		{#if item.kind === 'comment' && item.comment}
			{@const forMe = !!myId && item.comment.mentions?.includes(myId)}
			<RichText text={item.comment.body} projectId={item.project?.id} class="mt-1.5 line-clamp-6 rounded-md bg-surface-container px-3.5 py-2.5 body-medium text-on-surface {forMe ? 'mentions-me quiet' : ''}" />
		{:else if item.kind === 'job' && item.job}
			<div class="mt-1.5 flex flex-col gap-1.5 rounded-md border border-outline-variant/70 px-3.5 py-2.5">
				<div class="flex flex-wrap items-center gap-x-3 gap-y-1 body-small">
					{#if outcome}
						<span class="flex items-center gap-1 font-medium {outcome.text}"><Icon name={outcome.icon} size={16} />{outcome.label}</span>
					{/if}
					{#if item.job.exit_code !== null && item.job.exit_code !== 0}<span class="text-error">exit {item.job.exit_code}</span>{/if}
					{#if item.job.seconds !== null}<span class="text-on-surface-variant">{secs(item.job.seconds)}</span>{/if}
					{#if item.job.agent}<span class="flex items-center gap-1 text-on-surface-variant"><Icon name="dns" size={14} />{item.job.agent}</span>{/if}
				</div>
				<code class="block truncate font-mono text-[12px] text-on-surface-variant" title={item.job.command}>{item.job.command}</code>
			</div>
		{:else}
			<p class="mt-0.5 body-medium text-on-surface">
				{#if link && !item.task}<a href={link} class="hover:underline">{item.message}</a>{:else}{item.message}{/if}
			</p>
		{/if}
	</div>
</div>
