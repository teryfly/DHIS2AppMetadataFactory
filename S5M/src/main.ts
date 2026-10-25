import { createApp } from 'vue';
import { createPinia } from 'pinia';
import { VueQueryPlugin } from '@tanstack/vue-query';
import './fui/tokens.css';
import App from './App.vue';
import { router } from './router';
import { i18n } from './i18n';
import { createQueryClient } from './api/queryClient';
import { usePrefsStore } from './stores/prefs';
import { attachSessionLapseWatch } from './stores/auth';

const app = createApp(App);
const pinia = createPinia();
app.use(pinia);
// No global Vant install (UM-TECH-001/UM-NFR-02): every Vant component is imported inside the FUI
// wrapper that uses it, together with its own style, so tree-shaking drops the ~70 unused ones.
app.use(router);
app.use(i18n);
const queryClient = createQueryClient();
app.use(VueQueryPlugin, { queryClient });
// Pinia is installed above, so the store this watch creates has a pinia to attach to. A 401 from
// any cached read or write lands here and brings the sign-in panel up (UM-NET-002).
attachSessionLapseWatch(queryClient);

usePrefsStore().apply();
if (window.matchMedia) {
  window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => usePrefsStore().apply());
}

app.mount('#app');
