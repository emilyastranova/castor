/**
 * Motion for things that appear and disappear while you watch (a comment someone posts, a
 * new event in the timeline, a job finishing). One vocabulary everywhere:
 *
 *   in:enter / out:leave   list rows: the row opens its space (so neighbours glide instead of
 *                          jumping), then fades and drops in a few pixels; leaving reverses it
 *   in:pop / out:pop       cards in a grid: fade with a slight scale
 *   animate:glide          reordering
 *
 * Only live changes should animate: pass `{ enabled: false }` while a list is (re)loading so
 * a page or filter change doesn't cascade. With "reduce motion" turned on in the OS, every
 * transition is a short fade with no movement; in a hidden tab nothing animates (its frames
 * don't run, so leaving rows would linger until you came back).
 */
import { cubicOut } from 'svelte/easing';
import { flip, type AnimationConfig } from 'svelte/animate';
import type { TransitionConfig } from 'svelte/transition';

interface Params {
	/** false: appear/disappear instantly (initial loads, filter changes). */
	enabled?: boolean;
	duration?: number;
	delay?: number;
}

const reducedQuery = typeof window !== 'undefined' ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
export const reducedMotion = () => !!reducedQuery?.matches;
const hidden = () => typeof document !== 'undefined' && document.hidden;

function still(enabled: boolean | undefined): boolean {
	return enabled === false || hidden();
}

function fadeOnly(duration = 140, delay = 0): TransitionConfig {
	return { duration, delay, css: (t) => `opacity: ${t}` };
}

/** A list row arriving: its height opens, then it fades in and settles from just above. */
export function enter(node: Element, { enabled, duration = 240, delay = 0 }: Params = {}): TransitionConfig {
	if (still(enabled)) return { duration: 0 };
	if (reducedMotion()) return fadeOnly(140, delay);
	return collapse(node, duration, delay, -6);
}

/** A list row going: it fades and its space closes. */
export function leave(node: Element, { enabled, duration = 170, delay = 0 }: Params = {}): TransitionConfig {
	if (still(enabled)) return { duration: 0 };
	if (reducedMotion()) return fadeOnly(120, delay);
	return collapse(node, duration, delay, 0);
}

function collapse(node: Element, duration: number, delay: number, y: number): TransitionConfig {
	const style = getComputedStyle(node);
	const px = (v: string) => parseFloat(v) || 0;
	const height = px(style.height);
	const box = {
		pt: px(style.paddingTop),
		pb: px(style.paddingBottom),
		mt: px(style.marginTop),
		mb: px(style.marginBottom),
		bt: px(style.borderTopWidth),
		bb: px(style.borderBottomWidth)
	};
	return {
		duration,
		delay,
		easing: cubicOut,
		css: (t) => {
			// Space first, content second: the row is mostly open before it becomes visible.
			const opacity = Math.max(0, (t - 0.35) / 0.65);
			return [
				'overflow: hidden',
				`height: ${t * height}px`,
				`padding-top: ${t * box.pt}px`,
				`padding-bottom: ${t * box.pb}px`,
				`margin-top: ${t * box.mt}px`,
				`margin-bottom: ${t * box.mb}px`,
				`border-top-width: ${t * box.bt}px`,
				`border-bottom-width: ${t * box.bb}px`,
				`opacity: ${opacity}`,
				`transform: translateY(${(1 - t) * y}px)`
			].join(';');
		}
	};
}

/** A card in a grid (pinned notes): fades with a slight scale, without moving its neighbours. */
export function pop(_node: Element, { enabled, duration = 200, delay = 0 }: Params = {}): TransitionConfig {
	if (still(enabled)) return { duration: 0 };
	if (reducedMotion()) return fadeOnly(140, delay);
	return { duration, delay, easing: cubicOut, css: (t) => `opacity: ${t}; transform: scale(${0.96 + 0.04 * t})` };
}

/** Reordering: items glide to their new places (instantly with reduced motion). */
export function glide(node: Element, fromTo: { from: DOMRect; to: DOMRect }, { enabled, duration = 220 }: Params = {}): AnimationConfig {
	return flip(node, fromTo, { duration: still(enabled) || reducedMotion() ? 0 : duration, easing: cubicOut });
}

/**
 * Cards that move between lists while you watch (someone else moved them): FLIP across
 * containers. `flying` holds the ones in the air, so their lists skip enter/leave for them;
 * `fly` draws a copy over everything (scroll areas can't clip it) from where the card was to
 * where it is now, while the real card waits invisible in its new place.
 */
export const flying = new Set<string>();

export function rectOf(selector: string): DOMRect | undefined {
	const el = document.querySelector<HTMLElement>(selector);
	return el ? el.getBoundingClientRect() : undefined;
}

export function fly(selector: string, from: DOMRect, id: string, duration = 420): void {
	const done = () => {
		flying.delete(id);
	};
	const el = document.querySelector<HTMLElement>(selector);
	if (!el || reducedMotion() || hidden()) return done();
	const to = el.getBoundingClientRect();
	const dx = from.left - to.left;
	const dy = from.top - to.top;
	if (Math.hypot(dx, dy) < 4 || !to.width) return done();
	const ghost = el.cloneNode(true) as HTMLElement;
	Object.assign(ghost.style, {
		position: 'fixed',
		left: `${to.left}px`,
		top: `${to.top}px`,
		width: `${to.width}px`,
		height: `${to.height}px`,
		margin: '0',
		zIndex: '60',
		pointerEvents: 'none',
		boxSizing: 'border-box'
	});
	ghost.removeAttribute('id');
	ghost.setAttribute('aria-hidden', 'true');
	document.body.append(ghost);
	el.style.visibility = 'hidden';
	const anim = ghost.animate(
		[
			{ transform: `translate(${dx}px, ${dy}px)`, boxShadow: 'none' },
			{ transform: `translate(${dx * 0.5}px, ${dy * 0.5 - 6}px) scale(1.03)`, boxShadow: 'var(--md-sys-elevation-3, 0 8px 24px rgb(0 0 0 / 0.3))', offset: 0.5 },
			{ transform: 'none', boxShadow: 'none' }
		],
		{ duration, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' }
	);
	const finish = () => {
		ghost.remove();
		el.style.visibility = '';
		done();
	};
	anim.onfinish = finish;
	anim.oncancel = finish;
}

/** Where every element matching `selector` is now, by its `attr` value. */
export function snapshot(selector: string, attr: string): Map<string, DOMRect> {
	const out = new Map<string, DOMRect>();
	for (const el of document.querySelectorAll<HTMLElement>(selector)) {
		const key = el.getAttribute(attr);
		if (key) out.set(key, el.getBoundingClientRect());
	}
	return out;
}

/** Slide every element that moved since `before` from its old place to its new one (the
 *  neighbours a moved card pushed or left behind). `skip` is the card flying on its own. */
export function shift(selector: string, attr: string, before: Map<string, DOMRect>, skip?: string, duration = 260): void {
	if (reducedMotion() || hidden()) return;
	for (const el of document.querySelectorAll<HTMLElement>(selector)) {
		const key = el.getAttribute(attr);
		if (!key || key === skip) continue;
		const from = before.get(key);
		if (!from) continue;
		const to = el.getBoundingClientRect();
		const dx = from.left - to.left;
		const dy = from.top - to.top;
		if (Math.abs(dx) < 1 && Math.abs(dy) < 1) continue;
		el.animate([{ transform: `translate(${dx}px, ${dy}px)` }, { transform: 'none' }], { duration, easing: 'cubic-bezier(0.2, 0.8, 0.2, 1)' });
	}
}
