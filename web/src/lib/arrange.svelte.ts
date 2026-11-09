import { MediaQuery } from 'svelte/reactivity';

/**
 * Arranging the board on a touch screen. A swipe over the cards scrolls; holding a card puts
 * the whole board into arrange mode (cards wiggle and can be dragged), and Done, a tap on empty
 * board or Escape leaves it. With a mouse cards drag as they always did.
 */
class Arrange {
	/** Cards wiggle and drag. */
	on = $state(false);
	readonly touch = new MediaQuery('(pointer: coarse)', false);

	/** Whether cards can be dragged right now. */
	get canDrag() {
		return !this.touch.current || this.on;
	}

	start() {
		if (this.on) return;
		this.on = true;
		try {
			navigator.vibrate?.(12);
		} catch {
			/* not offered */
		}
	}

	stop() {
		this.on = false;
	}
}

export const arrange = new Arrange();

const HOLD_MS = 450;
const SLOP_PX = 10;

/**
 * Hold-to-arrange on a card: a touch that stays put for HOLD_MS starts arrange mode, and the
 * click that follows the hold doesn't open the card. Moving the finger first (a scroll) cancels.
 */
export function holdToArrange(node: HTMLElement) {
	let timer: ReturnType<typeof setTimeout> | undefined;
	let x = 0;
	let y = 0;
	let held = false;

	const cancel = () => {
		clearTimeout(timer);
		timer = undefined;
	};
	const down = (e: PointerEvent) => {
		if (e.pointerType !== 'touch' || arrange.on) return;
		held = false;
		x = e.clientX;
		y = e.clientY;
		cancel();
		timer = setTimeout(() => {
			held = true;
			arrange.start();
		}, HOLD_MS);
	};
	const move = (e: PointerEvent) => {
		if (timer && Math.hypot(e.clientX - x, e.clientY - y) > SLOP_PX) cancel();
	};
	// The click after a hold (or any tap while arranging) doesn't open the card.
	const click = (e: MouseEvent) => {
		if (held || arrange.on) {
			e.stopPropagation();
			e.preventDefault();
			held = false;
		}
	};
	// Long-press menus (copy, share…) would cover the board.
	const menu = (e: Event) => {
		if (arrange.touch.current) e.preventDefault();
	};

	node.addEventListener('pointerdown', down);
	node.addEventListener('pointermove', move);
	node.addEventListener('pointerup', cancel);
	node.addEventListener('pointercancel', cancel);
	node.addEventListener('click', click, true);
	node.addEventListener('contextmenu', menu);
	return {
		destroy() {
			cancel();
			node.removeEventListener('pointerdown', down);
			node.removeEventListener('pointermove', move);
			node.removeEventListener('pointerup', cancel);
			node.removeEventListener('pointercancel', cancel);
			node.removeEventListener('click', click, true);
			node.removeEventListener('contextmenu', menu);
		}
	};
}
