<script lang="ts">
	import { goto } from '$app/navigation';
	import type { AppNotification } from '$lib/api';
	import { Button, Icon, IconButton, Switch } from '$lib/m3';
	import { notifications, SEVERITY } from '$lib/notifications.svelte';
	import { absoluteTime, relativeTime } from '$lib/format';

	/** Bell in the navigation rail with an unread badge; opens a panel beside the rail. */
	let open = $state(false);
	let root: HTMLDivElement | undefined = $state();
	let showRead = $state(false);

	let shown = $derived(showRead ? notifications.items : notifications.items.filter((n) => !n.read_at));

	function onDocClick(e: MouseEvent) {
		if (open && root && !root.contains(e.target as Node)) open = false;
	}

	function visit(n: AppNotification) {
		void notifications.read(n);
		open = false;
		if (n.link) void goto(n.link);
	}
</script>

<svelte:document onclick={onDocClick} onkeydown={(e) => e.key === 'Escape' && (open = false)} />

<div class="relative" bind:this={root}>
	<button
		type="button"
		class="state-layer focus-ring relative flex size-10 items-center justify-center rounded-full text-on-surface-variant"
		aria-label={notifications.unread ? `Notifications, ${notifications.unread} unread` : 'Notifications'}
		title="Notifications"
		onclick={() => {
			open = !open;
			if (open) void notifications.load();
		}}
	>
		<Icon name="notifications" filled={notifications.unread > 0} />
		{#if notifications.unread}
			<span class="absolute top-0.5 right-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-error px-1 text-[10px] leading-none font-medium text-on-error">
				{notifications.unread > 99 ? '99+' : notifications.unread}
			</span>
		{/if}
	</button>

	{#if open}
		<div
			class="fixed bottom-3 left-[5.5rem] z-50 flex max-h-[min(36rem,85vh)] w-[min(26rem,calc(100vw-6.5rem))] flex-col overflow-hidden rounded-lg bg-surface-container shadow-e3"
			role="dialog"
			aria-label="Notifications"
		>
			<div class="flex items-center gap-2 border-b border-outline-variant px-4 py-3">
				<h2 class="flex-1 title-medium text-on-surface">Notifications</h2>
				<button type="button" class="label-medium text-primary hover:underline" onclick={() => (showRead = !showRead)}>
					{showRead ? 'Unread only' : 'Show all'}
				</button>
				{#if notifications.unread}
					<IconButton icon="done_all" label="Mark all read" size="sm" onclick={() => notifications.readAll()} />
				{/if}
			</div>
			<ul class="min-h-24 flex-1 overflow-auto">
				{#each shown as n (n.id)}
					{@const sev = SEVERITY[n.severity] ?? SEVERITY.info}
					<li class="border-b border-outline-variant/50 last:border-b-0">
						<div class="flex items-start gap-3 px-4 py-3 {n.read_at ? 'opacity-70' : ''}">
							<Icon name={sev.icon} size={20} class="mt-0.5 shrink-0 {sev.tone}" filled />
							<button type="button" class="min-w-0 flex-1 text-left" onclick={() => visit(n)}>
								<p class="body-medium text-on-surface {n.read_at ? '' : 'font-medium'}">{n.title}</p>
								{#if n.body}<p class="mt-0.5 line-clamp-3 body-small break-words text-on-surface-variant">{n.body}</p>{/if}
								<p class="mt-1 label-small text-on-surface-variant" title={absoluteTime(n.created_at)}>{relativeTime(n.created_at)}</p>
							</button>
							{#if !n.read_at}
								<IconButton icon="check" label="Mark read" size="sm" onclick={() => notifications.read(n)} />
							{/if}
						</div>
					</li>
				{:else}
					<li class="flex flex-col items-center gap-2 px-4 py-10 text-center text-on-surface-variant">
						<Icon name="notifications_off" size={32} />
						<span class="body-medium">{showRead ? 'No notifications yet.' : 'You’re all caught up.'}</span>
						<span class="body-small">Playbook steps that need a decision show up here.</span>
					</li>
				{/each}
			</ul>
			<div class="flex items-center gap-3 border-t border-outline-variant px-4 py-2.5">
				{#if notifications.permission === 'unsupported'}
					<span class="body-small text-on-surface-variant">This browser can’t show desktop notifications.</span>
				{:else if notifications.permission === 'denied'}
					<span class="body-small text-on-surface-variant">Desktop notifications are blocked in this browser’s site settings.</span>
				{:else}
					<span class="flex-1 body-small text-on-surface-variant">Desktop notifications</span>
					{#if notifications.permission === 'granted'}
						<Switch
							checked={notifications.desktop}
							onchange={(v: boolean) => (v ? notifications.enableDesktop() : notifications.disableDesktop())}
						/>
					{:else}
						<Button variant="tonal" icon="notifications_active" onclick={() => notifications.enableDesktop()}>Turn on</Button>
					{/if}
				{/if}
			</div>
		</div>
	{/if}
</div>
