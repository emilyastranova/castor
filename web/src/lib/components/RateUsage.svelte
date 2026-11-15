<script lang="ts">
	import type { RateUsage } from '$lib/api';
	import { Icon } from '$lib/m3';
	import { absoluteTime, relativeTime } from '$lib/format';

	/** How much of a saved rate limit is in use right now (a variable set's, a plugin's):
	 *  room left, requests in flight, and a pause the API asked for. Refreshes while shown. */
	interface Props {
		load: () => Promise<RateUsage | null>;
	}
	let { load }: Props = $props();

	let usage = $state<RateUsage | null>(null);
	$effect(() => {
		let live = true;
		const tick = () => load().then((u) => live && (usage = u)).catch(() => {});
		void tick();
		const timer = setInterval(tick, 5000);
		return () => {
			live = false;
			clearInterval(timer);
		};
	});
	const PERIOD: Record<number, string> = { 1: 'second', 60: 'minute', 3600: 'hour', 86400: 'day' };
	let used = $derived(usage ? usage.count - usage.available : 0);
</script>

{#if usage}
	<div class="flex flex-col gap-1.5 rounded-md bg-surface-container px-3 py-2" aria-live="polite">
		<div class="flex flex-wrap items-center gap-x-3 gap-y-1 body-small text-on-surface-variant">
			<span class="label-medium text-on-surface">In use now</span>
			<span>{usage.available} of {usage.count} left this {PERIOD[usage.period_s] ?? `${usage.period_s} s`}</span>
			{#if usage.concurrent}<span>{usage.in_flight} of {usage.concurrent} at once</span>{:else if usage.in_flight}<span>{usage.in_flight} in flight</span>{/if}
			{#if !usage.available && usage.next_in_s}<span>next in {Math.ceil(usage.next_in_s)} s</span>{/if}
			{#if usage.paused_until}
				<span class="inline-flex items-center gap-1 text-tertiary" title={absoluteTime(usage.paused_until)}><Icon name="pause_circle" size={16} />The API asked to wait, until {relativeTime(usage.paused_until)}</span>
			{/if}
		</div>
		<span class="block h-1.5 overflow-hidden rounded-full bg-surface-container-highest" title="{used} of {usage.count} used">
			<span class="block h-full rounded-full transition-all {usage.available ? 'bg-primary' : 'bg-tertiary'}" style:width="{(100 * used) / usage.count}%"></span>
		</span>
	</div>
{/if}
