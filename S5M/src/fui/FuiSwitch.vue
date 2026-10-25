<script setup lang="ts">
import { Switch as VanSwitch } from 'vant';
import 'vant/es/switch/style';

// On/off toggle (FUI answer to `<van-switch>`). `v-model`, `size` and `disabled` fall through.
//
// `label` exists because a switch has no visible text of its own in every layout: Vant renders a
// `role="switch"` div, and a screen reader reading "switch, off" with nothing named is not an
// answer. Callers that do show a caption pass it here as well — the caption is a sibling, not an
// association, so it carries no `for`/`id` link to the control.
defineProps<{ label?: string }>();
</script>

<template>
  <VanSwitch class="fui-switch" :aria-label="label" />
</template>

<style scoped>
.fui-switch { --van-switch-on-background: var(--fui-primary); }
</style>
