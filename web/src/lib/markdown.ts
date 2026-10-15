import { marked } from 'marked';
import DOMPurify from 'dompurify';
import { browser } from '$app/environment';

marked.setOptions({ gfm: true, breaks: true });

export function renderMarkdown(source: string): string {
	const html = marked.parse(source ?? '', { async: false }) as string;
	if (!browser) return html;
	return DOMPurify.sanitize(html, { USE_PROFILES: { html: true } });
}
