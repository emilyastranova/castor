<script lang="ts">
	import { untrack } from 'svelte';
	import type { AssetEnd, AssetLink, UUID } from '$lib/api';
	import { Button, Icon, IconButton } from '$lib/m3';
	import { assetTypes } from '$lib/assetTypes.svelte';

	/** A project's assets as a graph: each asset with relationships is a node, each relationship an
	 *  arrow from its "from" end. Laid out by a small force simulation; drag the background to pan,
	 *  scroll to zoom, drag a node to move it, click one to see what it's connected to. */
	interface Props {
		links: AssetLink[];
		/** Select this asset when the graph appears (from an asset's "Show in graph"). */
		focus?: UUID | null;
		onopenasset: (id: UUID) => void;
	}
	let { links, focus = null, onopenasset }: Props = $props();

	interface Node extends AssetEnd {
		x: number;
		y: number;
		vx: number;
		vy: number;
		pinned: boolean;
		degree: number;
	}
	interface Edge {
		link: AssetLink;
		a: Node;
		b: Node;
	}

	const MAX_NODES = 600;
	/** Fill and icon colour per type, from the theme's roles. */
	const LOOK: Record<string, [string, string]> = {
		host: ['fill-primary', 'fill-on-primary'],
		service: ['fill-primary-container', 'fill-on-primary-container'],
		share: ['fill-secondary-container', 'fill-on-secondary-container'],
		url: ['fill-tertiary-container', 'fill-on-tertiary-container'],
		domain: ['fill-tertiary-container', 'fill-on-tertiary-container'],
		credential: ['fill-tertiary', 'fill-on-tertiary'],
		user: ['fill-secondary', 'fill-on-secondary'],
		file: ['fill-surface-container-highest', 'fill-on-surface'],
		vulnerability: ['fill-error-container', 'fill-on-error-container']
	};
	const look = (t: string) => LOOK[t] ?? ['fill-surface-container-highest', 'fill-on-surface'];

	let nodes = $state.raw<Node[]>([]);
	let edges = $state.raw<Edge[]>([]);
	let capped = $state(false);
	/** Bumped every simulation step: the template reads it so positions redraw. */
	let frame = $state(0);
	let selected = $state<UUID | null>(null);
	let hovered = $state<UUID | null>(null);

	// Build the graph, keeping positions of nodes that were already there.
	$effect(() => {
		const current = links;
		untrack(() => build(current));
	});
	function build(links: AssetLink[]) {
		const old = new Map(nodes.map((n) => [n.id, n]));
		const byId = new Map<UUID, Node>();
		const add = (e: AssetEnd, i: number) => {
			let n = byId.get(e.id);
			if (n) return n;
			if (byId.size >= MAX_NODES) return null;
			const prev = old.get(e.id);
			// A golden-angle spiral: a stable, even start.
			const r = 30 * Math.sqrt(i + 1);
			const t = i * 2.39996;
			n = prev ? { ...prev, ...e, degree: 0 } : { ...e, x: r * Math.cos(t), y: r * Math.sin(t), vx: 0, vy: 0, pinned: false, degree: 0 };
			byId.set(e.id, n);
			return n;
		};
		const list: Edge[] = [];
		links.forEach((l, i) => {
			const a = add(l.from, i * 2);
			const b = add(l.to, i * 2 + 1);
			if (!a || !b) return;
			a.degree++;
			b.degree++;
			list.push({ link: l, a, b });
		});
		capped = byId.size >= MAX_NODES;
		nodes = [...byId.values()];
		edges = list;
		if (selected && !byId.has(selected)) selected = null;
		heat(old.size ? 0.5 : 1);
	}

	// ---- the simulation ----
	let alpha = 1;
	let raf = 0;
	const reduced = typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches;
	function step() {
		const ns = nodes;
		const k = alpha;
		for (let i = 0; i < ns.length; i++) {
			const a = ns[i];
			for (let j = i + 1; j < ns.length; j++) {
				const b = ns[j];
				let dx = a.x - b.x;
				let dy = a.y - b.y;
				let d2 = dx * dx + dy * dy;
				if (d2 < 1) {
					dx = Math.random() - 0.5;
					dy = Math.random() - 0.5;
					d2 = 1;
				}
				if (d2 > 160000) continue; // far apart: no push
				const f = (1500 / d2) * k;
				a.vx += dx * f;
				a.vy += dy * f;
				b.vx -= dx * f;
				b.vy -= dy * f;
			}
		}
		for (const { a, b } of edges) {
			const dx = b.x - a.x;
			const dy = b.y - a.y;
			const d = Math.sqrt(dx * dx + dy * dy) || 1;
			const f = ((d - 80) / d) * 0.08 * k;
			a.vx += dx * f;
			a.vy += dy * f;
			b.vx -= dx * f;
			b.vy -= dy * f;
		}
		for (const n of ns) {
			n.vx -= n.x * 0.012 * k;
			n.vy -= n.y * 0.012 * k;
			if (n.pinned) {
				n.vx = n.vy = 0;
				continue;
			}
			n.vx *= 0.6;
			n.vy *= 0.6;
			n.x += Math.max(-40, Math.min(40, n.vx));
			n.y += Math.max(-40, Math.min(40, n.vy));
		}
		alpha *= 0.985;
	}
	function heat(to: number) {
		alpha = Math.max(alpha, to);
		if (reduced) {
			for (let i = 0; i < 300 && alpha > 0.02; i++) step();
			frame++;
			if (!touched) fit();
			return;
		}
		if (!raf) raf = requestAnimationFrame(run);
	}
	function run() {
		raf = 0;
		step();
		step();
		frame++;
		// Until someone pans or zooms, the view follows the layout as it settles.
		if (!touched) fit();
		if (alpha > 0.02) raf = requestAnimationFrame(run);
	}
	$effect(() => () => cancelAnimationFrame(raf));

	// ---- the view: pan and zoom ----
	let box = $state<SVGSVGElement | null>(null);
	let w = $state(800);
	let h = $state(520);
	let tx = $state(400);
	let ty = $state(260);
	let scale = $state(1);
	/** Someone panned or zoomed: stop fitting the view by itself. */
	let touched = false;
	function fit() {
		if (!nodes.length) return;
		let x0 = Infinity;
		let y0 = Infinity;
		let x1 = -Infinity;
		let y1 = -Infinity;
		for (const n of nodes) {
			x0 = Math.min(x0, n.x);
			y0 = Math.min(y0, n.y);
			x1 = Math.max(x1, n.x);
			y1 = Math.max(y1, n.y);
		}
		const pad = 60;
		scale = Math.max(0.15, Math.min(1.6, Math.min(w / (x1 - x0 + pad * 2), h / (y1 - y0 + pad * 2))));
		tx = w / 2 - ((x0 + x1) / 2) * scale;
		ty = h / 2 - ((y0 + y1) / 2) * scale;
	}
	function zoomAt(factor: number, cx = w / 2, cy = h / 2) {
		touched = true;
		const next = Math.max(0.1, Math.min(4, scale * factor));
		tx = cx - ((cx - tx) * next) / scale;
		ty = cy - ((cy - ty) * next) / scale;
		scale = next;
	}
	$effect(() => {
		void w;
		void h;
		if (!untrack(() => touched)) untrack(fit);
	});
	$effect(() => {
		const el = box;
		if (!el) return;
		const onWheel = (e: WheelEvent) => {
			e.preventDefault();
			const r = el.getBoundingClientRect();
			zoomAt(Math.exp(-e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
		};
		el.addEventListener('wheel', onWheel, { passive: false });
		return () => el.removeEventListener('wheel', onWheel);
	});

	let drag: { node: Node | null; x: number; y: number; moved: boolean; tx: number; ty: number } | null = null;
	let grabbing = $state(false);
	function down(e: PointerEvent, node: Node | null) {
		if (e.button !== 0) return;
		e.stopPropagation();
		(e.currentTarget as Element).setPointerCapture(e.pointerId);
		drag = { node, x: e.clientX, y: e.clientY, moved: false, tx, ty };
	}
	function move(e: PointerEvent) {
		if (!drag) return;
		const dx = e.clientX - drag.x;
		const dy = e.clientY - drag.y;
		if (!drag.moved && Math.hypot(dx, dy) < 4) return;
		drag.moved = true;
		grabbing = true;
		if (drag.node) {
			const r = box!.getBoundingClientRect();
			drag.node.x = (e.clientX - r.left - tx) / scale;
			drag.node.y = (e.clientY - r.top - ty) / scale;
			drag.node.pinned = true;
			heat(0.3);
			frame++;
		} else {
			touched = true;
			tx = drag.tx + dx;
			ty = drag.ty + dy;
		}
	}
	function up() {
		if (drag && !drag.moved) selected = drag.node ? (selected === drag.node.id ? null : drag.node.id) : null;
		if (drag?.node) drag.node.pinned = drag.moved || drag.node.pinned;
		drag = null;
		grabbing = false;
	}

	// ---- what's highlighted ----
	let near = $derived.by(() => {
		const id = selected ?? hovered;
		if (!id) return null;
		const s = new Set<UUID>([id]);
		for (const { a, b } of edges) {
			if (a.id === id) s.add(b.id);
			if (b.id === id) s.add(a.id);
		}
		return s;
	});
	let sel = $derived(nodes.find((n) => n.id === selected) ?? null);
	let selLinks = $derived(sel ? edges.filter((e) => e.a.id === sel.id || e.b.id === sel.id) : []);
	let showLabels = $derived(nodes.length <= 80);
	/** Text keeps about the same size on screen as you zoom out. */
	let textScale = $derived(1 / Math.min(1, Math.max(scale, 0.4)));
	const radius = (n: Node) => 13 + Math.min(9, n.degree * 1.3);
	const short = (s: string) => (s.length > 26 ? `${s.slice(0, 25)}…` : s);
	/** An arrow from a's edge to b's edge (not their centres). */
	function line(e: Edge) {
		void frame;
		const dx = e.b.x - e.a.x;
		const dy = e.b.y - e.a.y;
		const d = Math.hypot(dx, dy) || 1;
		const ra = radius(e.a) + 1;
		const rb = radius(e.b) + 5;
		return { x1: e.a.x + (dx / d) * ra, y1: e.a.y + (dy / d) * ra, x2: e.b.x - (dx / d) * rb, y2: e.b.y - (dy / d) * rb, mx: (e.a.x + e.b.x) / 2, my: (e.a.y + e.b.y) / 2 };
	}
	const lit = (e: Edge) => !!(selected ?? hovered) && (e.a.id === (selected ?? hovered) || e.b.id === (selected ?? hovered));

	$effect(() => {
		if (focus && nodes.some((n) => n.id === focus)) selected = focus;
	});
	function onkey(e: KeyboardEvent, n: Node) {
		if (e.key === 'Enter') onopenasset(n.id);
		else if (e.key === ' ') {
			e.preventDefault();
			selected = selected === n.id ? null : n.id;
		}
	}
	let legend = $derived([...new Set(nodes.map((n) => n.type))].sort((a, b) => assetTypes.list.findIndex((t) => t.name === a) - assetTypes.list.findIndex((t) => t.name === b)));
</script>

<div class="relative overflow-hidden rounded-lg border border-outline-variant bg-surface-container-lowest" bind:clientWidth={w} bind:clientHeight={h} style="height: min(70vh, 640px); min-height: 420px">
	<svg
		bind:this={box}
		width={w}
		height={h}
		class="block touch-none select-none {grabbing ? 'cursor-grabbing' : 'cursor-grab'}"
		role="img"
		aria-label="Graph of {nodes.length} assets and {edges.length} relationships"
		onpointerdown={(e) => down(e, null)}
		onpointermove={move}
		onpointerup={up}
		onpointercancel={up}
	>
		<defs>
			<marker id="ag-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
				<path d="M 0 0 L 10 5 L 0 10 z" class="fill-outline" />
			</marker>
			<marker id="ag-arrow-lit" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
				<path d="M 0 0 L 10 5 L 0 10 z" class="fill-primary" />
			</marker>
		</defs>
		<g transform="translate({tx} {ty}) scale({scale})">
			{#each edges as e (e.link.id)}
				{@const p = line(e)}
				{@const on = lit(e)}
				<line
					x1={p.x1}
					y1={p.y1}
					x2={p.x2}
					y2={p.y2}
					class={on ? 'stroke-primary' : 'stroke-outline-variant'}
					stroke-width={(on ? 2 : 1.25) / Math.max(scale, 0.6)}
					opacity={near && !on ? 0.25 : 1}
					marker-end="url(#{on ? 'ag-arrow-lit' : 'ag-arrow'})"
				/>
			{/each}
			{#each nodes as n (n.id)}
				{@const r = radius(n)}
				{@const [fill, ink] = look(n.type)}
				{@const dim = near && !near.has(n.id)}
				<g
					transform="translate({(void frame, n.x)} {n.y})"
					class="cursor-pointer outline-none"
					opacity={dim ? 0.3 : 1}
					role="button"
					tabindex="0"
					aria-label="{assetTypes.get(n.type)?.label ?? n.type} {n.label}, {n.degree} relationship{n.degree === 1 ? '' : 's'}"
					aria-pressed={selected === n.id}
					onpointerdown={(e) => down(e, n)}
					onpointerenter={() => (hovered = n.id)}
					onpointerleave={() => (hovered = hovered === n.id ? null : hovered)}
					ondblclick={() => onopenasset(n.id)}
					onkeydown={(e) => onkey(e, n)}
					onfocus={() => (hovered = n.id)}
					onblur={() => (hovered = hovered === n.id ? null : hovered)}
				>
					{#if selected === n.id}<circle {r} class="fill-none stroke-primary" stroke-width="3" transform="scale({(r + 4) / r})" />{/if}
					<circle {r} class="{fill} stroke-surface" stroke-width="1.5" />
					<text class="material-symbols-rounded pointer-events-none {ink}" font-size={r * 1.15} text-anchor="middle" dominant-baseline="central">{assetTypes.get(n.type)?.icon ?? 'radar'}</text>
					{#if showLabels || near?.has(n.id)}
						<text y={r + 4 + 10 * textScale} text-anchor="middle" class="pointer-events-none fill-on-surface stroke-surface-container-lowest" font-size={11 * textScale} stroke-width={3 * textScale} paint-order="stroke">{short(n.label)}</text>
					{/if}
				</g>
			{/each}
			{#each edges as e (e.link.id)}
				{#if lit(e)}
					{@const p = line(e)}
					<text x={p.mx} y={p.my} text-anchor="middle" dominant-baseline="central" class="pointer-events-none fill-primary stroke-surface-container-lowest font-medium" font-size={10.5 * textScale} stroke-width={3 * textScale} paint-order="stroke">{e.link.forward}</text>
				{/if}
			{/each}
		</g>
	</svg>

	<div class="pointer-events-none absolute inset-x-3 bottom-3 flex flex-wrap items-end justify-between gap-2">
		<ul class="pointer-events-auto flex flex-wrap gap-x-3 gap-y-1 rounded-md bg-surface-container/90 px-2.5 py-1.5 body-small text-on-surface-variant" aria-label="Legend">
			{#each legend as t (t)}
				<li class="flex items-center gap-1.5">
					<svg width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="5.5" class={look(t)[0]} /></svg>{assetTypes.get(t)?.plural ?? t}
				</li>
			{/each}
		</ul>
		<div class="pointer-events-auto flex gap-1 rounded-full bg-surface-container/90 p-0.5">
			<IconButton icon="add" label="Zoom in" size="sm" onclick={() => zoomAt(1.3)} />
			<IconButton icon="remove" label="Zoom out" size="sm" onclick={() => zoomAt(1 / 1.3)} />
			<IconButton icon="fit_screen" label="Fit everything in view" size="sm" onclick={() => { touched = false; fit(); }} />
			<IconButton icon="restart_alt" label="Lay it out again" size="sm" onclick={() => { for (const n of nodes) n.pinned = false; touched = false; heat(1); }} />
		</div>
	</div>

	{#if sel}
		<aside class="absolute top-3 left-3 flex max-h-[calc(100%-5rem)] w-72 max-w-[calc(100%-1.5rem)] flex-col gap-2 overflow-auto rounded-md border border-outline-variant bg-surface-container px-3 py-2.5 shadow-e2" aria-label="Selected asset">
			<div class="flex items-start gap-2">
				<Icon name={assetTypes.get(sel.type)?.icon ?? 'radar'} size={18} class="mt-0.5 text-primary" />
				<p class="min-w-0 flex-1 body-medium font-medium break-all text-on-surface">{sel.label}</p>
				<IconButton icon="close" label="Clear the selection" size="sm" onclick={() => (selected = null)} />
			</div>
			<ul class="flex flex-col gap-1 body-small">
				{#each selLinks.slice(0, 12) as e (e.link.id)}
					{@const out = e.a.id === sel.id}
					{@const other = out ? e.b : e.a}
					<li class="text-on-surface-variant">
						{out ? e.link.forward : e.link.reverse}
						<button type="button" class="text-left break-all text-primary hover:underline" onclick={() => (selected = other.id)}>{other.label}</button>
					</li>
				{/each}
				{#if selLinks.length > 12}<li class="text-on-surface-variant">and {selLinks.length - 12} more</li>{/if}
			</ul>
			<Button variant="tonal" icon="open_in_new" class="self-start" onclick={() => onopenasset(sel.id)}>Open</Button>
		</aside>
	{/if}
	{#if capped}
		<p class="absolute top-3 right-3 rounded-md bg-surface-container px-2.5 py-1 body-small text-on-surface-variant">Showing the first {MAX_NODES} assets; filter to narrow it down.</p>
	{/if}
</div>
