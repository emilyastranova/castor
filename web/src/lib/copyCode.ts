/**
 * Copying code out of rendered markdown: code blocks get a copy button and a snapshot button (a
 * picture of the code, SnapshotDialog), and inline `code` is itself a button that copies its text. Use on the element that holds the {@html} markdown:
 *
 *   <div class="prose-m3" use:copyableCode>{@html renderMarkdown(body)}</div>
 *
 * The markdown can change underneath (it's re-rendered, mentions are chipped), so new code is
 * picked up as it appears.
 */
import { snackbar } from '$lib/snackbar.svelte';
import { snapshots } from '$lib/snapshots.svelte';

/** Put text on the clipboard; falls back to a hidden textarea where the Clipboard API isn't
 *  available (plain http on a non-local address). */
export async function copyText(text: string): Promise<boolean> {
	try {
		if (navigator.clipboard && window.isSecureContext) {
			await navigator.clipboard.writeText(text);
			return true;
		}
	} catch {
		/* fall back */
	}
	const area = document.createElement('textarea');
	area.value = text;
	area.setAttribute('readonly', '');
	area.style.cssText = 'position:fixed;top:0;left:0;opacity:0;pointer-events:none';
	document.body.append(area);
	area.select();
	let ok = false;
	try {
		ok = document.execCommand('copy');
	} catch {
		ok = false;
	}
	area.remove();
	return ok;
}

function icon(name: string): HTMLSpanElement {
	const span = document.createElement('span');
	span.className = 'material-symbols-rounded';
	span.setAttribute('aria-hidden', 'true');
	span.textContent = name;
	return span;
}

function enhance(root: HTMLElement) {
	for (const pre of root.querySelectorAll<HTMLPreElement>('pre:not([data-copy])')) {
		// The button goes inside the <pre> (not a wrapper): {@html} keeps track of its top-level
		// nodes, so they stay where they are. The code scrolls; the button stays put.
		pre.dataset.copy = '';
		pre.classList.add('code-block');
		const button = document.createElement('button');
		button.type = 'button';
		button.className = 'code-copy';
		button.title = 'Copy';
		button.setAttribute('aria-label', 'Copy the code');
		button.append(icon('content_copy'));
		const shot = document.createElement('button');
		shot.type = 'button';
		shot.className = 'code-copy code-snapshot';
		shot.title = 'Snapshot';
		shot.setAttribute('aria-label', 'Make a picture of the code');
		shot.append(icon('photo_camera'));
		pre.append(shot, button);
	}
	for (const code of root.querySelectorAll<HTMLElement>(':not(pre) > code:not([data-copy])')) {
		if (code.closest('a, .mention')) continue; // a link's text goes where the link goes
		code.dataset.copy = '';
		code.classList.add('code-inline-copy');
		code.setAttribute('role', 'button');
		code.tabIndex = 0;
		code.title = 'Click to copy';
	}
}

async function copyFrom(text: string, done: () => void) {
	if (await copyText(text)) {
		done();
		snackbar.show('Copied to the clipboard');
	} else {
		snackbar.error("Couldn't copy: select the text instead");
	}
}

export function copyableCode(root: HTMLElement) {
	enhance(root);
	const observer = new MutationObserver(() => enhance(root));
	observer.observe(root, { childList: true, subtree: true });

	const onclick = (e: MouseEvent) => {
		const target = e.target as HTMLElement;
		const shot = target.closest<HTMLButtonElement>('.code-snapshot');
		if (shot && root.contains(shot)) {
			e.preventDefault();
			e.stopPropagation();
			const code = shot.closest('pre')?.querySelector('code');
			const language = [...(code?.classList ?? [])].find((c) => c.startsWith('language-'))?.slice(9) ?? null;
			snapshots.open({ text: (code?.textContent ?? '').replace(/\n$/, ''), language });
			return;
		}
		const button = target.closest<HTMLButtonElement>('.code-copy');
		if (button && root.contains(button)) {
			e.preventDefault();
			e.stopPropagation();
			const pre = button.closest('pre');
			const text = (pre?.querySelector('code')?.textContent ?? '').replace(/\n$/, '');
			void copyFrom(text, () => {
				button.replaceChildren(icon('check'));
				button.classList.add('copied');
				setTimeout(() => {
					button.replaceChildren(icon('content_copy'));
					button.classList.remove('copied');
				}, 1500);
			});
			return;
		}
		const code = target.closest<HTMLElement>('code.code-inline-copy');
		if (code && root.contains(code)) {
			// Selecting part of it to copy by hand still works.
			if (window.getSelection()?.toString()) return;
			e.preventDefault();
			e.stopPropagation();
			void copyFrom(code.textContent ?? '', () => {
				code.classList.add('copied');
				setTimeout(() => code.classList.remove('copied'), 900);
			});
		}
	};
	const onkeydown = (e: KeyboardEvent) => {
		const code = (e.target as HTMLElement).closest?.('code.code-inline-copy');
		if (code && (e.key === 'Enter' || e.key === ' ')) {
			e.preventDefault();
			(code as HTMLElement).click();
		}
	};
	// Capture: copying wins over whatever the surrounding entry does on click (a timeline entry
	// opens its card).
	root.addEventListener('click', onclick, true);
	root.addEventListener('keydown', onkeydown);
	return {
		destroy() {
			observer.disconnect();
			root.removeEventListener('click', onclick, true);
			root.removeEventListener('keydown', onkeydown);
		}
	};
}
