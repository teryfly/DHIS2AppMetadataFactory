<script setup lang="ts">
import { Dialog as VanDialog } from 'vant';
import 'vant/es/dialog/style';

// A modal question the reader must answer (FUI answer to `<van-switch>`… `<van-dialog>`, UM-TECH-001).
// `show` is declared instead of left to attribute fall-through because the Vant dialog teleports its root.
// `title` is declared for the same reason: Vant names the dialog by writing its *text* into
// `aria-labelledby` (Dialog.mjs:210), which references no element, so the accessible name is
// supplied here as `aria-label` — the axe `aria-dialog-name` audit fails the Vant value.
// `show-cancel-button` and the button captions still fall through as attributes; `@confirm` and
// `@cancel` fall through as listeners.
//
// `beforeClose` is declared because it is the only way Vant offers to keep a dialog open after a
// confirm — the component has **no `close-on-confirm` prop**, so a caller that passes one is silently
// ignored and every tap closes the dialog, throwing away what the operator typed. Vant calls it with
// the action and closes only if the return value is truthy (utils/interceptor.mjs), so "may I close"
// is the question. A sign-in dialog needs to stay open when the credentials were wrong.
const show = defineModel<boolean>('show', { default: false });
defineProps<{
  title?: string;
  label?: string;
  beforeClose?: (action: string) => boolean;
}>();
</script>

<template>
  <VanDialog
    v-model:show="show"
    class="fui-dialog"
    :title="title"
    :before-close="beforeClose"
    :aria-label="label ?? title"
  >
    <slot />
  </VanDialog>
</template>

<style scoped>
.fui-dialog :deep(.van-dialog) { background: var(--fui-surface); }
.fui-dialog :deep(.van-dialog__message) { color: var(--fui-text); }
</style>
