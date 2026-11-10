/**
 * #hashtags in timeline posts. The rule is the server's (api/posts.py HASHTAG): a letter
 * first, then letters, digits, _ or -, up to 40; after a space or punctuation, not inside a
 * word or URL, not ##. Trailing - and _ aren't part of it.
 */
export const HASHTAG = /(?<![\p{L}\p{N}_#/&])#(\p{L}[\p{L}\p{N}_-]{0,39})/gu;

/** Turn the #hashtags in rendered markdown into links (`href(tag)`), outside code and links. */
export function chipHashtags(root: HTMLElement, href: (tag: string) => string, active?: string | null) {
	const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
		acceptNode: (n) => (n.parentElement?.closest('code, pre, a, .mention') || !n.textContent?.includes('#') ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT)
	});
	const nodes: Text[] = [];
	while (walker.nextNode()) nodes.push(walker.currentNode as Text);
	for (const node of nodes) {
		const text = node.textContent ?? '';
		const parts: (string | HTMLElement)[] = [];
		let last = 0;
		for (const match of text.matchAll(HASHTAG)) {
			const tag = match[1].replace(/[-_]+$/, '');
			const at = match.index!;
			parts.push(text.slice(last, at));
			const a = document.createElement('a');
			a.className = 'hashtag';
			a.href = href(tag);
			a.dataset.tag = tag;
			a.title = `Show the posts tagged #${tag}`;
			a.textContent = `#${tag}`;
			parts.push(a);
			last = at + 1 + tag.length;
		}
		if (!parts.length) continue;
		parts.push(text.slice(last));
		node.replaceWith(...parts.filter((p) => p !== ''));
	}
	// Links made earlier follow the filter as it changes.
	for (const a of root.querySelectorAll<HTMLElement>('a.hashtag')) a.classList.toggle('hashtag-on', !!active && a.dataset.tag?.toLowerCase() === active.toLowerCase());
}
