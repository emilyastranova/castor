/** Terminal snapshots: a picture of a job's output for a report (the card's Snapshot button).
 *
 *  The output is replayed into an unopened xterm.js terminal, the same emulator the console uses,
 *  so colours, carriage-return progress bars and interactive (tty) jobs read as they did there.
 *  Its cells become logical lines; a recipe (what's shown, hidden and highlighted, the look) turns
 *  those into a scene of rectangles and text runs, drawn on a canvas for PNG or written as SVG.
 *  The recipe is stored with the attachment, so a snapshot can be opened and changed again. */

import type { Job, LogChunk } from '$lib/api';

// ---------- the recipe ----------

export type Frame = 'window' | 'bar' | 'none';
export type Backdrop = 'none' | 'solid' | 'gradient';
export type ThemeName = 'console' | 'paper' | 'theme';

export interface Palette {
	bg: string;
	fg: string;
	/** Line numbers, the window title, hidden text. */
	dim: string;
	/** The prompt, highlights. */
	accent: string;
	/** The window's title bar. */
	chrome: string;
	backdrop: [string, string];
	/** ANSI 0–15. */
	ansi: string[];
}

export interface Hidden {
	/** A logical line, and cells [start, end) in it. */
	line: number;
	start: number;
	end: number;
}

export interface SnapshotRecipe {
	v: 1;
	job_id?: string;
	/** Logical lines [from, to) of the job's output. */
	from: number;
	to: number;
	highlight: number[];
	hide: Hidden[];
	/** Regular expressions whose matches are hidden on every line. */
	patterns: string[];
	title: string;
	/** Show the job's command as a prompt line first. */
	command: boolean;
	/** Castor's own lines ("Job dispatched to …"). */
	system: boolean;
	frame: Frame;
	theme: ThemeName;
	/** The colours of `theme: 'theme'`, captured when it was chosen (a report shouldn't change with
	 *  the UI's theme). */
	palette?: Palette;
	background: Backdrop;
	padding: number;
	font_size: number;
	/** Columns to wrap at. */
	wrap: number;
	line_numbers: boolean;
	scale: number;
}

export function newRecipe(job: Job, title: string, from = 0, to = Number.MAX_SAFE_INTEGER): SnapshotRecipe {
	return {
		v: 1,
		job_id: job.id,
		from,
		to,
		highlight: [],
		hide: [],
		patterns: [],
		title,
		command: !job.tty,
		system: false,
		frame: 'window',
		theme: 'console',
		background: 'gradient',
		padding: 48,
		font_size: 14,
		wrap: 100,
		line_numbers: false,
		scale: 2
	};
}

// ---------- palettes ----------

const CONSOLE: Palette = {
	bg: '#101418',
	fg: '#e2e8f0',
	dim: '#6e7681',
	accent: '#6cb6ff',
	chrome: '#1a1f25',
	backdrop: ['#3b4a9a', '#1e7d8a'],
	ansi: ['#1d2228', '#ff6b6b', '#69d48c', '#f2cc60', '#6cb6ff', '#d38aff', '#56d4dd', '#c9d1d9',
		'#6e7681', '#ff8f8f', '#8ae4a6', '#f8e08e', '#9ecbff', '#e2b0ff', '#8be9f0', '#f0f6fc']
};

/** Light, for printed reports. */
const PAPER: Palette = {
	bg: '#fbfbfd',
	fg: '#1f2328',
	dim: '#8c959f',
	accent: '#0969da',
	chrome: '#eceef2',
	backdrop: ['#dde3ef', '#c9d3e6'],
	ansi: ['#24292f', '#cf222e', '#116329', '#7d4e00', '#0969da', '#8250df', '#1b7c83', '#6e7781',
		'#57606a', '#a40e26', '#1a7f37', '#633c01', '#218bff', '#a475f9', '#3192aa', '#8c959f']
};

export const THEMES: { value: ThemeName; label: string }[] = [
	{ value: 'console', label: 'Console (dark)' },
	{ value: 'paper', label: 'Paper (light, for print)' },
	{ value: 'theme', label: 'Your Castor theme' }
];

/** The current Castor theme's colours, to keep in a recipe. */
export function themePalette(): Palette {
	const css = getComputedStyle(document.documentElement);
	const v = (name: string, or: string) => css.getPropertyValue(`--md-sys-color-${name}`).trim() || or;
	const bg = v('surface-container-lowest', CONSOLE.bg);
	const base = luminance(bg) < 0.4 ? CONSOLE : PAPER;
	return {
		bg,
		fg: v('on-surface', base.fg),
		dim: v('on-surface-variant', base.dim),
		accent: v('primary', base.accent),
		chrome: v('surface-container', base.chrome),
		backdrop: [v('primary-container', base.backdrop[0]), v('tertiary-container', base.backdrop[1])],
		ansi: base.ansi
	};
}

export function paletteOf(r: SnapshotRecipe): Palette {
	if (r.theme === 'theme' && r.palette) return r.palette;
	return r.theme === 'paper' ? PAPER : CONSOLE;
}

function luminance(color: string): number {
	const m = /^#?([0-9a-f]{6})$/i.exec(color.trim());
	if (!m) return 0;
	const n = parseInt(m[1], 16);
	return (0.2126 * ((n >> 16) & 255) + 0.7152 * ((n >> 8) & 255) + 0.0722 * (n & 255)) / 255;
}

/** xterm's 256 colours: 16 from the palette, a 6×6×6 cube, 24 greys. */
function color256(n: number, p: Palette): string {
	if (n < 16) return p.ansi[n];
	const hex = (x: number) => x.toString(16).padStart(2, '0');
	if (n >= 232) {
		const g = 8 + (n - 232) * 10;
		return `#${hex(g)}${hex(g)}${hex(g)}`;
	}
	const i = n - 16;
	const c = (x: number) => (x ? 55 + x * 40 : 0);
	return `#${hex(c(Math.floor(i / 36)))}${hex(c(Math.floor(i / 6) % 6))}${hex(c(i % 6))}`;
}

// ---------- reading the output ----------

/** A colour as the terminal had it: a palette index (-1: the accent), an RGB value, or the default. */
type Ink = number | string | null;

export interface Cell {
	ch: string;
	/** Columns it takes (2 for wide characters). */
	w: number;
	fg: Ink;
	bg: Ink;
	bold: boolean;
	italic: boolean;
	dim: boolean;
	underline: boolean;
	inverse: boolean;
}

export interface Line {
	cells: Cell[];
	/** Castor's own line (written dim and italic by the console). */
	system: boolean;
	/** Its text, cell by cell. */
	text: string;
}

const PLAIN_COLS = 160;

/** Plain streams need CR before LF; stderr red, Castor's lines dim and italic (as the console). */
function chunkText(c: LogChunk, tty: boolean): string {
	if (tty && c.stream === 'stdout') return c.data;
	const text = c.data.replace(/\r?\n/g, '\r\n');
	if (c.stream === 'stderr') return `\x1b[31m${text}\x1b[0m`;
	if (c.stream === 'system') return `\x1b[2;3m${text}\x1b[0m`;
	return text;
}

/** The job's output as logical lines (wrapped rows joined again), the way the console shows it. */
export async function readOutput(job: Job, logs: LogChunk[]): Promise<Line[]> {
	const { Terminal } = await import('@xterm/xterm');
	const tty = !!job.tty;
	const term = new Terminal({
		cols: tty && job.tty_size ? job.tty_size.cols : PLAIN_COLS,
		rows: tty && job.tty_size ? job.tty_size.rows : 24,
		scrollback: 20000,
		allowProposedApi: true
	});
	try {
		for (const c of [...logs].sort((a, b) => a.seq - b.seq)) term.write(chunkText(c, tty));
		await new Promise<void>((resolve) => term.write('', resolve));
		const buf = term.buffer.active;
		const lines: Cell[][] = [];
		const scratch = buf.getNullCell();
		for (let y = 0; y < buf.length; y++) {
			const row = buf.getLine(y);
			if (!row) continue;
			const cells: Cell[] = [];
			for (let x = 0; x < row.length; x++) {
				const c = row.getCell(x, scratch);
				if (!c || c.getWidth() === 0) continue; // the second half of a wide character
				const ink = (def: boolean, pal: boolean, rgb: boolean, v: number): Ink =>
					def ? null : pal ? v : rgb ? `#${v.toString(16).padStart(6, '0')}` : null;
				cells.push({
					ch: c.getChars() || ' ',
					w: c.getWidth(),
					fg: ink(c.isFgDefault(), c.isFgPalette(), c.isFgRGB(), c.getFgColor()),
					bg: ink(c.isBgDefault(), c.isBgPalette(), c.isBgRGB(), c.getBgColor()),
					bold: !!c.isBold(),
					italic: !!c.isItalic(),
					dim: !!c.isDim(),
					underline: !!c.isUnderline(),
					inverse: !!c.isInverse()
				});
			}
			if (row.isWrapped && lines.length) lines[lines.length - 1].push(...cells);
			else lines.push(cells);
		}
		const out = lines.map((cells) => {
			let end = cells.length;
			while (end > 0 && cells[end - 1].ch === ' ' && cells[end - 1].bg === null && !cells[end - 1].inverse) end--;
			const kept = cells.slice(0, end);
			const ink = kept.filter((c) => c.ch !== ' ');
			return { cells: kept, text: kept.map((c) => c.ch).join(''), system: ink.length > 0 && ink.every((c) => c.dim && c.italic) };
		});
		while (out.length && !out[out.length - 1].cells.length) out.pop();
		return out;
	} finally {
		term.dispose();
	}
}

/** Cells [start, end) a regex hides on a line (offsets into `text` mapped onto cells). */
export function patternHits(line: Line, patterns: string[]): Hidden[] {
	const hits: Hidden[] = [];
	if (!patterns.length) return hits;
	// String offset → cell index (a cell may hold more than one UTF-16 unit).
	const cellAt: number[] = [];
	line.cells.forEach((c, i) => {
		for (let k = 0; k < c.ch.length; k++) cellAt.push(i);
	});
	cellAt.push(line.cells.length);
	for (const p of patterns) {
		const re = compile(p);
		if (!re) continue;
		for (const m of line.text.matchAll(re)) {
			if (!m[0]) continue;
			const s = m.index ?? 0;
			hits.push({ line: -1, start: cellAt[s], end: cellAt[s + m[0].length] });
		}
	}
	return hits;
}

/** A pattern as a global regex, or null when it doesn't compile. */
export function compile(pattern: string): RegExp | null {
	if (!pattern.trim()) return null;
	try {
		return new RegExp(pattern, 'g');
	} catch {
		return null;
	}
}

// ---------- the scene ----------

export const FONT = '"Roboto Mono", ui-monospace, SFMono-Regular, Menlo, monospace';

type Item =
	| { t: 'rect'; x: number; y: number; w: number; h: number; r?: number; fill: string; opacity?: number; shadow?: boolean }
	| { t: 'gradient'; x: number; y: number; w: number; h: number; from: string; to: string }
	| { t: 'circle'; cx: number; cy: number; r: number; fill: string }
	| { t: 'text'; x: number; y: number; text: string; fill: string; size: number; bold?: boolean; italic?: boolean; underline?: boolean; opacity?: number; anchor?: 'middle'; family?: string }
	| { t: 'clip'; x: number; y: number; w: number; h: number; r: number }
	| { t: 'unclip' };

export interface Scene {
	width: number;
	height: number;
	items: Item[];
}

let measurer: CanvasRenderingContext2D | null = null;
function charWidth(size: number): number {
	measurer ??= document.createElement('canvas').getContext('2d');
	if (!measurer) return size * 0.6;
	measurer.font = `${size}px ${FONT}`;
	return measurer.measureText('MMMMMMMMMM').width / 10;
}

/** Load the monospace face first, or the canvas measures and draws a fallback. */
export async function fontsReady(size: number): Promise<void> {
	try {
		await Promise.all([document.fonts.load(`400 ${size}px "Roboto Mono"`), document.fonts.load(`700 ${size}px "Roboto Mono"`)]);
	} catch {
		/* the fallback face, then */
	}
}

interface Run {
	col: number;
	cells: Cell[];
	hidden: boolean;
}

/** One logical line's rows at `wrap` columns: runs of cells that look the same, or are hidden. */
function rows(line: Line, wrap: number, hidden: (i: number) => boolean): Run[][] {
	const out: Run[][] = [[]];
	let col = 0;
	line.cells.forEach((c, i) => {
		if (col + c.w > wrap && col > 0) {
			out.push([]);
			col = 0;
		}
		const row = out[out.length - 1];
		const h = hidden(i);
		const last = row[row.length - 1];
		if (last && last.hidden === h && (h || same(last.cells[last.cells.length - 1], c))) last.cells.push(c);
		else row.push({ col, cells: [c], hidden: h });
		col += c.w;
	});
	return out;
}

const same = (a: Cell, b: Cell) =>
	a.fg === b.fg && a.bg === b.bg && a.bold === b.bold && a.italic === b.italic && a.dim === b.dim && a.underline === b.underline && a.inverse === b.inverse;

const width = (cells: Cell[]) => cells.reduce((n, c) => n + c.w, 0);

/** The picture, as rectangles and text runs. */
export function buildScene(lines: Line[], recipe: SnapshotRecipe, job: Pick<Job, 'command'>): Scene {
	const p = paletteOf(recipe);
	const size = recipe.font_size;
	const cw = charWidth(size);
	const lh = Math.round(size * 1.55);
	const wrap = Math.max(20, Math.min(400, recipe.wrap));
	const items: Item[] = [];

	// What's shown: the command as a prompt line, then the chosen lines.
	type Shown = { line: Line; index: number | null; prompt?: boolean };
	const shown: Shown[] = [];
	if (recipe.command && job.command.trim()) {
		const text = `$ ${job.command.trim().split('\n')[0]}`;
		const cells: Cell[] = [...text].map((ch, i) => ({ ch, w: 1, fg: i === 0 ? -1 : null, bg: null, bold: i === 0, italic: false, dim: false, underline: false, inverse: false }));
		shown.push({ line: { cells, text, system: false }, index: null, prompt: true });
	}
	const to = Math.min(recipe.to, lines.length);
	for (let i = Math.max(0, recipe.from); i < to; i++) {
		if (!recipe.system && lines[i].system) continue;
		shown.push({ line: lines[i], index: i });
	}

	const highlight = new Set(recipe.highlight);
	let number = 0;
	type Laid = { runs: Run[]; number: number | null; highlight: boolean; prompt: boolean; first: boolean };
	const laid: Laid[] = [];
	for (const s of shown) {
		const hidden = new Set<number>();
		if (s.index !== null) for (const h of recipe.hide) if (h.line === s.index) for (let k = h.start; k < h.end; k++) hidden.add(k);
		// Patterns hide on every line, the command's too.
		for (const h of patternHits(s.line, recipe.patterns)) for (let k = h.start; k < h.end; k++) hidden.add(k);
		const n = s.index !== null ? ++number : null;
		rows(s.line, wrap, (i) => hidden.has(i)).forEach((runs, k) =>
			laid.push({ runs, number: k === 0 ? n : null, highlight: s.index !== null && highlight.has(s.index), prompt: !!s.prompt, first: k === 0 })
		);
	}
	if (!laid.length) laid.push({ runs: [], number: null, highlight: false, prompt: false, first: true });

	const cols = Math.max(...laid.map((l) => l.runs.reduce((n, r) => Math.max(n, r.col + width(r.cells)), 0)), 24);
	const gutter = recipe.line_numbers ? (String(Math.max(number, 1)).length + 2) * cw : 0;
	const padX = Math.round(size * 1.5);
	const padY = Math.round(size * 1.2);
	const bar = recipe.frame === 'none' ? 0 : Math.round(size * 2.5);
	const winW = Math.ceil(Math.max(gutter + cols * cw + padX * 2, recipe.frame === 'none' ? 0 : 280));
	const winH = Math.ceil(bar + padY * 2 + laid.length * lh);
	const outer = recipe.background === 'none' ? 0 : recipe.padding;
	const W = winW + outer * 2;
	const H = winH + outer * 2;
	const radius = Math.round(size * 0.75);

	if (recipe.background === 'gradient') items.push({ t: 'gradient', x: 0, y: 0, w: W, h: H, from: p.backdrop[0], to: p.backdrop[1] });
	if (recipe.background === 'solid') items.push({ t: 'rect', x: 0, y: 0, w: W, h: H, fill: p.backdrop[0] });
	const wx = outer;
	const wy = outer;
	items.push({ t: 'rect', x: wx, y: wy, w: winW, h: winH, r: radius, fill: p.bg, shadow: recipe.background !== 'none' });
	items.push({ t: 'clip', x: wx, y: wy, w: winW, h: winH, r: radius });

	if (bar) {
		items.push({ t: 'rect', x: wx, y: wy, w: winW, h: bar, fill: p.chrome });
		if (recipe.frame === 'window') {
			['#ff5f57', '#febc2e', '#28c840'].forEach((fill, i) =>
				items.push({ t: 'circle', cx: wx + padX * 0.6 + size * 0.45 + i * size * 1.25, cy: wy + bar / 2, r: size * 0.42, fill })
			);
		}
		if (recipe.title.trim()) {
			items.push({ t: 'text', x: wx + winW / 2, y: wy + bar / 2 + size * 0.35, text: clip(recipe.title.trim(), Math.floor((winW - size * 9) / (size * 0.55))), fill: p.dim, size: size * 0.9, anchor: 'middle', family: 'Roboto, system-ui, sans-serif' });
		}
	}

	const textX = wx + padX + gutter;
	laid.forEach((l, k) => {
		const top = wy + bar + padY + k * lh;
		const base = top + lh / 2 + size * 0.35;
		if (l.highlight) {
			items.push({ t: 'rect', x: wx, y: top, w: winW, h: lh, fill: p.accent, opacity: 0.16 });
			if (l.first) items.push({ t: 'rect', x: wx, y: top, w: 3, h: lh, fill: p.accent });
		}
		if (l.number !== null && gutter) {
			const label = String(l.number);
			items.push({ t: 'text', x: textX - cw * (label.length + 1), y: base, text: label, fill: p.dim, size, opacity: 0.8 });
		}
		for (const r of l.runs) {
			const x = textX + r.col * cw;
			const w = width(r.cells) * cw;
			if (r.hidden) {
				items.push({ t: 'rect', x: x + 1, y: top + lh * 0.18, w: w - 2, h: lh * 0.64, r: 3, fill: p.dim, opacity: 0.85 });
				continue;
			}
			const c = r.cells[0];
			let fg = c.fg === -1 ? p.accent : c.fg === null ? p.fg : typeof c.fg === 'number' ? color256(c.fg, p) : c.fg;
			let bg = c.bg === null ? null : typeof c.bg === 'number' ? color256(c.bg, p) : c.bg;
			if (c.inverse) [fg, bg] = [bg ?? p.bg, fg];
			if (bg) items.push({ t: 'rect', x, y: top, w, h: lh, fill: bg });
			const text = r.cells.map((x) => x.ch).join('');
			if (text.trim()) items.push({ t: 'text', x, y: base, text, fill: fg, size, bold: c.bold, italic: c.italic, underline: c.underline, opacity: c.dim ? 0.6 : undefined });
		}
	});
	items.push({ t: 'unclip' });
	return { width: W, height: H, items };
}

const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, Math.max(1, n - 1))}…` : s);

// ---------- drawing ----------

/** The scene on a canvas, `scale` times its size (2 for sharp print). */
export function toCanvas(scene: Scene, scale: number): HTMLCanvasElement {
	const canvas = document.createElement('canvas');
	canvas.width = Math.ceil(scene.width * scale);
	canvas.height = Math.ceil(scene.height * scale);
	const g = canvas.getContext('2d');
	if (!g) return canvas;
	g.scale(scale, scale);
	g.textBaseline = 'alphabetic';
	for (const it of scene.items) {
		g.save();
		switch (it.t) {
			case 'gradient': {
				const grad = g.createLinearGradient(it.x, it.y, it.x + it.w, it.y + it.h);
				grad.addColorStop(0, it.from);
				grad.addColorStop(1, it.to);
				g.fillStyle = grad;
				g.fillRect(it.x, it.y, it.w, it.h);
				break;
			}
			case 'rect':
				g.globalAlpha = it.opacity ?? 1;
				g.fillStyle = it.fill;
				if (it.shadow) {
					g.shadowColor = 'rgba(0, 0, 0, 0.35)';
					g.shadowBlur = 40;
					g.shadowOffsetY = 14;
				}
				g.beginPath();
				g.roundRect(it.x, it.y, it.w, it.h, it.r ?? 0);
				g.fill();
				break;
			case 'circle':
				g.fillStyle = it.fill;
				g.beginPath();
				g.arc(it.cx, it.cy, it.r, 0, Math.PI * 2);
				g.fill();
				break;
			case 'text': {
				g.globalAlpha = it.opacity ?? 1;
				g.fillStyle = it.fill;
				g.font = `${it.italic ? 'italic ' : ''}${it.bold ? 700 : 400} ${it.size}px ${it.family ?? FONT}`;
				g.textAlign = it.anchor === 'middle' ? 'center' : 'left';
				g.fillText(it.text, it.x, it.y);
				if (it.underline) g.fillRect(it.x, it.y + 2, g.measureText(it.text).width, 1);
				break;
			}
			case 'clip':
				g.restore();
				g.save();
				g.beginPath();
				g.roundRect(it.x, it.y, it.w, it.h, it.r);
				g.clip();
				continue; // keep the clip until 'unclip'
			case 'unclip':
				g.restore();
				g.restore();
				continue;
		}
		g.restore();
	}
	return canvas;
}

export function toPng(scene: Scene, scale: number): Promise<Blob> {
	return new Promise((resolve, reject) => toCanvas(scene, scale).toBlob((b) => (b ? resolve(b) : reject(new Error('Could not draw the picture'))), 'image/png'));
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const n = (x: number) => Math.round(x * 100) / 100;

/** The scene as a standalone SVG (text stays text; the viewer needs Roboto Mono to match exactly). */
export function toSvg(scene: Scene): string {
	const out: string[] = [];
	let defs = '';
	let clips = 0;
	for (const it of scene.items) {
		switch (it.t) {
			case 'gradient':
				defs += `<linearGradient id="bg" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="${esc(it.from)}"/><stop offset="1" stop-color="${esc(it.to)}"/></linearGradient>`;
				out.push(`<rect x="${n(it.x)}" y="${n(it.y)}" width="${n(it.w)}" height="${n(it.h)}" fill="url(#bg)"/>`);
				break;
			case 'rect':
				if (it.shadow && !defs.includes('id="shadow"')) defs += '<filter id="shadow" x="-20%" y="-20%" width="140%" height="150%"><feDropShadow dx="0" dy="14" stdDeviation="20" flood-color="#000" flood-opacity="0.35"/></filter>';
				out.push(`<rect x="${n(it.x)}" y="${n(it.y)}" width="${n(it.w)}" height="${n(it.h)}"${it.r ? ` rx="${it.r}"` : ''} fill="${esc(it.fill)}"${it.opacity !== undefined ? ` opacity="${it.opacity}"` : ''}${it.shadow ? ' filter="url(#shadow)"' : ''}/>`);
				break;
			case 'circle':
				out.push(`<circle cx="${n(it.cx)}" cy="${n(it.cy)}" r="${n(it.r)}" fill="${esc(it.fill)}"/>`);
				break;
			case 'text':
				out.push(`<text x="${n(it.x)}" y="${n(it.y)}" font-size="${it.size}" fill="${esc(it.fill)}"${it.bold ? ' font-weight="700"' : ''}${it.italic ? ' font-style="italic"' : ''}${it.underline ? ' text-decoration="underline"' : ''}${it.opacity !== undefined ? ` opacity="${it.opacity}"` : ''}${it.anchor === 'middle' ? ' text-anchor="middle"' : ''}${it.family ? ` font-family="${esc(it.family)}"` : ''}>${esc(it.text)}</text>`);
				break;
			case 'clip':
				defs += `<clipPath id="c${clips}"><rect x="${n(it.x)}" y="${n(it.y)}" width="${n(it.w)}" height="${n(it.h)}" rx="${it.r}"/></clipPath>`;
				out.push(`<g clip-path="url(#c${clips++})">`);
				break;
			case 'unclip':
				out.push('</g>');
				break;
		}
	}
	return `<svg xmlns="http://www.w3.org/2000/svg" width="${scene.width}" height="${scene.height}" viewBox="0 0 ${scene.width} ${scene.height}" font-family="${esc(FONT)}" xml:space="preserve"><defs>${defs}</defs>${out.join('')}</svg>`;
}

/** A file name for the picture: the card's title, the lines. */
export function fileName(title: string, ext: 'png' | 'svg'): string {
	const base = title.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').slice(0, 60) || 'output';
	return `snapshot-${base}.${ext}`;
}
