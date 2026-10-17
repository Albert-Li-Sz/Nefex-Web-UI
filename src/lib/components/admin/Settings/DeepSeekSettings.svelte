<script lang="ts">
	import { getContext } from 'svelte';
	import SensitiveInput from '$lib/components/common/SensitiveInput.svelte';
	import AdminSettingField from './AdminSettingField.svelte';

	const i18n: any = getContext('i18n');
	export let apiKey = '';
	export let apiBaseUrl = 'https://api.deepseek.com';
</script>

<AdminSettingField label={`DeepSeek ${$i18n.t('API Base URL')}`}>
	<input
		class="w-full rounded-lg border border-gray-100/50 bg-gray-50/40 px-2 py-1.5 text-xs text-gray-700 outline-hidden transition-colors focus:border-blue-400 dark:border-white/[0.04] dark:bg-white/[0.03] dark:text-gray-300 dark:focus:border-blue-500"
		type="url"
		placeholder="https://api.deepseek.com"
		bind:value={apiBaseUrl}
		autocomplete="off"
	/>
</AdminSettingField>

<AdminSettingField
	label={`DeepSeek ${$i18n.t('API Key')}`}
	description={$i18n.t('DeepSeek Search and Fetch share your official DeepSeek API key.')}
>
	<SensitiveInput variant="settings" placeholder="sk-…" bind:value={apiKey} />
</AdminSettingField>
