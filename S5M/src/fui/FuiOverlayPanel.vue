<script setup lang="ts">
import { Popup as VanPopup } from 'vant';
import 'vant/es/popup/style';

// A panel that overlays the page (FUI answer to `<van-popup>`, UM-TECH-001). Used for the
// top-anchored search sheet; a confirmation flow should use FuiBottomSheet instead (UM-UX-002
// keeps one-hand reach on a phone). `v-model:show`, `position` and `style` fall through.
const show = defineModel<boolean>('show', { default: false });
</script>

<template>
  <VanPopup v-model:show="show" class="fui-overlay-panel">
    <slot />
  </VanPopup>
</template>

<style scoped>
.fui-overlay-panel :deep(.van-popup) { background: var(--fui-surface); color: var(--fui-text); }
</style>
