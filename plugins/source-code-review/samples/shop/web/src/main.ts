import { mount } from 'svelte';
import App from './App.svelte';
import { api } from '$lib/api';

mount(App, { target: document.body, props: { api } });
