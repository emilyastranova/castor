import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { browser } from '$app/environment';
import { spoilerExtension } from './spoilers';

marked.setOptions({ gfm: true, breaks: true });
marked.use({ extensions: [spoilerExtension] }); // ||spoiler||

export function renderMarkdown(source: string): string {
	const html = marked.parse(source ?? '', { async: false }) as string;
	if (!browser) return html;
	// tabindex and role let a keyboard reach a spoiler (and press Enter to show it).
	return DOMPurify.sanitize(html, { USE_PROFILES: { html: true }, ADD_ATTR: ['tabindex', 'role'] });
}
