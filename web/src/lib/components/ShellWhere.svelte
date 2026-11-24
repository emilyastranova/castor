<script lang="ts">
	import { api } from '$lib/api';
	import type { RoutePlan, UUID } from '$lib/api';
	import { Button, Icon, Select, TextField } from '$lib/m3';
	import { SHELL_IMAGES, shellPreset } from '$lib/shells';

	/** Where a shell card opens: the agent's own shell, a ready image (Debian, Alpine) or another one.
	 * Checks the route the shell would go through: a strict route takes only containers, so a shell in
	 * the agent's own shell is refused up front, with the reason and the images to use instead. */
	interface Props {
		/** The image ("" = the agent's own shell). */
		image: string;
		projectId: UUID;
		/** An existing card (its route is the one it goes through); none for one not made yet. */
		taskId?: UUID | null;
		/** The card's route choice ("" = the project's default, "none" = direct). */
		routeId?: string;
		/** Re-check when this changes (the card was saved). */
		recheck?: unknown;
		/** Out: it can't open where it is now. */
		blocked?: boolean;
		onchange?: (image: string) => void;
		class?: string;
	}
	let { image = $bindable(''), projectId, taskId = null, routeId = '', recheck, blocked = $bindable(false), onchange, class: klass = '' }: Props = $props();

	const CUSTOM = '__custom';
	// "Another image…" stays chosen while its box is empty.
	let custom = $state(!!image.trim() && !shellPreset(image));
	let mode = $derived(!image.trim() ? (custom ? CUSTOM : '') : (shellPreset(image)?.id ?? CUSTOM));
	const options = [
		{ value: '', label: 'The agent’s own shell', description: 'On the agent’s machine' },
		...SHELL_IMAGES.map((p) => ({ value: p.id, label: p.label, description: `${p.image} · ${p.description}` })),
		{ value: CUSTOM, label: 'Another image…', description: 'Any image with /bin/sh' }
	];
	function pick(v: string) {
		custom = v === CUSTOM;
		const preset = SHELL_IMAGES.find((p) => p.id === v);
		const next = preset ? preset.image : v === CUSTOM ? (shellPreset(image) ? '' : image) : '';
		if (next !== image) {
			image = next;
			onchange?.(next);
		}
	}
	function use(preset: (typeof SHELL_IMAGES)[number]) {
		custom = false;
		image = preset.image;
		onchange?.(preset.image);
	}

	// How it would go through the card's (or the project's) route from the agent's own shell.
	let plan = $state<RoutePlan | null>(null);
	let seq = 0;
	$effect(() => {
		void recheck;
		const mine = ++seq;
		const route = routeId, task = taskId, project = projectId;
		if (route === 'none') {
			plan = null;
			return;
		}
		api()
			.routes.plan(task ? { task_id: task, route_id: route || null } : { project_id: project, route_id: route || null, shell: true })
			.then((p) => mine === seq && (plan = p))
			.catch(() => mine === seq && (plan = null));
	});
	let reason = $derived(!image.trim() && plan?.route && plan.host?.error ? plan.host.error : null);
	let canContainer = $derived(!!plan?.container?.ways?.length);
	$effect(() => {
		blocked = !!reason;
	});
</script>

<div class="flex flex-col gap-3 {klass}">
	<Select label="Opens in" value={mode} {options} onchange={pick} supporting={mode === '' ? 'No container: the agent needs “allow host” on' : ''} />
	{#if mode === CUSTOM}
		<TextField label="Image" bind:value={image} mono placeholder="docker.io/kalilinux/kali-rolling" supporting="Needs /bin/sh; bash is used where it has it" onblur={() => onchange?.(image.trim())} />
	{/if}
	{#if reason && plan?.route}
		<div class="flex flex-col gap-2 rounded-md bg-error-container px-3 py-2.5 text-on-error-container" role="alert">
			<p class="flex items-start gap-2 body-small">
				<Icon name="block" size={18} class="mt-px shrink-0" />
				<span>
					It can’t open in the agent’s own shell through “{plan.route.name}”: {reason}.
					{#if canContainer}Open it in a container instead, where everything it does goes through the route.{/if}
				</span>
			</p>
			{#if canContainer}
				<div class="flex flex-wrap gap-2 pl-6">
					{#each SHELL_IMAGES as p (p.id)}
						<Button variant="tonal" icon="deployed_code" onclick={() => use(p)}>Use {p.label}</Button>
					{/each}
				</div>
			{/if}
		</div>
	{/if}
</div>
