<script lang="ts">
	import { IconButton, Icon, TextField } from '$lib/m3';
	import { notifications } from '$lib/notifications.svelte';
	import { DUE_TONE, dueInfo, fromInputs, toInputs } from '$lib/due';

	/** A card's due date: a date and, if it matters, a time (none: the end of that day). Its
	 *  people are reminded the day before and when it's overdue, on the desktop too if allowed. */
	interface Props {
		value: string | null;
		editable?: boolean;
		/** Whether the card is finished (done, reported): no "overdue" then. */
		finished?: boolean;
		onchange: (iso: string | null) => void;
		/** Ask for desktop notifications next to it (not in the New card dialog). */
		desktopHint?: boolean;
	}
	let { value, editable = true, finished = false, onchange, desktopHint = true }: Props = $props();

	let date = $state('');
	let time = $state('');
	let shownFor: string | null | undefined = undefined;
	$effect(() => {
		if (value === shownFor) return;
		shownFor = value;
		({ date, time } = toInputs(value));
	});

	let timer: ReturnType<typeof setTimeout> | undefined;
	function changed() {
		clearTimeout(timer);
		timer = setTimeout(() => {
			const next = fromInputs(date, time);
			if (next === value || (next && value && new Date(next).getTime() === new Date(value).getTime())) return;
			shownFor = next;
			onchange(next);
		}, 350);
	}
	function clear() {
		date = time = '';
		clearTimeout(timer);
		shownFor = null;
		onchange(null);
	}
	let info = $derived(value ? dueInfo(value, finished) : null);
</script>

<div class="@container flex flex-col gap-1.5">
	<div class="flex items-center gap-2">
		<span class="flex-1 label-large text-on-surface-variant">Due</span>
		{#if info}
			<span class="inline-flex items-center gap-1 rounded-sm px-1.5 py-0.5 label-small {DUE_TONE[info.tone]}" title={info.title}>
				<Icon name={info.icon} size={14} />{info.label}
			</span>
		{/if}
		{#if editable && value}<IconButton icon="close" size="sm" label="Remove the due date" onclick={clear} />{/if}
	</div>
	<!-- Side by side where there's room (New card); stacked in the card's narrow side column. -->
	<div class="grid grid-cols-1 gap-x-2 gap-y-3 @sm:grid-cols-[minmax(0,1fr)_minmax(0,10rem)]">
		<TextField label="Date" type="date" bind:value={date} readonly={!editable} oninput={changed} class="min-w-0" />
		<TextField label="Time (optional)" type="time" bind:value={time} readonly={!editable || !date} oninput={changed} class="min-w-0" supporting={date && !time ? 'Empty: the end of that day' : undefined} />
	</div>
	{#if desktopHint && value && !finished && notifications.permission !== 'unsupported'}
		{#if notifications.permission === 'denied'}
			<p class="body-small text-on-surface-variant">Assignees (or whoever made the card) get a reminder a day before and when it's overdue. Desktop notifications are blocked in this browser.</p>
		{:else if !notifications.desktop}
			<p class="body-small text-on-surface-variant">
				Assignees (or whoever made the card) get a reminder a day before and when it's overdue.
				<button type="button" class="text-primary hover:underline" onclick={() => void notifications.enableDesktop()}>Show them on my desktop</button>
			</p>
		{:else}
			<p class="body-small text-on-surface-variant">Assignees (or whoever made the card) get a reminder a day before and when it's overdue; yours show on this desktop too.</p>
		{/if}
	{/if}
</div>
