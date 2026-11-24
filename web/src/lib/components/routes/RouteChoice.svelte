<script lang="ts">
	import { api } from '$lib/api';
	import type { NetworkRoute, RoutePlan, UUID } from '$lib/api';
	import { Icon, Select } from '$lib/m3';
	import { describePlan, routeOptions } from '$lib/routes';

	/** Picks a network route: for a card ("" = the project's default, "none" = direct), a playbook run,
	 * or a project's default. With `taskId`, says how the card's runs would go through it. */
	interface Props {
		projectId: UUID | null;
		value: string;
		label?: string;
		/** Offer "the project's default" (cards and runs). */
		inherit?: boolean;
		taskId?: UUID | null;
		/** Changes that should re-check how it goes (the command, its tool…). */
		recheck?: unknown;
		disabled?: boolean;
		class?: string;
		onchange?: (value: string) => void;
		/** Out: the route it comes to (its own, or the project's default), null when direct. */
		route?: NetworkRoute | null;
		/** Say nothing when it can't go through (something next to it explains, as a shell's picker does). */
		quietRefusal?: boolean;
	}
	let { projectId, value = $bindable(''), label = 'Network route', inherit = true, taskId = null, recheck, disabled = false, class: klass = '', onchange, route = $bindable(null), quietRefusal = false }: Props = $props();

	let routes = $state<NetworkRoute[]>([]);
	let projectDefault = $state<string | null>(null);
	let plan = $state<RoutePlan | null>(null);

	$effect(() => {
		const pid = projectId;
		api().routes.list(pid ?? undefined).then((r) => (routes = r)).catch(() => (routes = []));
		if (pid && inherit) api().projects.get(pid).then((p) => (projectDefault = ((p.settings ?? {}) as Record<string, unknown>).route as string ?? null)).catch(() => {});
	});
	const defaultName = $derived(projectDefault ? (routes.find((r) => r.id === projectDefault)?.name ?? 'a route') : null);
	const options = $derived(routeOptions(routes, inherit ? { inherit: defaultName } : { direct: true }));

	$effect(() => {
		void recheck;
		const v = value, t = taskId;
		if (!t || v === 'none' || (!v && !projectDefault)) {
			plan = null;
			return;
		}
		api().routes.plan({ task_id: t, route_id: v || null }).then((p) => (plan = p)).catch(() => (plan = null));
	});
	const said = $derived(describePlan(plan));
	const chosen = $derived(value === 'none' ? undefined : routes.find((r) => r.id === (value || projectDefault)));
	$effect(() => {
		route = chosen ?? null;
	});
</script>

<div class="flex flex-col gap-1 {klass}">
	<Select {label} bind:value {options} {disabled} onchange={(v) => onchange?.(v)}
		supporting={chosen ? chosen.label : value === 'none' ? 'From the agent’s own network' : 'From the agent’s own network (no route)'} />
	{#if said && (said.ok || !quietRefusal)}
		<p class="flex items-start gap-1.5 px-1 body-small {said.ok ? 'text-on-surface-variant' : 'text-error'}" role={said.ok ? undefined : 'alert'}>
			<Icon name={said.ok ? 'alt_route' : 'block'} size={16} class="mt-px shrink-0 {said.ok ? 'text-primary' : ''}" />
			<span>{said.text}{!said.ok ? '. It won’t run until that changes.' : ''}</span>
		</p>
	{/if}
</div>
