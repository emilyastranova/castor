<script lang="ts">
	import { api, ApiError, type Reaction } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { me } from '$lib/session';
	import { snackbar } from '$lib/snackbar.svelte';
	import { emojiName } from '$lib/emoji';
	import Emoji from './Emoji.svelte';
	import EmojiPicker from './EmojiPicker.svelte';

	/** The reactions under a comment or post: one chip per emoji (yours highlighted; clicking toggles
	 *  yours), and a button to add one. */
	interface Props {
		target: { comment?: string; post?: string };
		reactions: Reaction[];
		/** May react (operators in the project). */
		editable?: boolean;
		/** Show the add button even with no reactions yet (otherwise it shows on hover). */
		always?: boolean;
		onchange?: (reactions: Reaction[]) => void;
	}
	let { target, reactions = $bindable([]), editable = true, always = false, onchange }: Props = $props();

	let picking = $state(false);
	let addButton: HTMLButtonElement | null = $state(null);
	const myId = $derived(me()?.id ?? '');

	function who(r: Reaction): string {
		const names = r.user_ids.map((id, i) => (id === myId ? 'you' : r.names[i]));
		const list = names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names.at(-1)}` : names[0];
		return `${list} reacted with ${emojiName(r.emoji)}`;
	}

	async function toggle(emoji: string) {
		if (!editable) return;
		const mine = reactions.find((r) => r.emoji === emoji)?.user_ids.includes(myId);
		// At once, then as the server has it.
		const before = reactions;
		reactions = mine
			? reactions.map((r) => (r.emoji === emoji ? { ...r, count: r.count - 1, user_ids: r.user_ids.filter((u) => u !== myId),
				names: r.names.filter((_, i) => r.user_ids[i] !== myId) } : r)).filter((r) => r.count > 0)
			: reactions.some((r) => r.emoji === emoji)
				? reactions.map((r) => (r.emoji === emoji ? { ...r, count: r.count + 1, user_ids: [...r.user_ids, myId], names: [...r.names, 'you'] } : r))
				: [...reactions, { emoji, count: 1, user_ids: [myId], names: ['you'] }];
		try {
			reactions = mine ? await api().reactions.remove(target, emoji) : await api().reactions.add(target, emoji);
			onchange?.(reactions);
		} catch (err) {
			reactions = before;
			snackbar.error(err instanceof ApiError ? err.message.replace(/^emoji: /, '') : 'Couldn’t react');
		}
	}
</script>

{#if reactions.length || editable}
	<div class="reactions group/reactions mt-1.5 flex flex-wrap items-center gap-1" role="group" aria-label="Reactions">
		{#each reactions as r (r.emoji)}
			{@const mine = r.user_ids.includes(myId)}
			<button
				type="button"
				class="state-layer focus-ring inline-flex h-7 items-center gap-1 rounded-full border px-2 label-medium tabular-nums transition-colors
					{mine ? 'border-primary bg-primary-container text-on-primary-container' : 'border-outline-variant text-on-surface-variant'}
					{editable ? '' : 'cursor-default'}"
				aria-pressed={mine}
				title={who(r)}
				aria-label={who(r)}
				onclick={() => toggle(r.emoji)}
			>
				<Emoji emoji={r.emoji} size={16} />{r.count}
			</button>
		{/each}
		{#if editable}
			<button
				bind:this={addButton}
				type="button"
				class="state-layer focus-ring inline-flex h-7 items-center justify-center rounded-full px-1.5 text-on-surface-variant
					{reactions.length || always || picking ? '' : 'opacity-0 group-hover/reactions:opacity-100 focus-visible:opacity-100 [.reacts-host:hover_&]:opacity-100'}"
				title="Add a reaction"
				aria-label="Add a reaction"
				aria-expanded={picking}
				onclick={() => (picking = !picking)}
			>
				<Icon name="add_reaction" size={18} />
			</button>
			<EmojiPicker bind:open={picking} anchor={addButton} onpick={(e) => !reactions.find((r) => r.emoji === e)?.user_ids.includes(myId) && toggle(e)} />
		{/if}
	</div>
{/if}
