<script lang="ts">
	import { Icon, Menu } from '$lib/m3';
	import { people } from '$lib/people.svelte';

	/** Groups chosen for something (who approves runs, who's told when a card finishes): chips,
	 *  and when editable a menu of every group. */
	interface Props {
		label: string;
		/** Group ids. */
		value: string[];
		editable?: boolean;
		/** Shown when none are chosen. */
		empty?: string;
		/** Under the chips: what choosing groups does. */
		supporting?: string;
		onchange: (ids: string[]) => void;
	}
	let { label, value, editable = false, empty = 'None', supporting = '', onchange }: Props = $props();

	let all = $derived(people.groups().map((g) => g.group!));
	let chosen = $derived(value.map((id) => all.find((g) => g.id === id) ?? { id, slug: 'group', name: 'A group', member_ids: [] as string[] }));
	let filter = $state('');
	let shown = $derived(
		all.filter((g) => {
			const q = filter.trim().toLowerCase().replace(/^@/, '');
			return !q || g.name.toLowerCase().includes(q) || g.slug.toLowerCase().includes(q);
		})
	);
	const count = (n: number) => `${n} ${n === 1 ? 'person' : 'people'}`;
	const toggle = (id: string) => onchange(value.includes(id) ? value.filter((x) => x !== id) : [...value, id]);
</script>

<div class="flex flex-col gap-1.5">
	<div class="flex items-center gap-2">
		<span class="flex-1 label-large text-on-surface-variant">{label}</span>
		{#if editable}
			<Menu>
				{#snippet anchor()}
					<button type="button" class="state-layer touch-target flex size-7 items-center justify-center rounded-full text-on-surface-variant" aria-label="Choose groups: {label}" title="Choose groups"><Icon name="group_add" size={18} /></button>
				{/snippet}
				<div class="w-64" role="presentation" onclick={(e) => e.stopPropagation()}>
					<input class="mx-3 mb-1 h-9 w-[calc(100%-1.5rem)] rounded-xs border border-outline-variant bg-transparent px-2 body-medium text-on-surface outline-none focus:border-primary" placeholder="Find a group" bind:value={filter} />
					<ul class="max-h-64 overflow-y-auto">
						{#each shown as g (g.id)}
							<li>
								<button type="button" role="menuitemcheckbox" aria-checked={value.includes(g.id)} class="state-layer flex w-full items-center gap-2 px-3 py-1.5 text-left pointer-coarse:py-3" onclick={() => toggle(g.id)}>
									<span class="flex size-6 shrink-0 items-center justify-center rounded-full bg-secondary-container text-on-secondary-container"><Icon name="groups" size={16} /></span>
									<span class="flex min-w-0 flex-1 flex-col"><span class="truncate body-medium text-on-surface">{g.name}</span><span class="truncate body-small text-on-surface-variant">@{g.slug} · {count(g.member_ids.length)}</span></span>
									{#if value.includes(g.id)}<Icon name="check" size={18} class="text-primary" />{/if}
								</button>
							</li>
						{:else}
							<li class="px-3 py-2 body-small text-on-surface-variant">{all.length ? 'No group matches.' : 'No groups yet. Admins make them in Settings → Groups.'}</li>
						{/each}
					</ul>
				</div>
			</Menu>
		{/if}
	</div>
	{#if chosen.length}
		<ul class="flex flex-wrap gap-1.5">
			{#each chosen as g (g.id)}
				<li class="flex h-8 items-center gap-1.5 rounded-full bg-surface-container-high py-1 pl-1 {editable ? 'pr-1' : 'pr-3'}" title="{g.name}: {count(g.member_ids.length)}">
					<span class="flex size-6 items-center justify-center rounded-full bg-secondary-container text-on-secondary-container"><Icon name="groups" size={16} /></span>
					<span class="max-w-36 truncate label-large text-on-surface">@{g.slug}</span>
					{#if editable}
						<button type="button" class="state-layer touch-target flex size-6 items-center justify-center rounded-full text-on-surface-variant" aria-label="Remove @{g.slug}" title="Remove" onclick={() => toggle(g.id)}><Icon name="close" size={16} /></button>
					{/if}
				</li>
			{/each}
		</ul>
	{:else}
		<p class="body-small text-on-surface-variant">{empty}</p>
	{/if}
	{#if supporting}<p class="body-small text-on-surface-variant">{supporting}</p>{/if}
</div>
