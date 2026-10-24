<script lang="ts">
	import { tick } from 'svelte';
	import type { PlaybookSpec, PlaybookStepSpec, PlaybookRun, Task, Job, CommandDefinition, OutputSpec } from '$lib/api';
	import { Icon, IconButton } from '$lib/m3';

	interface Props {
		spec: PlaybookSpec;
		run?: PlaybookRun | null;
		commands?: CommandDefinition[];
		selectedStepId?: string | null;
		onStepSelect?: (step: PlaybookStepSpec, task?: Task) => void;
	}

	let {
		spec,
		run = null,
		commands = [],
		selectedStepId = $bindable(null),
		onStepSelect
	}: Props = $props();

	const NODE_WIDTH = 260;
	const NODE_HEIGHT = 104;
	const HORIZONTAL_GAP = 180;
	const VERTICAL_GAP = 40;
	const PADDING_X = 60;
	const PADDING_Y = 60;

	export interface StepLayout {
		step: PlaybookStepSpec;
		x: number;
		y: number;
		width: number;
		height: number;
		layer: number;
		task?: Task;
		job?: Job | null;
		status: 'pending' | 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled';
		duration?: string;
		cmdOutputs?: OutputSpec[];
	}

	export interface EdgeBinding {
		sourceOutput: string;
		targetArg: string;
		parser: string | null;
		type: string | null;
	}

	export interface EdgeLayout {
		id: string;
		fromId: string;
		toId: string;
		d: string;
		midX: number;
		midY: number;
		status: 'active' | 'succeeded' | 'pending';
		bindings: EdgeBinding[];
		primaryParser: string | null;
	}

	// Canvas Viewport & Interaction State
	let containerEl = $state<HTMLDivElement | null>(null);
	let panX = $state(0);
	let panY = $state(0);
	let zoom = $state(1);
	let isPanning = $state(false);
	let isFullscreen = $state(false);
	let panStart = { x: 0, y: 0, initialPanX: 0, initialPanY: 0 };

	// Draggable Nodes State (custom overrides per step id)
	let customPositions = $state<Map<string, { x: number; y: number }>>(new Map());
	let draggingNodeId = $state<string | null>(null);
	let dragStart = { mouseX: 0, mouseY: 0, nodeX: 0, nodeY: 0, hasMoved: false };

	let patternId = $derived(`canvas-grid-${(spec.id || 'default').replace(/[^a-zA-Z0-9_-]/g, '')}`);

	let tasksByStepId = $derived.by(() => {
		const map = new Map<string, Task>();
		if (run?.tasks) {
			for (const t of run.tasks) {
				if (t.playbook_step_id) {
					map.set(t.playbook_step_id, t);
				}
			}
		}
		return map;
	});

	let commandsById = $derived.by(() => {
		const map = new Map<string, CommandDefinition>();
		for (const c of commands || []) {
			map.set(c.id, c);
		}
		return map;
	});

	function formatDuration(startStr?: string | null, finishStr?: string | null): string | undefined {
		if (!startStr) return undefined;
		const start = new Date(startStr).getTime();
		const end = finishStr ? new Date(finishStr).getTime() : Date.now();
		const sec = Math.max(0, Math.round((end - start) / 1000));
		if (sec < 60) return `${sec}s`;
		return `${Math.floor(sec / 60)}m ${sec % 60}s`;
	}

	let graph = $derived.by(() => {
		const steps = spec.steps || [];
		if (steps.length === 0) {
			return {
				nodes: [] as StepLayout[],
				edges: [] as EdgeLayout[],
				width: 600,
				height: 300,
				parallelLayers: new Set<number>()
			};
		}

		// 1. Compute layer for each step (topological ranking)
		const layers = new Map<string, number>();
		const stepsById = new Map(steps.map((s) => [s.id, s]));

		function getLayer(id: string, visited = new Set<string>()): number {
			if (layers.has(id)) return layers.get(id)!;
			if (visited.has(id)) return 0;
			visited.add(id);
			const step = stepsById.get(id);
			if (!step || !step.depends_on || step.depends_on.length === 0) {
				layers.set(id, 0);
				return 0;
			}
			let maxDep = -1;
			for (const depId of step.depends_on) {
				maxDep = Math.max(maxDep, getLayer(depId, new Set(visited)));
			}
			const layer = maxDep + 1;
			layers.set(id, layer);
			return layer;
		}

		for (const step of steps) {
			getLayer(step.id);
		}

		// 2. Group steps by layer
		const layerGroups = new Map<number, PlaybookStepSpec[]>();
		let maxLayer = 0;
		for (const step of steps) {
			const l = layers.get(step.id) ?? 0;
			if (l > maxLayer) maxLayer = l;
			if (!layerGroups.has(l)) layerGroups.set(l, []);
			layerGroups.get(l)!.push(step);
		}

		const parallelLayers = new Set<number>();
		let maxColSteps = 1;
		for (const [l, grp] of layerGroups.entries()) {
			if (grp.length > 1) parallelLayers.add(l);
			if (grp.length > maxColSteps) maxColSteps = grp.length;
		}

		const maxColHeight = maxColSteps * NODE_HEIGHT + (maxColSteps - 1) * VERTICAL_GAP;
		const totalWidth = PADDING_X * 2 + (maxLayer + 1) * NODE_WIDTH + maxLayer * HORIZONTAL_GAP;
		const totalHeight = Math.max(340, PADDING_Y * 2 + maxColHeight);

		// 3. Compute coordinates for each step node (allowing custom drag offsets)
		const nodes: StepLayout[] = [];
		const nodesById = new Map<string, StepLayout>();

		let boundMaxX = totalWidth;
		let boundMaxY = totalHeight;

		for (let l = 0; l <= maxLayer; l++) {
			const grp = layerGroups.get(l) || [];
			const colHeight = grp.length * NODE_HEIGHT + (grp.length - 1) * VERTICAL_GAP;
			const colOffsetY = (maxColHeight - colHeight) / 2;

			grp.forEach((step, idx) => {
				const defaultX = PADDING_X + l * (NODE_WIDTH + HORIZONTAL_GAP);
				const defaultY = PADDING_Y + colOffsetY + idx * (NODE_HEIGHT + VERTICAL_GAP);

				const custom = customPositions.get(step.id);
				const x = custom ? custom.x : defaultX;
				const y = custom ? custom.y : defaultY;

				boundMaxX = Math.max(boundMaxX, x + NODE_WIDTH + PADDING_X);
				boundMaxY = Math.max(boundMaxY, y + NODE_HEIGHT + PADDING_Y);

				const task = tasksByStepId.get(step.id);
				const job = task?.latest_job;
				let status: StepLayout['status'] = 'pending';

				if (job) {
					if (job.status === 'succeeded') status = 'succeeded';
					else if (job.status === 'failed' || job.status === 'lost') status = 'failed';
					else if (job.status === 'cancelled') status = 'cancelled';
					else if (job.status === 'running') status = 'running';
					else if (job.status === 'queued' || job.status === 'dispatched') status = 'queued';
				}

				const duration = job ? formatDuration(job.started_at, job.finished_at) : undefined;
				const cmd = commandsById.get(step.command_ref);

				const layout: StepLayout = {
					step,
					x,
					y,
					width: NODE_WIDTH,
					height: NODE_HEIGHT,
					layer: l,
					task,
					job,
					status,
					duration,
					cmdOutputs: cmd?.spec?.outputs || undefined
				};
				nodes.push(layout);
				nodesById.set(step.id, layout);
			});
		}

		// 4. Compute edges with parsed data bindings
		const edges: EdgeLayout[] = [];
		for (const node of nodes) {
			const deps = node.step.depends_on || [];
			for (const depId of deps) {
				const fromNode = nodesById.get(depId);
				if (!fromNode) continue;

				const x1 = fromNode.x + fromNode.width;
				const y1 = fromNode.y + fromNode.height / 2;
				const x2 = node.x;
				const y2 = node.y + node.height / 2;
				const dx = Math.max(40, Math.abs(x2 - x1) * 0.45);

				const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
				const midX = (x1 + x2) / 2;
				const midY = (y1 + y2) / 2;

				let edgeStatus: EdgeLayout['status'] = 'pending';
				if (fromNode.status === 'succeeded') {
					if (node.status === 'running' || node.status === 'queued') {
						edgeStatus = 'active';
					} else if (node.status === 'succeeded') {
						edgeStatus = 'succeeded';
					}
				}

				// Resolve data pipeline bindings
				const bindings: EdgeBinding[] = [];
				const fromCmd = commandsById.get(fromNode.step.command_ref);

				for (const [argName, argVal] of Object.entries(node.step.args || {})) {
					if (typeof argVal !== 'string') continue;
					const regex = new RegExp(`steps\\.${depId}\\.outputs\\.([a-zA-Z0-9_-]+)`, 'g');
					let match: RegExpExecArray | null;
					while ((match = regex.exec(argVal)) !== null) {
						const sourceOutput = match[1];
						const outSpec = fromCmd?.spec?.outputs?.find((o) => o.name === sourceOutput);
						bindings.push({
							sourceOutput,
							targetArg: argName,
							parser: outSpec?.parser || null,
							type: outSpec?.type || null
						});
					}
				}

				let primaryParser = bindings[0]?.parser || null;
				if (!primaryParser && fromCmd?.spec?.outputs && fromCmd.spec.outputs.length > 0) {
					primaryParser = fromCmd.spec.outputs[0].parser || null;
				}

				edges.push({
					id: `${depId}->${node.step.id}`,
					fromId: depId,
					toId: node.step.id,
					d,
					midX,
					midY,
					status: edgeStatus,
					bindings,
					primaryParser
				});
			}
		}

		return {
			nodes,
			edges,
			width: Math.max(boundMaxX, 760),
			height: Math.max(boundMaxY, 340),
			parallelLayers
		};
	});

	function handleNodeClick(node: StepLayout) {
		selectedStepId = node.step.id;
		onStepSelect?.(node.step, node.task);
	}

	// ---- Canvas Zoom & Pan Actions ----
	function zoomBy(factor: number) {
		if (!containerEl) return;
		const rect = containerEl.getBoundingClientRect();
		const mouseX = rect.width / 2;
		const mouseY = rect.height / 2;
		const nextZoom = Math.min(Math.max(zoom * factor, 0.25), 2.5);
		panX = mouseX - (mouseX - panX) * (nextZoom / zoom);
		panY = mouseY - (mouseY - panY) * (nextZoom / zoom);
		zoom = nextZoom;
	}

	function resetZoom() {
		if (!containerEl) return;
		const rect = containerEl.getBoundingClientRect();
		const mouseX = rect.width / 2;
		const mouseY = rect.height / 2;
		const nextZoom = 1;
		panX = mouseX - (mouseX - panX) * (nextZoom / zoom);
		panY = mouseY - (mouseY - panY) * (nextZoom / zoom);
		zoom = nextZoom;
	}

	export function fitView() {
		if (!containerEl || graph.nodes.length === 0) return;
		const rect = containerEl.getBoundingClientRect();
		if (rect.width <= 0 || rect.height <= 0) return;

		let minX = Infinity;
		let minY = Infinity;
		let maxX = -Infinity;
		let maxY = -Infinity;

		for (const n of graph.nodes) {
			minX = Math.min(minX, n.x);
			minY = Math.min(minY, n.y);
			maxX = Math.max(maxX, n.x + n.width);
			maxY = Math.max(maxY, n.y + n.height);
		}

		const pad = 60;
		const contentW = maxX - minX + pad * 2;
		const contentH = maxY - minY + pad * 2;

		const scaleX = rect.width / contentW;
		const scaleY = rect.height / contentH;
		const targetZoom = Math.min(Math.max(Math.min(scaleX, scaleY), 0.35), 1.15);

		const centerX = (minX + maxX) / 2;
		const centerY = (minY + maxY) / 2;

		panX = Math.round(rect.width / 2 - centerX * targetZoom);
		panY = Math.round(rect.height / 2 - centerY * targetZoom);
		zoom = targetZoom;
	}

	function resetLayout() {
		customPositions = new Map();
		setTimeout(() => fitView(), 40);
	}

	function toggleFullscreen() {
		isFullscreen = !isFullscreen;
		setTimeout(() => fitView(), 80);
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape' && isFullscreen) {
			isFullscreen = false;
			setTimeout(() => fitView(), 80);
		}
	}

	// Auto-fit on initial render or spec switch
	let prevSpecId = '';
	let hasInitializedFit = false;

	$effect(() => {
		const currentId = spec.id || '';
		if (currentId !== prevSpecId) {
			prevSpecId = currentId;
			customPositions = new Map();
			hasInitializedFit = false;
		}

		if (!hasInitializedFit && containerEl && graph.nodes.length > 0) {
			hasInitializedFit = true;
			setTimeout(() => fitView(), 60);
		}
	});

	// Non-passive wheel listener for smooth zooming
	$effect(() => {
		if (!containerEl) return;
		const el = containerEl;
		const onWheel = (e: WheelEvent) => {
			e.preventDefault();
			const rect = el.getBoundingClientRect();
			const mouseX = e.clientX - rect.left;
			const mouseY = e.clientY - rect.top;

			const zoomFactor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
			const nextZoom = Math.min(Math.max(zoom * zoomFactor, 0.25), 2.5);

			panX = mouseX - (mouseX - panX) * (nextZoom / zoom);
			panY = mouseY - (mouseY - panY) * (nextZoom / zoom);
			zoom = nextZoom;
		};
		el.addEventListener('wheel', onWheel, { passive: false });
		return () => el.removeEventListener('wheel', onWheel);
	});

	// Canvas Panning Event Handlers
	function handleCanvasPointerDown(ev: PointerEvent) {
		if (ev.button !== 0 && ev.button !== 1) return;
		isPanning = true;
		panStart = {
			x: ev.clientX,
			y: ev.clientY,
			initialPanX: panX,
			initialPanY: panY
		};
		try {
			(ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
		} catch {}
	}

	function handleCanvasPointerMove(ev: PointerEvent) {
		if (!isPanning) return;
		panX = panStart.initialPanX + (ev.clientX - panStart.x);
		panY = panStart.initialPanY + (ev.clientY - panStart.y);
	}

	function handleCanvasPointerUp(ev: PointerEvent) {
		if (isPanning) {
			isPanning = false;
			try {
				(ev.currentTarget as HTMLElement).releasePointerCapture(ev.pointerId);
			} catch {}
		}
	}

	// Node Dragging Event Handlers
	function handleNodePointerDown(ev: PointerEvent, node: StepLayout) {
		if (ev.button !== 0) return;
		ev.stopPropagation();
		try {
			(ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId);
		} catch {}

		draggingNodeId = node.step.id;
		dragStart = {
			mouseX: ev.clientX,
			mouseY: ev.clientY,
			nodeX: node.x,
			nodeY: node.y,
			hasMoved: false
		};
	}

	function handleNodePointerMove(ev: PointerEvent) {
		if (!draggingNodeId) return;
		ev.stopPropagation();
		const dx = (ev.clientX - dragStart.mouseX) / zoom;
		const dy = (ev.clientY - dragStart.mouseY) / zoom;

		if (Math.hypot(dx, dy) > 3 || dragStart.hasMoved) {
			dragStart.hasMoved = true;
			const updated = new Map(customPositions);
			updated.set(draggingNodeId, {
				x: Math.round(dragStart.nodeX + dx),
				y: Math.round(dragStart.nodeY + dy)
			});
			customPositions = updated;
		}
	}

	function handleNodePointerUp(ev: PointerEvent, node: StepLayout) {
		if (draggingNodeId === node.step.id) {
			ev.stopPropagation();
			try {
				(ev.currentTarget as HTMLElement).releasePointerCapture(ev.pointerId);
			} catch {}

			if (!dragStart.hasMoved) {
				handleNodeClick(node);
			}
			draggingNodeId = null;
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<div
	role="region"
	aria-label="Playbook execution graph canvas"
	class="relative w-full overflow-hidden border border-outline-variant bg-surface-container-lowest shadow-xs select-none transition-all duration-200
		{isPanning ? 'cursor-grabbing' : 'cursor-grab'}
		{isFullscreen ? 'fixed inset-0 z-50 h-screen w-screen rounded-none' : 'h-[520px] rounded-xl'}"
	bind:this={containerEl}
	onpointerdown={handleCanvasPointerDown}
	onpointermove={handleCanvasPointerMove}
	onpointerup={handleCanvasPointerUp}
	onpointercancel={handleCanvasPointerUp}
	ondblclick={() => fitView()}
>
	<!-- SVG Background Dynamic Dot Grid (pans and zooms with canvas) -->
	<svg class="pointer-events-none absolute inset-0 size-full">
		<defs>
			<pattern
				id={patternId}
				width={30 * zoom}
				height={30 * zoom}
				patternUnits="userSpaceOnUse"
				x={panX % (30 * zoom)}
				y={panY % (30 * zoom)}
			>
				<circle cx={1.5} cy={1.5} r={1.2} style="fill: var(--md-sys-color-outline-variant); opacity: 0.75;" />
			</pattern>
		</defs>
		<rect width="100%" height="100%" fill="url(#{patternId})" />
	</svg>

	<!-- Canvas Header / Legend (Floating top-left) -->
	<div class="pointer-events-none absolute top-4 left-4 z-20 flex flex-wrap items-center gap-3 rounded-xl border border-outline-variant/80 bg-surface-container/90 px-3 py-1.5 shadow-sm backdrop-blur-md text-xs text-on-surface-variant select-none">
		<div class="flex items-center gap-3">
			<span class="flex items-center gap-1.5 font-medium">
				<span class="size-2 rounded-full bg-primary ring-2 ring-primary/20"></span>
				Output Feed
			</span>
			<span class="flex items-center gap-1.5 font-medium">
				<span class="size-2 rounded-full border-2 border-outline-variant bg-surface"></span>
				Input Dependency
			</span>
			<span class="flex items-center gap-1.5 font-medium text-tertiary">
				<Icon name="filter_alt" size={13} class="text-tertiary" />
				Parser & Data Pipeline
			</span>
		</div>
		<span class="hidden sm:inline text-outline-variant">·</span>
		<div class="hidden sm:flex items-center gap-2 font-mono text-[11px]">
			<span>{graph.nodes.length} step{graph.nodes.length === 1 ? '' : 's'}</span>
			<span>·</span>
			<span>{graph.edges.length} edge{graph.edges.length === 1 ? '' : 's'}</span>
		</div>
	</div>

	<!-- Canvas Controls Toolbar (Floating bottom-right) -->
	<div class="pointer-events-none absolute bottom-4 right-4 z-20 flex items-center gap-1 rounded-xl border border-outline-variant/80 bg-surface-container/90 px-2 py-1.5 shadow-md backdrop-blur-md">
		<IconButton icon="remove" label="Zoom Out" size="sm" onclick={() => zoomBy(1 / 1.2)} />
		<button
			type="button"
			class="pointer-events-auto min-w-[50px] px-1 text-center font-mono text-xs font-semibold text-on-surface hover:text-primary transition-colors cursor-pointer select-none"
			title="Click to reset zoom to 100%"
			onclick={resetZoom}
		>
			{Math.round(zoom * 100)}%
		</button>
		<IconButton icon="add" label="Zoom In" size="sm" onclick={() => zoomBy(1.2)} />

		<span class="mx-1 h-4 w-px bg-outline-variant/70"></span>

		<IconButton icon="fit_screen" label="Fit View" size="sm" onclick={fitView} />
		<IconButton
			icon="auto_awesome"
			label="Reset Node Layout"
			size="sm"
			onclick={resetLayout}
		/>

		<span class="mx-1 h-4 w-px bg-outline-variant/70"></span>

		<IconButton
			icon={isFullscreen ? 'fullscreen_exit' : 'fullscreen'}
			label={isFullscreen ? 'Exit Fullscreen' : 'Fullscreen'}
			size="sm"
			onclick={toggleFullscreen}
		/>
	</div>

	<!-- Hint Overlay (Floating bottom-left) -->
	<div class="pointer-events-none absolute bottom-4 left-4 z-20 hidden md:flex items-center gap-2 rounded-lg bg-surface-container-high/70 px-2.5 py-1 text-[11px] text-on-surface-variant/90 backdrop-blur-xs select-none">
		<Icon name="pan_tool" size={13} class="opacity-70" />
		<span>Drag canvas to pan · Scroll to zoom · Drag cards to reposition</span>
	</div>

	{#if graph.nodes.length === 0}
		<div class="flex h-full flex-col items-center justify-center text-on-surface-variant">
			<Icon name="schema" size={44} class="opacity-40" />
			<p class="mt-2 title-small text-on-surface">No Steps in Playbook</p>
			<p class="body-small">Define steps in YAML to see the visual workflow DAG.</p>
		</div>
	{/if}

	<!-- Transformed Stage (Scale & Pan) -->
	<div
		class="absolute origin-top-left will-change-transform"
		style="transform: translate({panX}px, {panY}px) scale({zoom}); transform-origin: 0 0;"
	>
		<div
			class="relative select-none"
			style="width: {graph.width}px; height: {graph.height}px; min-width: {graph.width}px; min-height: {graph.height}px;"
		>
			<!-- SVG Edges Layer -->
			<svg
				class="pointer-events-none absolute inset-0 overflow-visible"
				style="width: {graph.width}px; height: {graph.height}px;"
			>
				<defs>
					<marker
						id="arrow-pending"
						viewBox="0 0 10 10"
						refX="8"
						refY="5"
						markerWidth="6"
						markerHeight="6"
						orient="auto-start-reverse"
					>
						<path d="M 0 1.5 L 8 5 L 0 8.5 z" style="fill: var(--md-sys-color-outline);" />
					</marker>
					<marker
						id="arrow-active"
						viewBox="0 0 10 10"
						refX="8"
						refY="5"
						markerWidth="6"
						markerHeight="6"
						orient="auto-start-reverse"
					>
						<path d="M 0 1.5 L 8 5 L 0 8.5 z" style="fill: var(--md-sys-color-primary);" />
					</marker>
					<marker
						id="arrow-succeeded"
						viewBox="0 0 10 10"
						refX="8"
						refY="5"
						markerWidth="6"
						markerHeight="6"
						orient="auto-start-reverse"
					>
						<path d="M 0 1.5 L 8 5 L 0 8.5 z" style="fill: var(--md-sys-color-primary);" />
					</marker>
				</defs>

				<!-- Edge Paths -->
				{#each graph.edges as edge (edge.id)}
					{#if edge.status === 'active'}
						<!-- Glowing animated path for active data flow -->
						<path
							d={edge.d}
							fill="none"
							style="stroke: var(--md-sys-color-primary); stroke-opacity: 0.25; stroke-width: 6px;"
						/>
						<path
							d={edge.d}
							fill="none"
							style="stroke: var(--md-sys-color-primary); stroke-width: 2.5px; stroke-dasharray: 6 4;"
							marker-end="url(#arrow-active)"
						>
							<animate
								attributeName="stroke-dashoffset"
								from="20"
								to="0"
								dur="1s"
								repeatCount="indefinite"
							/>
						</path>
					{:else if edge.status === 'succeeded'}
						<path
							d={edge.d}
							fill="none"
							style="stroke: var(--md-sys-color-primary); stroke-width: 2.2px;"
							marker-end="url(#arrow-succeeded)"
						/>
					{:else}
						<path
							d={edge.d}
							fill="none"
							style="stroke: var(--md-sys-color-outline); stroke-width: 1.8px; stroke-dasharray: 5 4;"
							marker-end="url(#arrow-pending)"
						/>
					{/if}
				{/each}
			</svg>

			<!-- Edge Relationship & Parser Badges (centered directly on the connecting line) -->
			{#each graph.edges as edge (edge.id + '-badge')}
				<div
					class="absolute -translate-x-1/2 -translate-y-1/2 pointer-events-auto flex flex-col items-center gap-0.5 rounded-lg border bg-surface-container-high px-2.5 py-1.5 shadow-sm transition-all duration-150 hover:scale-105 hover:shadow-md z-10 max-w-[170px]
						{edge.status === 'active'
							? 'border-primary ring-2 ring-primary/25'
							: edge.status === 'succeeded'
								? 'border-primary/60'
								: 'border-outline-variant/80'}"
					style="left: {edge.midX}px; top: {edge.midY}px;"
					title={edge.bindings.length > 0
						? `Pipeline: ${edge.bindings.map((b) => `${b.sourceOutput} (${b.parser || 'raw'}) → ${b.targetArg}`).join(', ')}`
						: `Dependency: ${edge.fromId} → ${edge.toId}`}
				>
					{#if edge.primaryParser}
						<div class="flex items-center gap-1 font-mono text-[11px] font-semibold text-primary truncate max-w-full">
							<Icon name="filter_alt" size={13} class="shrink-0 text-primary" />
							<span class="truncate">{edge.primaryParser}</span>
						</div>
					{/if}

					{#if edge.bindings.length > 0}
						<div class="flex flex-col items-center gap-0.5 w-full">
							{#each edge.bindings as b}
								<div class="flex items-center gap-1 font-mono text-[10px] text-on-surface-variant truncate max-w-full">
									<span class="font-medium text-on-surface">{b.sourceOutput}</span>
									<span>→</span>
									<span class="text-primary font-medium">{b.targetArg}</span>
									{#if b.type}
										<span class="rounded bg-surface-container-highest px-1 text-[8.5px] text-on-surface-variant/80">
											{b.type}
										</span>
									{/if}
								</div>
							{/each}
						</div>
					{:else}
						<div class="flex items-center gap-1 font-mono text-[9.5px] text-on-surface-variant">
							<Icon name="arrow_forward" size={11} class="opacity-70" />
							<span>depends_on</span>
						</div>
					{/if}
				</div>
			{/each}

			<!-- Nodes Layer (Draggable cards) -->
			{#each graph.nodes as node (node.step.id)}
				{@const isSelected = selectedStepId === node.step.id}
				{@const isDraggingThis = draggingNodeId === node.step.id}
				<div
					role="button"
					tabindex="0"
					class="group absolute flex flex-col justify-between rounded-xl border text-left transition-shadow duration-150 focus:outline-none select-none
						{isDraggingThis ? 'cursor-grabbing z-30 shadow-xl ring-2 ring-primary/40' : 'cursor-grab hover:shadow-md z-10'}
						{isSelected
							? 'border-primary ring-3 ring-primary/25 shadow-md bg-surface-container-high'
							: 'border-outline-variant bg-surface-container-low hover:border-outline hover:bg-surface-container'}"
					style="left: {node.x}px; top: {node.y}px; width: {node.width}px; height: {node.height}px;"
					onpointerdown={(e) => handleNodePointerDown(e, node)}
					onpointermove={handleNodePointerMove}
					onpointerup={(e) => handleNodePointerUp(e, node)}
					onpointercancel={(e) => handleNodePointerUp(e, node)}
					onkeydown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); handleNodeClick(node); } }}
				>
					<!-- Card Top: Step Name & Command Ref -->
					<div class="p-3 pb-1">
						<div class="flex items-center justify-between gap-2">
							<span class="truncate font-medium title-small text-on-surface" title={node.step.name}>
								{node.step.name}
							</span>
							{#if node.duration}
								<span class="shrink-0 font-mono text-[11px] text-on-surface-variant">
									{node.duration}
								</span>
							{/if}
						</div>

						<div class="mt-0.5 flex items-center justify-between gap-1.5 font-mono text-[11px] text-on-surface-variant">
							<div class="flex items-center gap-1.5 truncate">
								<Icon name="terminal" size={13} class="opacity-70 shrink-0" />
								<span class="truncate">{node.step.command_ref}</span>
							</div>

							{#if node.cmdOutputs && node.cmdOutputs.length > 0}
								{@const primary = node.cmdOutputs[0]}
								<span
									class="shrink-0 rounded bg-tertiary-container/30 px-1.5 py-0.2 text-[9.5px] text-on-tertiary-container font-mono"
									title="Parser: {primary.parser || 'none'} ({primary.name}: {primary.type || 'text'})"
								>
									{primary.parser || primary.name}
								</span>
							{/if}
						</div>
					</div>

					<!-- Card Bottom: Status Bar & Port Indicators -->
					<div class="flex items-center justify-between border-t border-outline-variant/50 px-3 py-1.5 text-[11.5px]">
						<!-- Left: Status pill -->
						{#if node.status === 'running'}
							<span class="flex items-center gap-1.5 font-medium text-primary">
								<span class="size-2 animate-ping rounded-full bg-primary"></span>
								Running...
							</span>
						{:else if node.status === 'succeeded'}
							<span class="flex items-center gap-1 font-medium text-primary">
								<Icon name="check_circle" size={14} class="text-primary" />
								Done
							</span>
						{:else if node.status === 'failed'}
							<span class="flex items-center gap-1 font-medium text-error">
								<Icon name="error" size={14} class="text-error" />
								Failed
							</span>
						{:else if node.status === 'queued'}
							<span class="flex items-center gap-1 font-medium text-tertiary">
								<Icon name="hourglass_empty" size={14} class="text-tertiary" />
								Queued
							</span>
						{:else if node.status === 'cancelled'}
							<span class="flex items-center gap-1 text-on-surface-variant">
								<Icon name="cancel" size={14} />
								Cancelled
							</span>
						{:else}
							<span class="flex items-center gap-1 text-on-surface-variant/80">
								<Icon name="schedule" size={14} />
								Waiting
							</span>
						{/if}

						<!-- Right: Dependencies count badge -->
						<span class="text-[11px] text-on-surface-variant">
							{#if !node.step.depends_on || node.step.depends_on.length === 0}
								<span class="rounded bg-surface-container-highest px-1.5 py-0.5">Initial</span>
							{:else}
								<span>{node.step.depends_on.length} dep{node.step.depends_on.length > 1 ? 's' : ''}</span>
							{/if}
						</span>
					</div>

					<!-- Connection Port Dots -->
					{#if node.step.depends_on && node.step.depends_on.length > 0}
						<span
							class="absolute -left-1.5 top-1/2 size-3 -translate-y-1/2 rounded-full border-2 border-surface bg-outline-variant"
							title="Input dependency: {node.step.depends_on.join(', ')}"
						></span>
					{/if}
					<span
						class="absolute -right-1.5 top-1/2 size-3 -translate-y-1/2 rounded-full border-2 border-surface bg-primary"
						title="Output feed"
					></span>
				</div>
			{/each}
		</div>
	</div>
</div>
