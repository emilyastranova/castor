<script lang="ts">
	/** Text whose letters can flicker in one at a time, in a scattered order (not left to right), like
	 *  a tube warming up: only where a theme turns it on (AstraLabs), by animating the hook class
	 *  `m3-flicker-letter` with the `m3-flicker-in` keyframes and each letter's `--d` delay. Elsewhere
	 *  it's plain text. The order comes from the text itself, so the server's page and the browser's
	 *  agree (no flash of the finished text, nothing hidden without scripts); screen readers get the
	 *  whole text at once. */
	interface Props {
		text: string;
		/** About how long until the last letter has come in, in ms. */
		span?: number;
	}
	let { text, span = 450 }: Props = $props();

	/** A small seeded generator (mulberry32), seeded from the text. */
	function random(seed: string) {
		let a = 0;
		for (const ch of seed) a = (Math.imul(a ^ ch.codePointAt(0)!, 2654435761) + 0x9e3779b9) | 0;
		return () => {
			a = (a + 0x6d2b79f5) | 0;
			let t = Math.imul(a ^ (a >>> 15), 1 | a);
			t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
			return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
		};
	}

	/** Words (kept whole when it wraps) of letters, each with when it flickers in. */
	const words = $derived.by(() => {
		const chars = [...text];
		const rand = random(text);
		const letters = chars.map((c, i) => ({ c, i })).filter((x) => x.c.trim());
		// Shuffle the letters' order (Fisher–Yates), then space them over the span with a little jitter.
		for (let i = letters.length - 1; i > 0; i--) {
			const j = Math.floor(rand() * (i + 1));
			[letters[i], letters[j]] = [letters[j], letters[i]];
		}
		const step = span / Math.max(letters.length, 1);
		const delay = new Map(letters.map((l, rank) => [l.i, Math.round(rank * step + rand() * step * 0.6)]));
		const out: { c: string; d: number | null }[][] = [[]];
		chars.forEach((c, i) => {
			if (!c.trim()) out.push([{ c, d: null }], []);
			else out[out.length - 1].push({ c, d: delay.get(i)! });
		});
		return out.filter((w) => w.length);
	});
</script>

<span class="flicker"><span class="sr-only">{text}</span><span aria-hidden="true">{#each words as word, w (w)}<span class="word">{#each word as l, i (i)}{#if l.d === null}{l.c}{:else}<span class="m3-flicker-letter" style:--d="{l.d}ms">{l.c}</span>{/if}{/each}</span>{/each}</span></span>

<style>
	.word {
		white-space: pre;
	}
	/* For themes to use (they can't define keyframes inside their scope): off, a bright stutter, dark,
	   on, a last dip, on. Nothing here animates by itself. */
	@keyframes -global-m3-flicker-in {
		0% { opacity: 0; }
		18% { opacity: 0.85; }
		30% { opacity: 0.1; }
		48% { opacity: 1; }
		62% { opacity: 0.35; }
		76%,
		100% { opacity: 1; }
	}
</style>
