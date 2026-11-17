<script lang="ts">
	import { Icon } from '$lib/m3';
	import { hopAddress, hopKind } from '$lib/routes';
	import type { HopKind } from '$lib/api';

	/** One node of the chain as drawn: a hop, with what's known about reaching it. */
	export interface ChainHop {
		key: string;
		kind: HopKind;
		host: string;
		port: number | null;
		username: string;
		label?: string;
		/** none | password | key; "new" for a secret typed but not saved yet. */
		auth: string;
		/** SSH: trusted, pending (changed), or new (trusted on first use). */
		hostKey?: 'trusted' | 'pending' | 'new';
		/** How reaching it went (live, else the last test); null: not known. */
		result?: { ok: boolean | null; ms?: number; error?: string } | null;
	}
	interface Props {
		hops: ChainHop[];
		/** The hop being edited. */
		selected?: number | null;
		/** Where the chain starts: an agent's name, or "Any agent". */
		from?: string;
		/** The target the last test tried, and how it went. */
		target?: { label: string; ok: boolean | null; ms?: number; error?: string } | null;
		/** Jobs are going through it now: the links show it. */
		flowing?: boolean;
		editable?: boolean;
		onselect?: (index: number) => void;
		/** Insert a new hop at this position. */
		oninsert?: (index: number) => void;
	}
	let { hops, selected = null, from = 'Any agent', target = null, flowing = false, editable = false, onselect, oninsert }: Props = $props();

	function linkTone(r: ChainHop['result'] | undefined): 'ok' | 'error' | 'idle' {
		if (!r || r.ok === null) return 'idle';
		return r.ok ? 'ok' : 'error';
	}
	const authIcon = (a: string) => (a === 'key' ? 'key' : a === 'password' || a === 'new' ? 'password' : 'lock_open');
	const authWord = (h: ChainHop) =>
		h.auth === 'key' ? 'key' : h.auth === 'password' ? 'password' : h.auth === 'new' ? 'not saved yet' : h.kind === 'ssh' ? 'no sign-in set' : 'no sign-in';
	// The link into hop i is broken when hop i (not a hop before it) is what failed.
	const brokenAt = $derived(hops.findIndex((h) => h.result?.ok === false));
</script>

{#snippet link(index: number, tone: 'ok' | 'error' | 'idle', ms?: number)}
	<li class="chain-link relative flex shrink-0 items-center justify-center max-md:h-10 max-md:w-full md:w-14" aria-hidden={!editable}>
		<span
			class="chain-line absolute max-md:top-0 max-md:bottom-0 max-md:left-[27px] max-md:w-0.5 md:left-0 md:right-0 md:h-0.5
				{tone === 'ok' ? 'bg-primary' : tone === 'error' ? 'chain-broken' : 'bg-outline-variant'} {flowing && tone !== 'error' ? 'chain-flow' : ''}"
		></span>
		{#if ms !== undefined && tone === 'ok'}
			<span class="relative z-[1] rounded-full bg-surface px-1.5 font-mono label-small text-primary max-md:ml-14 max-md:mr-auto md:-mt-7">{ms} ms</span>
		{:else if tone === 'error'}
			<span class="relative z-[1] flex size-6 items-center justify-center rounded-full bg-error-container text-on-error-container max-md:ml-[16px] max-md:mr-auto">
				<Icon name="close" size={16} />
			</span>
		{/if}
		{#if editable && oninsert}
			<button
				type="button"
				class="chain-insert state-layer focus-ring absolute z-[2] flex size-7 items-center justify-center rounded-full border border-outline-variant bg-surface text-on-surface-variant max-md:right-2 md:bottom-[-6px]"
				title="Add a hop here"
				aria-label={`Add a hop ${index === 0 ? 'first' : `after hop ${index}`}`}
				onclick={() => oninsert(index)}
			>
				<Icon name="add" size={18} />
			</button>
		{/if}
	</li>
{/snippet}

<ol class="flex items-stretch max-md:flex-col md:items-center md:overflow-x-auto md:pb-3 md:pt-6" aria-label="The route, from the agent to the targets">
	<li class="flex shrink-0 items-center gap-3 rounded-lg bg-surface-container-high px-3 py-2.5 md:min-w-24 md:flex-col md:gap-1 md:py-3">
		<span class="flex size-8 items-center justify-center rounded-full bg-secondary-container text-on-secondary-container"><Icon name="dns" size={18} /></span>
		<span class="min-w-0 md:text-center">
			<span class="block label-small text-on-surface-variant">From</span>
			<span class="block max-w-32 truncate label-large text-on-surface" title={from}>{from}</span>
		</span>
	</li>

	{#each hops as hop, i (hop.key)}
		{@render link(i, linkTone(hop.result), hop.result?.ms)}
		{@const k = hopKind(hop.kind)}
		<li class="shrink-0">
			<button
				type="button"
				class="state-layer focus-ring flex w-full flex-col gap-1.5 rounded-lg border bg-surface-container-low p-3 text-left md:w-48
					{selected === i ? 'border-primary ring-1 ring-primary' : brokenAt === i ? 'border-error' : 'border-outline-variant'}"
				aria-pressed={selected === i}
				aria-label={`Hop ${i + 1}: ${k.label} ${hopAddress(hop)}`}
				onclick={() => onselect?.(i)}
			>
				<span class="flex items-center gap-2">
					<span class="flex size-7 shrink-0 items-center justify-center rounded-full {brokenAt === i ? 'bg-error-container text-on-error-container' : 'bg-primary-container text-on-primary-container'}">
						<Icon name={k.icon} size={16} />
					</span>
					<span class="min-w-0 flex-1 truncate label-medium text-on-surface-variant">{hop.label || k.label}</span>
					<span class="label-small text-on-surface-variant">{i + 1}</span>
				</span>
				<span class="truncate font-mono title-small text-on-surface" title={hopAddress(hop)}>{hop.host ? hopAddress(hop) : 'no host yet'}</span>
				<span class="flex min-w-0 items-center gap-1.5 body-small text-on-surface-variant">
					<Icon name={authIcon(hop.auth)} size={14} />
					<span class="truncate">{hop.username ? `${hop.username} · ` : ''}{authWord(hop)}</span>
				</span>
				{#if hop.kind === 'ssh'}
					<span class="flex items-center gap-1.5 body-small {hop.hostKey === 'pending' ? 'text-error' : 'text-on-surface-variant'}">
						<Icon name={hop.hostKey === 'trusted' ? 'verified_user' : hop.hostKey === 'pending' ? 'gpp_maybe' : 'shield'} size={14} />
						{hop.hostKey === 'trusted' ? 'Host key trusted' : hop.hostKey === 'pending' ? 'Host key changed' : 'Key trusted on first use'}
					</span>
				{/if}
				{#if hop.result?.ok === false && hop.result.error}
					<span class="line-clamp-2 body-small text-error" title={hop.result.error}>{hop.result.error}</span>
				{:else if hop.result?.ok === null && hop.result.error}
					<span class="body-small text-on-surface-variant">{hop.result.error}</span>
				{/if}
			</button>
		</li>
	{/each}

	{@render link(hops.length, target ? (target.ok === null ? 'idle' : target.ok ? 'ok' : 'error') : 'idle', target?.ms)}
	<li class="flex shrink-0 items-center gap-3 rounded-lg bg-surface-container-high px-3 py-2.5 md:min-w-24 md:flex-col md:gap-1 md:py-3">
		<span class="flex size-8 items-center justify-center rounded-full bg-tertiary-container text-on-tertiary-container"><Icon name="radar" size={18} /></span>
		<span class="min-w-0 md:text-center">
			<span class="block label-small text-on-surface-variant">Then</span>
			<span class="block max-w-40 truncate label-large text-on-surface" title={target?.label ?? 'the targets'}>{target?.label ?? 'the targets'}</span>
			{#if target?.ok === false && target.error}<span class="block max-w-40 truncate body-small text-error" title={target.error}>{target.error}</span>{/if}
		</span>
	</li>
</ol>

<style>
	.chain-broken {
		background-image: repeating-linear-gradient(90deg, var(--md-sys-color-error) 0 6px, transparent 6px 11px);
	}
	@media (max-width: 767px) {
		.chain-broken {
			background-image: repeating-linear-gradient(180deg, var(--md-sys-color-error) 0 6px, transparent 6px 11px);
		}
	}
	.chain-flow {
		background-image: linear-gradient(90deg, var(--md-sys-color-primary) 0 40%, var(--md-sys-color-primary-fixed-dim, var(--md-sys-color-inverse-primary)) 50%, var(--md-sys-color-primary) 60% 100%);
		background-size: 200% 100%;
		animation: chain-flow 1.6s linear infinite;
	}
	@keyframes chain-flow {
		from { background-position: 100% 0; }
		to { background-position: -100% 0; }
	}
	@media (prefers-reduced-motion: reduce) {
		.chain-flow { animation: none; }
	}
	.chain-insert {
		opacity: 0;
		transition: opacity 120ms;
	}
	.chain-link:hover .chain-insert,
	.chain-insert:focus-visible {
		opacity: 1;
	}
	@media (hover: none) {
		.chain-insert { opacity: 1; }
	}
</style>
