<script setup lang="ts">
import { defineAsyncComponent } from 'vue';
import { isDesktop } from '@/lib/breakpoint';

/**
 * UM-CAP-003 OpenAPI viewer (desktop, M3) — the light FUI shell pages are allowed to import.
 *
 * The document kernel (`./internal/OpenApiDoc.vue`, and with it `js-yaml`) is loaded dynamically so
 * a phone never downloads it and the first-screen graph stays clean (UM-TECH-002). The shell itself
 * imports nothing heavy and nothing third-party (UM-TECH-001).
 *
 * Below the desktop breakpoint this renders *nothing*, on purpose. UM-REV-005 keeps the mobile API
 * review surface as the operation-card list the page already draws; a two-pane document reader is
 * not a shrunk-down desktop view, it is a different job. `src/test/isolation.test.ts` and the
 * read-only banner inside the core are what keep this surface from ever growing a control that
 * sends a request to a target DHIS2 instance.
 */
// Type-based props with no `withDefaults`: `error` carries `unknown` (the failed read, whatever its
// shape), and the meaningful "nothing failed" value is the absence of one — matching `FuiDiffCard`.
defineProps<{
  /** The artifact content, as text: YAML for every document S2 compiles, JSON if it ever changes. */
  doc: string | null;
  loading?: boolean;
  /** The failed read, if any — handed to `src/api/notGenerated` for the guided empty state. */
  error?: unknown;
  projectId?: string;
}>();

const OpenApiDoc = defineAsyncComponent(() => import('./internal/OpenApiDoc.vue'));
</script>

<template>
  <OpenApiDoc v-if="isDesktop" :doc="doc" :loading="loading" :error="error" :project-id="projectId" />
</template>
