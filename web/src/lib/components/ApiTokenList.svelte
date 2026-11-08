<script lang="ts">
	import PersonName from '$lib/components/PersonName.svelte';
	import type { ApiToken } from '$lib/api';
	import { api, ApiError } from '$lib/api';
	import { Avatar, Button, Card, Icon, IconButton } from '$lib/m3';
	import { confirmAction } from '$lib/confirm.svelte';
	import { snackbar } from '$lib/snackbar.svelte';
	import { relativeTime, absoluteTime } from '$lib/format';

	/** API tokens with revoke and remove; `everyone` shows whose each one is (admins). */
	interface Props {
		tokens: ApiToken[];
		everyone?: boolean;
		onchange: () => void;
		oncreate?: () => void;
	}
	let { tokens, everyone = false, onchange, oncreate }: Props = $props();

	function state(t: ApiToken): { label: string; tone: string } | null {
		if (t.revoked_at) return { label: 'Revoked', tone: 'bg-error-container text-on-error-container' };
		if (t.expires_at && new Date(t.expires_at).getTime() <= Date.now()) return { label: 'Expired', tone: 'bg-surface-container-highest text-on-surface-variant' };
		return null;
	}
	const scopeText = (t: ApiToken) => (t.scopes.length > 4 ? `${t.scopes.slice(0, 4).join(', ')} +${t.scopes.length - 4}` : t.scopes.join(', '));

	async function revoke(t: ApiToken) {
		const whose = everyone && t.user ? ` (${t.user.display_name}'s)` : '';
		if (!(await confirmAction({ title: `Revoke “${t.name}”${whose}?`, message: 'Anything using it stops working straight away. This can’t be undone.', confirmLabel: 'Revoke', danger: true }))) return;
		try {
			await api().apiTokens.revoke(t.id);
			snackbar.show(`Revoked “${t.name}”`);
			onchange();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not revoke the token');
		}
	}
	async function remove(t: ApiToken) {
		try {
			await api().apiTokens.remove(t.id);
			onchange();
		} catch (err) {
			snackbar.error(err instanceof ApiError ? err.message : 'Could not remove the token');
		}
	}
</script>

<div class="flex flex-col gap-3">
	{#each tokens as t (t.id)}
		{@const st = state(t)}
		<Card variant="outlined" class="flex items-center gap-4 p-4 {st ? 'opacity-60' : ''}">
			<span class="flex size-10 shrink-0 items-center justify-center rounded-md bg-secondary-container text-on-secondary-container"><Icon name="key" /></span>
			<div class="min-w-0 flex-1">
				<span class="flex flex-wrap items-center gap-2">
					<span class="truncate title-small text-on-surface">{t.name}</span>
					<code class="rounded-full bg-surface-container-highest px-2 py-0.5 font-mono text-[11.5px] text-on-surface-variant">{t.prefix}…</code>
					{#if st}<span class="rounded-full px-2 py-0.5 label-small {st.tone}">{st.label}</span>{/if}
					{#if everyone && t.user}
						<span class="flex items-center gap-1 body-small text-on-surface-variant"><Avatar name={t.user.display_name} src={t.user.avatar_url} size={18} /><PersonName name={t.user.display_name} /></span>
					{/if}
				</span>
				<span class="block truncate body-small text-on-surface-variant" title={t.scopes.join(', ')}>
					{scopeText(t)} · {t.project_ids ? t.projects.join(', ') : 'every project'}
				</span>
				<span class="block body-small text-on-surface-variant">
					<span title={absoluteTime(t.created_at)}>Made {relativeTime(t.created_at)}</span>
					· {#if t.last_used_at}<span title={`${absoluteTime(t.last_used_at)}${t.last_used_ip ? ` from ${t.last_used_ip}` : ''}`}>last used {relativeTime(t.last_used_at)}</span>{:else}never used{/if}
					{#if !t.revoked_at}· {#if t.expires_at}<span title={absoluteTime(t.expires_at)}>{st ? 'expired' : 'expires'} {relativeTime(t.expires_at)}</span>{:else}doesn't expire{/if}{/if}
				</span>
			</div>
			{#if t.revoked_at || st}
				<IconButton icon="delete" label="Remove from the list" onclick={() => remove(t)} />
			{:else}
				<Button variant="text" icon="block" onclick={() => revoke(t)}>Revoke</Button>
			{/if}
		</Card>
	{:else}
		<div class="flex flex-col items-start gap-3 rounded-lg border border-dashed border-outline-variant p-6">
			<p class="body-medium text-on-surface">{everyone ? 'Nobody has made an API token yet.' : 'You have no API tokens yet.'}</p>
			<p class="body-small text-on-surface-variant">
				Tokens let scripts, CI pipelines and other tools use Castor's API as you, limited to the scopes and projects you pick.
			</p>
			{#if oncreate}<Button variant="tonal" icon="add" onclick={oncreate}>New token</Button>{/if}
		</div>
	{/each}
</div>
