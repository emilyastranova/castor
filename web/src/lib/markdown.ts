import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { browser } from '$app/environment';
import { spoilerExtension } from './spoilers';
import { customEmojis } from './emojis.svelte';
import type { TokenizerAndRendererExtension } from 'marked';

/** :name: for one of the instance's custom emoji draws its picture (an unknown name stays text). */
const customEmojiExtension: TokenizerAndRendererExtension = {
	name: 'customEmoji',
	level: 'inline',
	start(src: string) {
		const i = src.search(/:[a-z0-9_]{2,32}:/);
		return i < 0 ? undefined : i;
	},
	tokenizer(src: string) {
		const m = /^:([a-z0-9_]{2,32}):/.exec(src);
		if (!m || !customEmojis.url(m[0])) return undefined;
		return { type: 'customEmoji', raw: m[0], name: m[1] };
	},
	renderer(token) {
		const url = customEmojis.url(`:${token.name}:`) ?? '';
		return `<img class="emoji" src="${url}" alt=":${token.name}:" title=":${token.name}:">`;
	}
};

marked.setOptions({ gfm: true, breaks: true });
marked.use({ extensions: [spoilerExtension, customEmojiExtension] }); // ||spoiler||, :custom_emoji:

export function renderMarkdown(source: string): string {
	// The pictures for :name: (read reactively: text re-renders once the list arrives).
	if (browser && /:[a-z0-9_]{2,32}:/.test(source ?? '')) {
		customEmojis.load();
		void customEmojis.list;
	}
	const html = marked.parse(source ?? '', { async: false }) as string;
	if (!browser) return html;
	// tabindex and role let a keyboard reach a spoiler (and press Enter to show it).
	return DOMPurify.sanitize(html, { USE_PROFILES: { html: true }, ADD_ATTR: ['tabindex', 'role'] });
}
