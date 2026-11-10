<script lang="ts">
	import PersonName from '$lib/components/PersonName.svelte';
	import type { FeedItem } from '$lib/api';
	import { Avatar, Icon, IconButton, Menu, MenuItem } from '$lib/m3';
	import RichText from '../RichText.svelte';
	import { absoluteTime } from '$lib/format';
	import { me, canEdit, isAdmin } from '$lib/session';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '$lib/api';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import PostComposer from './PostComposer.svelte';
	import MoveWritingDialog from '../MoveWritingDialog.svelte';

	/** One timeline entry; how it looks depends on its kind. */
	interface Props {
		item: FeedItem;
		/** Show which project it's in (on the all-projects dashboard). */
		showProject?: boolean;
		/** The newest comment: she sits on it. */
		perched?: boolean;
		/** The hashtag the timeline is filtered by. */
		activeTag?: string | null;
	}
	let { item, showProject = true, activeTag = null, perched = false }: Props = $props();
	/** A hashtag opens the project's timeline filtered by it. */
	const tagLink = (tag: string) => `/projects/${item.project?.id}/dashboard?tag=${encodeURIComponent(tag)}`;
	let myId = $derived(me()?.id);

	const KIND: Record<string, { icon: string; tone: string }> = {
		comment: { icon: 'chat_bubble', tone: 'bg-secondary-container text-on-secondary-container' },
		job: { icon: 'terminal', tone: 'bg-primary-container text-on-primary-container' },
		card: { icon: 'note_stack_add', tone: 'bg-tertiary-container text-on-tertiary-container' },
		move: { icon: 'move_item', tone: 'bg-surface-container-highest text-on-surface-variant' },
		board: { icon: 'view_kanban', tone: 'bg-surface-container-highest text-on-surface-variant' },
		library: { icon: 'construction', tone: 'bg-secondary-container text-on-secondary-container' },
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
	/** Where the entry goes: the comment, run, file, card or item it's about (the server's link_for). */
	let link = $derived(item.link ?? null);
	let editing = $state(false); // a post being edited in place
	/** A post is already in full on its own project's Home; elsewhere it opens there. */
	let here = $derived(!!item.post && page.url.pathname === `/projects/${item.project?.id}/dashboard`);
	let clickable = $derived(!!link && !here && !editing);
	/** Clicking anywhere on the entry opens it, except on its own links, buttons and images, or
	 *  when selecting text. Ctrl/Cmd or the middle button open a new tab. */
	function open(e: MouseEvent) {
		if (!clickable || !link) return;
		if ((e.target as HTMLElement).closest('a, button, input, textarea, img, label, [role="menu"], [role="menuitem"], .mention')) return;
		if (window.getSelection()?.toString()) return;
		if (e.ctrlKey || e.metaKey || e.button === 1) window.open(link, '_blank', 'noopener');
		else void goto(link);
	}
	// The job outcome is the event itself ("job.failed"), not the job's state now.
	let outcome = $derived(item.kind === 'job' ? JOB[item.type.split('.')[1]] : undefined);
	let time = $derived(new Date(item.created_at).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' }));
	// A post: its author (or an admin) edits and deletes it here; long ones fold.
	let mayChange = $derived(!!item.post && canEdit() && (isAdmin() || (!!myId && item.post.author_id === myId)));
	let expanded = $state(false);
	let long = $derived(!!item.post && (item.post.body.split('\n').length > 12 || item.post.body.length > 1200));
	async function savePost(body: string) {
		await api().posts.update(item.post!.id, body);
		editing = false;
	}
	let moving = $state(false);
	async function movePost(toTaskId: string | null) {
		if (!toTaskId || !item.post || !item.project) return;
		const r = await api().posts.move(item.post.id, toTaskId);
		const pid = item.project.id;
		snackbar.show('Moved the post to that card', {
			action: { label: 'Open', run: () => void goto(`/projects/${pid}?task=${toTaskId}&comment=${r.comment?.id}`) }
		});
	}
	async function removePost() {
		if (!(await confirmAction({ title: 'Delete this post?', message: 'It leaves the timeline for everyone. Images in it stay in the project\'s files.', confirmLabel: 'Delete post' }))) return;
		try {
			await api().posts.remove(item.post!.id);
		} catch {
			snackbar.error("Couldn't delete the post");
		}
	}
	const secs = (s: number) => (s < 60 ? `${Math.round(s)}s` : `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`);
</script>

<div class="relative flex gap-3 pb-5 {perched ? 'perch-room' : ''}">
	<!-- the rail of the timeline -->
	{#if perched}
		<!-- With room above for her, the line runs down to the avatar and on from under it. -->
		<span class="rail-above absolute top-0 left-[19px] w-px bg-outline-variant/60" aria-hidden="true"></span>
		<span class="rail-below absolute bottom-0 left-[19px] w-px bg-outline-variant/60" aria-hidden="true"></span>
	{:else}
		<span class="absolute top-10 bottom-0 left-[19px] w-px bg-outline-variant/60" aria-hidden="true"></span>
	{/if}
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
	<!-- The whole entry opens what it's about; its own link (card, message) is the keyboard way in. -->
	<!-- svelte-ignore a11y_click_events_have_key_events, a11y_no_static_element_interactions -->
	<div
		class="min-w-0 flex-1 rounded-md pt-0.5 transition-colors {clickable ? '-mx-2 -my-1 cursor-pointer px-2 py-1 hover:bg-on-surface/5' : ''}"
		onclick={open}
		onauxclick={(e) => e.button === 1 && open(e)}
	>
		<div class="flex flex-wrap items-baseline gap-x-2 body-small text-on-surface-variant">
			{#if item.actor.username}
				<a href="/people/{item.actor.username}" class="label-large text-on-surface hover:underline"><PersonName name={item.actor.name} /></a>
			{:else}
				<span class="label-large text-on-surface"><PersonName name={item.actor.name} /></span>
			{/if}
			{#if item.via === 'mcp'}<span title="Done through an AI assistant connected over MCP">via MCP</span>{/if}
			{#if item.post}
				<span>posted{#if item.post.edited}<span class="italic"> · edited</span>{/if}</span>
			{:else if item.kind === 'comment'}
				<span>{item.type === 'comment.edited' ? 'edited a comment on' : item.type === 'comment.deleted' ? 'deleted a comment on' : item.type === 'comment.moved' ? `moved a ${item.message.startsWith('Moved a post') ? 'post' : 'comment'}${item.task ? ' to' : ''}` : 'commented on'}</span>
			{/if}
			{#if item.task}
				{#if link}<a href={link} class="min-w-0 truncate font-medium text-primary hover:underline">{item.task.name}</a>{:else}<span class="truncate">{item.task.name}</span>{/if}
			{/if}
			{#if showProject && item.project}
				<span class="rounded bg-surface-container-highest px-1.5 font-mono text-[11px]" title={item.project.name}>{item.project.codename}</span>
			{/if}
			<span class="ml-auto shrink-0" title={absoluteTime(item.created_at)}>{time}</span>
			{#if mayChange && !editing}
				<Menu>
					{#snippet anchor()}<IconButton icon="more_vert" label="Post actions" size="sm" class="-my-1.5" />{/snippet}
					<MenuItem icon="edit" onclick={() => (editing = true)}>Edit</MenuItem>
					<MenuItem icon="drive_file_move" onclick={() => (moving = true)}>Move to a card…</MenuItem>
					<MenuItem icon="delete" danger onclick={removePost}>Delete</MenuItem>
				</Menu>
			{/if}
		</div>

		{#if item.post}
			{#if editing}
				<div class="mt-2"><PostComposer projectId={item.project?.id ?? ''} initial={item.post.body} autofocus onsubmit={savePost} oncancel={() => (editing = false)} /></div>
			{:else}
				{@const forMe = !!myId && item.post.mentions.includes(myId)}
				<RichText text={item.post.body} projectId={item.project?.id} hashtags={item.project ? tagLink : undefined} {activeTag} class="mt-1.5 rounded-md bg-surface-container px-3.5 py-2.5 body-medium text-on-surface {long && !expanded ? 'line-clamp-[12]' : ''} {forMe ? 'mentions-me quiet' : ''}" />
				{#if long}<button type="button" class="touch-target mt-1 label-medium text-primary hover:underline" onclick={() => (expanded = !expanded)}>{expanded ? 'Show less' : 'Show all'}</button>{/if}
			{/if}
		{:else if item.kind === 'comment' && item.comment}
			{@const forMe = !!myId && item.comment.mentions?.includes(myId)}
			<div class="relative">
				<RichText text={item.comment.body} projectId={item.project?.id} class="mt-1.5 line-clamp-6 rounded-md bg-surface-container px-3.5 py-2.5 body-medium text-on-surface {forMe ? 'mentions-me quiet' : ''}" />
				{#if perched}
					<!-- She sits on the newest comment, legs over its front edge. -->
					<img src="/companion/sitting.webp" alt="" aria-hidden="true" class="companion" width="303" height="440" />
				{/if}
			</div>
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

{#if moving && item.post && item.project}
	<MoveWritingDialog bind:open={moving} projectId={item.project.id} what="post" onpick={movePost} />
{/if}

<style>
	/* The newest comment makes room above it for her, and she sits on its top edge: the seat line
	   in the picture (about 60% down, where her hand rests) lines up with the comment, so her legs hang over its front. */
	.perch-room {
		--room: 4.5rem;
		padding-top: var(--room);
	}
	.rail-above {
		height: var(--room);
	}
	.rail-below {
		top: calc(var(--room) + 2.5rem); /* under the 40px avatar */
	}
	.companion {
		--h: 150px;
		position: absolute;
		z-index: 5;
		top: calc(1px - var(--h) * 0.605); /* the comment's top margin collapses through its wrapper */
		right: 5.5rem;
		height: var(--h);
		width: auto;
		pointer-events: none;
		user-select: none;
		filter: drop-shadow(0 2px 2px rgb(0 0 0 / 0.28));
	}
	@media (max-width: 640px) {
		.perch-room {
			--room: 3.25rem;
		}
		.companion {
			--h: 116px;
			right: 3.5rem;
		}
	}
</style>
