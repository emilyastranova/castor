<script lang="ts">
	import type {
		PlaybookSpec,
		PlaybookStepSpec,
		PlaybookStepStatus,
		PlaybookRun,
		Task,
		Job,
		CommandDefinition,
		OutputSpec
	} from '$lib/api';
	import { Icon, IconButton } from '$lib/m3';
	import { BLOCK_MIME, SATISFIED, STEP_STATUS, conditionSteps, describeCondition, describeOp, stepBindings, stepFlags, stepRefs } from '$lib/playbook';

	interface Props {
		spec: PlaybookSpec;
		run?: PlaybookRun | null;
		commands?: CommandDefinition[];
		selectedStepId?: string | null;
		onStepSelect?: (step: PlaybookStepSpec, task?: Task) => void;
		/** Editing (the visual editor): connect, delete edges, drop blocks, save positions. */
		editable?: boolean;
		/** Fill the parent's height instead of the fixed canvas height. */
		fill?: boolean;
		/** Problems per step id, shown on the nodes. */
		errors?: Record<string, string[]>;
		/** A drag from `fromId`'s output port was dropped on `toId` (client coordinates). */
		onconnect?: (fromId: string, toId: string, at: { x: number; y: number }) => void;
		onedgedelete?: (fromId: string, toId: string) => void;
		/** Every node's position after one was moved (or {} after resetting the layout). */
		onlayout?: (positions: Record<string, { x: number; y: number }>) => void;
		/** A palette block was dropped on the canvas, at canvas coordinates. */
		ondropblock?: (data: string, at: { x: number; y: number }) => void;
		oncanvasclick?: () => void;
	}

	let {
		spec,
		run = null,
		commands = [],
		selectedStepId = $bindable(null),
		onStepSelect,
		editable = false,
		fill = false,
		errors = {},
		onconnect,
		onedgedelete,
		onlayout,
		ondropblock,
		oncanvasclick
	}: Props = $props();

	// Connecting: a line follows the pointer from an output port until it's dropped on a node.
	let connecting = $state<{ fromId: string; x1: number; y1: number; x2: number; y2: number } | null>(null);

	// Column width fits the wider command card; transform cards are narrower and centred.
	const NODE_WIDTH = 260;
	const NODE_HEIGHT = 104;
	const TRANSFORM_WIDTH = 216;
	const TRANSFORM_HEIGHT = 108;
	const MAX_OP_LINES = 3;
	const HORIZONTAL_GAP = 150;
	const VERTICAL_GAP = 36;
	const PADDING_X = 60;
	const PADDING_Y = 60;

	interface StepLayout {
		step: PlaybookStepSpec;
		x: number;
		y: number;
		width: number;
		height: number;
		layer: number;
		task?: Task;
		job?: Job | null;
		status: PlaybookStepStatus;
		duration?: string;
		cmdOutputs?: OutputSpec[];
		/** Transform results: items in → items out. */
		counts?: { input: number; output: number };
		/** for_each steps: items finished / total. */
		fan?: { done: number; failed: number; total: number };
		note?: string;
	}

	interface EdgeBinding {
		sourceOutput: string;
		targetArg: string;
		parser: string | null;
		type: string | null;
	}

	interface EdgeLayout {
		id: string;
		fromId: string;
		toId: string;
		d: string;
		midX: number;
		midY: number;
		status: 'active' | 'succeeded' | 'pending' | 'blocked' | 'not_taken';
		bindings: EdgeBinding[];
		/** The target's `when`, on edges from the steps it looks at. */
		gate: string | null;
		/** Parser that produced the value on this edge, or "transform". */
		via: string | null;
		/** Items carried, when known (transform outputs). */
		count: number | null;
	}

	// Canvas Viewport & Interaction State
	let containerEl = $state<HTMLDivElement | null>(null);
	let panX = $state(0);
	let panY = $state(0);
	let zoom = $state(1);
	let isPanning = $state(false);
	let isFullscreen = $state(false);
	let panStart = { x: 0, y: 0, initialPanX: 0, initialPanY: 0 };
	let panMoved = false;

	// Draggable Nodes State (custom overrides per step id)
	let customPositions = $state<Map<string, { x: number; y: number }>>(new Map());
	let draggingNodeId = $state<string | null>(null);
	let dragStart = { mouseX: 0, mouseY: 0, nodeX: 0, nodeY: 0, hasMoved: false };

	let patternId = $derived(`canvas-grid-${(spec.id || 'default').replace(/[^a-zA-Z0-9_-]/g, '')}`);

	let tasksByStepId = $derived.by(() => {
		const map = new Map<string, Task>();
		for (const t of run?.tasks ?? []) {
			if (t.playbook_step_id && t.playbook_item == null) map.set(t.playbook_step_id, t);
		}
		return map;
	});

	/** for_each steps: their item cards, in item order. */
	let itemsByStepId = $derived.by(() => {
		const map = new Map<string, Task[]>();
		for (const t of run?.tasks ?? []) {
			if (t.playbook_step_id && t.playbook_item != null) map.set(t.playbook_step_id, [...(map.get(t.playbook_step_id) ?? []), t]);
		}
		for (const list of map.values()) list.sort((a, b) => (a.playbook_item ?? 0) - (b.playbook_item ?? 0));
		return map;
	});

	let commandsById = $derived(new Map((commands || []).map((c) => [c.id, c])));

	// Re-render running durations every second.
	let now = $state(Date.now());
	$effect(() => {
		if (run?.status !== 'running') return;
		const timer = setInterval(() => (now = Date.now()), 1000);
		return () => clearInterval(timer);
	});

	function formatDuration(startStr?: string | null, finishStr?: string | null): string | undefined {
		if (!startStr) return undefined;
		const start = new Date(startStr).getTime();
		const end = finishStr ? new Date(finishStr).getTime() : now;
		const sec = Math.max(0, Math.round((end - start) / 1000));
		if (sec < 60) return `${sec}s`;
		return `${Math.floor(sec / 60)}m ${sec % 60}s`;
	}

	// Transforms grow a row for their flags; command cards always have one.
	const sizeOf = (step: PlaybookStepSpec) =>
		step.kind === 'transform'
			? { width: TRANSFORM_WIDTH, height: TRANSFORM_HEIGHT + (stepFlags(step).length ? 20 : 0) }
			: { width: NODE_WIDTH, height: NODE_HEIGHT };

	let graph = $derived.by(() => {
		const steps = spec.steps || [];
		if (steps.length === 0) {
			return { nodes: [] as StepLayout[], edges: [] as EdgeLayout[], width: 600, height: 300 };
		}
		const stepsById = new Map(steps.map((s) => [s.id, s]));

		// 1. Layer = longest path from a root.
		const layers = new Map<string, number>();
		function getLayer(id: string, visiting = new Set<string>()): number {
			if (layers.has(id)) return layers.get(id)!;
			if (visiting.has(id)) return 0;
			visiting.add(id);
			const deps = (stepsById.get(id)?.depends_on ?? []).filter((d) => stepsById.has(d));
			const layer = deps.length ? Math.max(...deps.map((d) => getLayer(d, visiting))) + 1 : 0;
			layers.set(id, layer);
			return layer;
		}
		for (const step of steps) getLayer(step.id);

		// 2. Group by layer; order each layer by the mean slot of its dependencies so edges
		// cross less (one barycentre pass is plenty for playbook-sized graphs).
		const maxLayer = Math.max(...layers.values());
		const groups: PlaybookStepSpec[][] = Array.from({ length: maxLayer + 1 }, () => []);
		for (const step of steps) groups[layers.get(step.id) ?? 0].push(step);
		const slot = new Map<string, number>();
		groups.forEach((grp, l) => {
			if (l > 0) {
				const rank = (s: PlaybookStepSpec) => {
					const deps = s.depends_on.filter((d) => slot.has(d));
					return deps.length ? deps.reduce((sum, d) => sum + slot.get(d)!, 0) / deps.length : Infinity;
				};
				grp.sort((a, b) => rank(a) - rank(b));
			}
			grp.forEach((s, i) => slot.set(s.id, i));
		});

		const colHeight = (grp: PlaybookStepSpec[]) =>
			grp.reduce((h, s) => h + sizeOf(s).height, 0) + Math.max(0, grp.length - 1) * VERTICAL_GAP;
		const maxColHeight = Math.max(...groups.map(colHeight));

		// 3. Place nodes (honouring dragged positions) and attach run state.
		const nodes: StepLayout[] = [];
		const nodesById = new Map<string, StepLayout>();
		let boundMaxX = 0;
		let boundMaxY = 0;

		groups.forEach((grp, l) => {
			let cursorY = PADDING_Y + (maxColHeight - colHeight(grp)) / 2;
			for (const step of grp) {
				const { width, height } = sizeOf(step);
				const defaultX = PADDING_X + l * (NODE_WIDTH + HORIZONTAL_GAP) + (NODE_WIDTH - width) / 2;
				const defaultY = cursorY;
				cursorY += height + VERTICAL_GAP;
				const custom = customPositions.get(step.id) ?? spec.layout?.[step.id];
				const x = custom ? custom.x : defaultX;
				const y = custom ? custom.y : defaultY;
				boundMaxX = Math.max(boundMaxX, x + width + PADDING_X);
				boundMaxY = Math.max(boundMaxY, y + height + PADDING_Y);

				const task = tasksByStepId.get(step.id);
				const job = task?.latest_job;
				const result = run?.step_results?.[step.id];
				const status: PlaybookStepStatus = run?.step_status?.[step.id] ?? 'pending';
				const layout: StepLayout = {
					step,
					x,
					y,
					width,
					height,
					layer: l,
					task,
					job,
					status,
					duration: job ? formatDuration(job.started_at, job.finished_at) : undefined,
					cmdOutputs: step.command_ref ? commandsById.get(step.command_ref)?.spec?.outputs : undefined,
					counts:
						result?.count !== undefined
							? { input: result.input_count ?? 0, output: result.count }
							: undefined,
					note: result?.error || result?.reason,
					fan: (() => {
						const list = itemsByStepId.get(step.id);
						if (!list?.length) return undefined;
						const states = list.map((t) => t.latest_job?.status);
						return {
							done: states.filter((x) => x === 'succeeded').length,
							failed: states.filter((x) => x === 'failed' || x === 'lost' || x === 'cancelled').length,
							total: list.length
						};
					})()
				};
				nodes.push(layout);
				nodesById.set(step.id, layout);
			}
		});

		// 4. Edges, labelled with what flows along them.
		const edges: EdgeLayout[] = [];
		for (const node of nodes) {
			for (const depId of node.step.depends_on || []) {
				const fromNode = nodesById.get(depId);
				if (!fromNode) continue;

				const x1 = fromNode.x + fromNode.width;
				const y1 = fromNode.y + fromNode.height / 2;
				const x2 = node.x;
				const y2 = node.y + node.height / 2;
				const dx = Math.max(40, Math.abs(x2 - x1) * 0.45);
				const d = `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}`;
				// Badge in the gap just before the target, so edges that skip layers don't put
				// their label on top of a node in between.
				const point = (t: number) => {
					const u = 1 - t;
					return {
						x: u * u * u * x1 + 3 * u * u * t * (x1 + dx) + 3 * u * t * t * (x2 - dx) + t * t * t * x2,
						y: u * u * u * y1 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y2
					};
				};
				const wantX = Math.max((x1 + x2) / 2, x2 - HORIZONTAL_GAP / 2);
				let mid = point(0.5);
				for (let i = 1; i < 40; i++) {
					const p = point(i / 40);
					if (Math.abs(p.x - wantX) < Math.abs(mid.x - wantX)) mid = p;
				}

				// A condition that looks at this step (its outputs or how it ended) gates the edge.
				const gated = conditionSteps(node.step.when).has(depId);
				const watched = gated && !stepRefs(JSON.stringify(node.step.when)).some((r) => r.step === depId);
				let edgeStatus: EdgeLayout['status'] = 'pending';
				if (node.status === 'not_taken' || fromNode.status === 'not_taken') edgeStatus = 'not_taken';
				else if ((fromNode.status === 'failed' && !watched && node.step.on_failure !== 'continue') || fromNode.status === 'blocked')
					edgeStatus = node.status === 'pending' || node.status === 'blocked' ? 'blocked' : 'succeeded';
				else if (SATISFIED.has(fromNode.status) || fromNode.status === 'failed') {
					if (node.status === 'running' || node.status === 'queued') edgeStatus = 'active';
					else if (node.status !== 'pending') edgeStatus = 'succeeded';
				}

				const fromStep = fromNode.step;
				const fromCmd = fromStep.command_ref ? commandsById.get(fromStep.command_ref) : undefined;
				const fromResult = run?.step_results?.[depId];
				const bindings: EdgeBinding[] = [];
				for (const [argName, argVal] of stepBindings(node.step)) {
					for (const ref of stepRefs(argVal)) {
						if (ref.step !== depId) continue;
						const outSpec = fromCmd?.spec?.outputs?.find((o) => o.name === ref.output);
						bindings.push({
							sourceOutput: ref.output,
							targetArg: argName,
							parser: fromStep.transform ? null : (outSpec?.parser ?? null),
							type: fromStep.transform
								? (fromResult?.type ?? fromStep.transform.output.type ?? null)
								: (outSpec?.type ?? null)
						});
					}
				}

				edges.push({
					id: `${depId}->${node.step.id}`,
					fromId: depId,
					toId: node.step.id,
					d,
					midX: mid.x,
					midY: mid.y,
					status: edgeStatus,
					bindings,
					gate: gated ? describeCondition(node.step.when) : null,
					via: fromStep.transform ? null : (bindings[0]?.parser ?? fromCmd?.spec?.outputs?.[0]?.parser ?? null),
					count: fromStep.transform && fromResult?.count !== undefined ? fromResult.count : null
				});
			}
		}

		return {
			nodes,
			edges,
			width: Math.max(boundMaxX, 760),
			height: Math.max(boundMaxY, 340)
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
		onlayout?.({});
		setTimeout(() => fitView(), 40);
	}

	/** Canvas coordinates of a point on the screen. */
	function toCanvas(clientX: number, clientY: number) {
		const rect = containerEl?.getBoundingClientRect();
		return { x: (clientX - (rect?.left ?? 0) - panX) / zoom, y: (clientY - (rect?.top ?? 0) - panY) / zoom };
	}

	/** Where every node is drawn now, so adding a step doesn't reshuffle the others. */
	export function positions(): Record<string, { x: number; y: number }> {
		return Object.fromEntries(graph.nodes.map((n) => [n.step.id, { x: Math.round(n.x), y: Math.round(n.y) }]));
	}

	/** Canvas coordinates of the middle of the visible area (where new blocks go). */
	export function visibleCenter() {
		const rect = containerEl?.getBoundingClientRect();
		return rect ? toCanvas(rect.left + rect.width / 2, rect.top + rect.height / 2) : { x: 80, y: 80 };
	}

	function startConnect(ev: PointerEvent, node: StepLayout) {
		if (!editable || ev.button !== 0) return;
		ev.stopPropagation();
		ev.preventDefault();
		const x1 = node.x + node.width;
		const y1 = node.y + node.height / 2;
		connecting = { fromId: node.step.id, x1, y1, x2: x1, y2: y1 };
		const move = (e: PointerEvent) => {
			if (!connecting) return;
			const p = toCanvas(e.clientX, e.clientY);
			connecting = { ...connecting, x2: p.x, y2: p.y };
		};
		const up = (e: PointerEvent) => {
			window.removeEventListener('pointermove', move);
			window.removeEventListener('pointerup', up);
			const from = connecting?.fromId;
			connecting = null;
			const target = document.elementFromPoint(e.clientX, e.clientY)?.closest<HTMLElement>('[data-step-id]');
			const toId = target?.dataset.stepId;
			if (from && toId && toId !== from) onconnect?.(from, toId, { x: e.clientX, y: e.clientY });
		};
		window.addEventListener('pointermove', move);
		window.addEventListener('pointerup', up);
	}

	function onDragOver(e: DragEvent) {
		if (editable && e.dataTransfer?.types.includes(BLOCK_MIME)) {
			e.preventDefault();
			e.dataTransfer.dropEffect = 'copy';
		}
	}

	function onDrop(e: DragEvent) {
		const data = e.dataTransfer?.getData(BLOCK_MIME);
		if (!editable || !data) return;
		e.preventDefault();
		ondropblock?.(data, toCanvas(e.clientX, e.clientY));
	}

	function toggleFullscreen() {
		isFullscreen = !isFullscreen;
		setTimeout(() => fitView(), 60);
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
		// Capturing the pointer retargets the click, so toolbar buttons would never fire.
		if ((ev.target as HTMLElement).closest('button, a, input')) return;
		isPanning = true;
		panMoved = false;
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
		if (Math.hypot(ev.clientX - panStart.x, ev.clientY - panStart.y) > 3) panMoved = true;
		panX = panStart.initialPanX + (ev.clientX - panStart.x);
		panY = panStart.initialPanY + (ev.clientY - panStart.y);
	}

	function handleCanvasPointerUp(ev: PointerEvent) {
		if (isPanning) {
			isPanning = false;
			if (!panMoved) oncanvasclick?.();
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
			} else if (onlayout) {
				onlayout(Object.fromEntries(graph.nodes.map((n) => [n.step.id, { x: Math.round(n.x), y: Math.round(n.y) }])));
			}
			draggingNodeId = null;
		}
	}
</script>

<svelte:window onkeydown={handleKeydown} />

<div
	role="region"
	aria-label="Playbook execution graph canvas"
	class="w-full overflow-hidden border border-outline-variant bg-surface-container-lowest shadow-xs select-none
		{isPanning ? 'cursor-grabbing' : 'cursor-grab'}
		{isFullscreen ? 'fixed inset-0 z-50 h-screen w-screen rounded-none' : fill ? 'relative h-full rounded-xl' : 'relative h-[520px] rounded-xl'}"
	bind:this={containerEl}
	ondragover={onDragOver}
	ondrop={onDrop}
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
		<span class="flex items-center gap-1.5 font-medium">
			<Icon name="terminal" size={13} />
			Command
		</span>
		<span class="flex items-center gap-1.5 font-medium text-tertiary">
			<Icon name="filter_alt" size={13} />
			Transform
		</span>
		<span class="flex items-center gap-1.5 font-medium">
			<span class="h-0.5 w-4 rounded bg-primary"></span>
			Data flow
		</span>
		<span class="flex items-center gap-1.5 font-medium text-tertiary">
			<Icon name="alt_route" size={13} />
			Condition
		</span>
		<span class="hidden sm:inline text-outline-variant">·</span>
		<span class="hidden sm:inline font-mono text-[11px]">
			{graph.nodes.length} step{graph.nodes.length === 1 ? '' : 's'} · {graph.edges.length} edge{graph.edges.length === 1 ? '' : 's'}
		</span>
	</div>

	<!-- Canvas Controls Toolbar (Floating bottom-right) -->
	<div class="absolute bottom-4 right-4 z-20 flex items-center gap-1 rounded-xl border border-outline-variant/80 bg-surface-container/90 px-2 py-1.5 shadow-md backdrop-blur-md">
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
	<div class="pointer-events-none absolute bottom-4 left-4 z-20 hidden {editable ? '' : 'md:flex'} items-center gap-2 rounded-lg bg-surface-container-high/70 px-2.5 py-1 text-[11px] text-on-surface-variant/90 backdrop-blur-xs select-none">
		<Icon name="pan_tool" size={13} class="opacity-70" />
		<span>Drag canvas to pan · Scroll to zoom · Drag cards to reposition</span>
	</div>

	{#if graph.nodes.length === 0 && !editable}
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
							<animate attributeName="stroke-dashoffset" from="20" to="0" dur="1s" repeatCount="indefinite" />
						</path>
					{:else if edge.status === 'succeeded'}
						<path
							d={edge.d}
							fill="none"
							style="stroke: var(--md-sys-color-primary); stroke-width: 2.2px;"
							marker-end="url(#arrow-succeeded)"
						/>
					{:else if edge.status === 'not_taken'}
						<path
							d={edge.d}
							fill="none"
							style="stroke: var(--md-sys-color-outline-variant); stroke-width: 1.5px; stroke-dasharray: 1 6; stroke-linecap: round;"
						/>
					{:else if edge.status === 'blocked'}
						<path
							d={edge.d}
							fill="none"
							style="stroke: var(--md-sys-color-error); stroke-opacity: 0.55; stroke-width: 1.8px; stroke-dasharray: 2 5;"
							marker-end="url(#arrow-pending)"
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
				{#if connecting}
					{@const dx = Math.max(40, Math.abs(connecting.x2 - connecting.x1) * 0.45)}
					<path
						d="M {connecting.x1} {connecting.y1} C {connecting.x1 + dx} {connecting.y1}, {connecting.x2 - dx} {connecting.y2}, {connecting.x2} {connecting.y2}"
						fill="none"
						style="stroke: var(--md-sys-color-primary); stroke-width: 2.5px; stroke-dasharray: 6 4;"
						marker-end="url(#arrow-active)"
					/>
				{/if}
			</svg>

			<!-- Edge badges: the output that flows, the parser that made it, and how many items -->
			{#each graph.edges as edge (edge.id + '-badge')}
				<div
					class="group/edge absolute -translate-x-1/2 -translate-y-1/2 pointer-events-auto flex flex-col items-center gap-0.5 rounded-lg border bg-surface-container-high px-2 py-1 shadow-sm transition-transform duration-150 hover:scale-105 hover:shadow-md z-10 {edge.gate ? 'max-w-[200px]' : 'max-w-[150px]'}
						{edge.status === 'active'
							? 'border-primary ring-2 ring-primary/25'
							: edge.status === 'succeeded'
								? 'border-primary/60'
								: edge.status === 'blocked'
									? 'border-error/40 opacity-70'
									: edge.status === 'not_taken'
										? 'border-outline-variant/60 opacity-50'
										: 'border-outline-variant/80'}"
					style="left: {edge.midX}px; top: {edge.midY}px;"
					title={(edge.gate ? `${edge.toId} runs only if ${edge.gate}\n` : '') +
						(edge.bindings.length > 0
						? edge.bindings
								.map((b) => `${edge.fromId}.${b.sourceOutput} (${b.parser || b.type || 'value'}) → ${edge.toId}.${b.targetArg}`)
								.join('\n')
						: `${edge.toId} waits for ${edge.fromId}`)}
				>
					{#if editable}
						<button
							type="button"
							class="absolute -top-2 -right-2 size-5 items-center justify-center rounded-full bg-error text-on-error shadow-e1 {selectedStepId === edge.toId || selectedStepId === edge.fromId ? 'flex' : 'hidden group-hover/edge:flex'}"
							aria-label="Remove the connection from {edge.fromId} to {edge.toId}"
							title="Remove this connection"
							onpointerdown={(e) => e.stopPropagation()}
							onclick={() => onedgedelete?.(edge.fromId, edge.toId)}
						>
							<Icon name="close" size={12} />
						</button>
					{/if}
					{#if edge.gate}
						<div class="flex max-w-full items-center gap-1 font-mono text-[10px] font-medium text-tertiary">
							<Icon name="alt_route" size={12} class="shrink-0" />
							<span class="truncate">if {edge.gate}</span>
						</div>
					{/if}
					{#if edge.bindings.length > 0}
						{#each edge.bindings as b (b.sourceOutput + b.targetArg)}
							<div class="flex max-w-full items-center gap-1 font-mono text-[10.5px] text-on-surface-variant">
								<span class="truncate font-medium text-on-surface">{b.sourceOutput}</span>
								<span class="shrink-0">→</span>
								<span class="truncate font-medium text-primary">{b.targetArg}</span>
							</div>
						{/each}
						{#if edge.via || edge.count !== null}
							<div class="flex max-w-full items-center gap-1 font-mono text-[9.5px] text-on-surface-variant">
								{#if edge.via}
									<Icon name="data_object" size={11} class="shrink-0 text-tertiary" />
									<span class="truncate">{edge.via}</span>
								{/if}
								{#if edge.count !== null}
									<span class="shrink-0 rounded bg-surface-container-highest px-1">{edge.count} item{edge.count === 1 ? '' : 's'}</span>
								{/if}
							</div>
						{/if}
					{:else if !edge.gate}
						<div class="flex items-center gap-1 font-mono text-[9.5px] text-on-surface-variant">
							<Icon name="arrow_forward" size={11} class="opacity-70" />
							<span>after</span>
						</div>
					{/if}
				</div>
			{/each}

			<!-- Nodes Layer (Draggable cards) -->
			{#each graph.nodes as node (node.step.id)}
				{@const isSelected = selectedStepId === node.step.id}
				{@const isDraggingThis = draggingNodeId === node.step.id}
				{@const meta = STEP_STATUS[node.status]}
				{@const isTransform = node.step.kind === 'transform'}
				{@const dimmed = node.status === 'skipped' || node.status === 'blocked' || node.status === 'cancelled' || node.status === 'not_taken'}
				{@const flags = stepFlags(node.step)}
				{#if node.step.for_each}
					<!-- cards peeking out behind: this step runs once per item -->
					{#each [8, 4] as offset (offset)}
						<div
							class="pointer-events-none absolute rounded-xl border border-outline-variant bg-surface-container-low {draggingNodeId === node.step.id ? 'z-30' : 'z-10'}"
							style="left: {node.x + offset}px; top: {node.y + offset}px; width: {node.width}px; height: {node.height}px;"
						></div>
					{/each}
				{/if}
				<div
					role="button"
					tabindex="0"
					aria-label="{isTransform ? 'Transform' : 'Step'} {node.step.name}: {meta.label}"
					data-step-id={node.step.id}
					class="group absolute flex flex-col border text-left transition-shadow duration-150 focus:outline-none select-none
						{isTransform ? 'rounded-2xl border-dashed' : 'rounded-xl'}
						{isDraggingThis ? 'cursor-grabbing z-30 shadow-xl ring-2 ring-primary/40' : 'cursor-grab hover:shadow-md z-10'}
						{isSelected
							? 'border-primary ring-3 ring-primary/25 shadow-md bg-surface-container-high'
							: isTransform
								? 'border-tertiary/70 bg-surface-container-low hover:bg-surface-container'
								: 'border-outline-variant bg-surface-container-low hover:border-outline hover:bg-surface-container'}
						{node.status === 'attention' && !isSelected ? 'border-tertiary ring-3 ring-tertiary/30' : ''}
						{errors[node.step.id]?.length && !isSelected ? 'border-error ring-2 ring-error/30' : ''}
						{connecting && connecting.fromId !== node.step.id ? 'ring-2 ring-primary/40' : ''}
						{dimmed && !isSelected ? 'opacity-60' : ''}"
					style="left: {node.x}px; top: {node.y}px; width: {node.width}px; height: {node.height}px;"
					onpointerdown={(e) => handleNodePointerDown(e, node)}
					onpointermove={handleNodePointerMove}
					onpointerup={(e) => handleNodePointerUp(e, node)}
					onpointercancel={(e) => handleNodePointerUp(e, node)}
					onkeydown={(e) => {
						if (e.key === 'Enter' || e.key === ' ') {
							e.preventDefault();
							handleNodeClick(node);
						}
					}}
				>
					{#if run}
						<span
							class="h-1 w-full shrink-0 {isTransform ? 'rounded-t-2xl' : 'rounded-t-xl'} {meta.strip} {node.status === 'running' ? 'animate-pulse' : ''}"
						></span>
					{/if}

					<div class="flex min-h-0 flex-1 flex-col px-3 pt-2 pb-1">
						<div class="flex items-center justify-between gap-2">
							<span class="flex min-w-0 items-center gap-1.5">
								<Icon
									name={isTransform ? 'filter_alt' : 'terminal'}
									size={15}
									class="shrink-0 {isTransform ? 'text-tertiary' : 'text-on-surface-variant'}"
								/>
								<span class="truncate font-medium title-small text-on-surface" title={node.step.name}>
									{node.step.name}
								</span>
							</span>
							{#if node.duration}
								<span class="shrink-0 font-mono text-[11px] text-on-surface-variant">{node.duration}</span>
							{/if}
						</div>

						{#if isTransform && node.step.transform}
							{@const ops = node.step.transform.ops}
							{@const shown = ops.length > MAX_OP_LINES ? ops.slice(0, MAX_OP_LINES - 1) : ops}
							<ol class="mt-1 flex min-w-0 flex-col font-mono text-[10.5px] leading-[14px] text-on-surface-variant" title={ops.map(describeOp).join('\n')}>
								{#each shown as op, i (i)}
									<li class="flex min-w-0 items-center gap-1.5">
										<span class="size-1 shrink-0 rounded-full bg-tertiary"></span>
										<span class="truncate">{describeOp(op)}</span>
									</li>
								{:else}
									<li>pass-through</li>
								{/each}
								{#if shown.length < ops.length}
									<li class="pl-2.5 text-on-surface-variant/80">+{ops.length - shown.length} more</li>
								{/if}
							</ol>
						{:else}
							<div class="mt-0.5 flex items-center justify-between gap-1.5 font-mono text-[11px] text-on-surface-variant">
								<span class="truncate">{node.step.command_ref}</span>
								{#if node.cmdOutputs && node.cmdOutputs.length > 0}
									{@const primary = node.cmdOutputs[0]}
									<span
										class="shrink-0 rounded bg-tertiary-container/40 px-1.5 text-[9.5px] text-on-tertiary-container"
										title="Parser: {primary.parser || 'none'} ({primary.name}: {primary.type || 'text'})"
									>
										{primary.parser || primary.name}
									</span>
								{/if}
							</div>
						{/if}
						{#if flags.length}
							<div class="mt-1 flex min-w-0 gap-1 overflow-hidden">
								{#each flags as f (f.label)}
									<span
										class="flex shrink-0 items-center gap-0.5 rounded px-1.5 text-[9.5px] leading-4 {f.tone === 'gate'
											? 'bg-tertiary-container text-on-tertiary-container'
											: f.tone === 'warn'
												? 'border border-tertiary/60 text-tertiary'
												: 'bg-surface-container-highest text-on-surface-variant'}"
										title={f.title}
									>
										{#if f.tone === 'gate'}<Icon name="alt_route" size={11} />{/if}{f.label}
									</span>
								{/each}
							</div>
						{/if}
					</div>

					<!-- Status bar -->
					<div class="flex items-center justify-between gap-2 border-t border-outline-variant/50 px-3 py-1 text-[11.5px]" title={node.note || ''}>
						<span class="flex items-center gap-1 font-medium {meta.text}">
							{#if node.status === 'running'}
								<span class="size-2 animate-ping rounded-full bg-primary"></span>
							{:else}
								<Icon name={meta.icon} size={14} />
							{/if}
							{run ? meta.label : isTransform ? 'Transform' : 'Command'}
						</span>
						<span class="truncate text-[11px] text-on-surface-variant">
							{#if node.fan}
								<span class="font-mono" title="{node.fan.done} of {node.fan.total} items done{node.fan.failed ? `, ${node.fan.failed} failed` : ''}">
									{node.fan.done}/{node.fan.total} items{node.fan.failed ? ` · ${node.fan.failed} ✗` : ''}
								</span>
							{:else if node.counts}
								<span class="font-mono">{node.counts.input} → {node.counts.output}</span>
							{:else if node.note}
								<span class="italic">{node.note}</span>
							{:else if !node.step.depends_on?.length}
								<span class="rounded bg-surface-container-highest px-1.5 py-0.5">Start</span>
							{:else}
								<span>{node.step.depends_on.length} input{node.step.depends_on.length > 1 ? 's' : ''}</span>
							{/if}
						</span>
					</div>

					<!-- Ports -->
					{#if node.step.depends_on && node.step.depends_on.length > 0}
						<span
							class="absolute -left-1.5 top-1/2 size-3 -translate-y-1/2 rounded-full border-2 border-surface bg-outline-variant"
							title="Waits for: {node.step.depends_on.join(', ')}"
						></span>
					{/if}
					{#if editable}
						<!-- Drag from here onto another step to feed it this step's output. -->
						<span
							role="button"
							tabindex="-1"
							aria-label="Connect {node.step.name} to another step"
							class="absolute -right-2.5 top-1/2 flex size-5 -translate-y-1/2 cursor-crosshair items-center justify-center rounded-full border-2 border-surface text-on-primary transition-transform hover:scale-125 {isTransform ? 'bg-tertiary' : 'bg-primary'}"
							title="Drag onto another step to connect"
							onpointerdown={(e) => startConnect(e, node)}
						>
							<Icon name="add" size={12} />
						</span>
					{:else}
						<span
							class="absolute -right-1.5 top-1/2 size-3 -translate-y-1/2 rounded-full border-2 border-surface {isTransform ? 'bg-tertiary' : 'bg-primary'}"
							title="Output"
						></span>
					{/if}
					{#if errors[node.step.id]?.length}
						<span
							class="absolute -top-2 -right-2 flex size-5 items-center justify-center rounded-full bg-error text-on-error shadow-e1"
							title={errors[node.step.id].join('\n')}
						>
							<Icon name="priority_high" size={14} />
						</span>
					{/if}
				</div>
			{/each}
		</div>
	</div>
</div>
