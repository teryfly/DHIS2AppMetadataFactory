<script setup lang="ts">

import { Button } from 'vant';
import 'vant/es/button/style';
const props = withDefaults(
  defineProps<{
    variant?: 'primary' | 'default' | 'danger' | 'warning' | 'ghost' | 'success';
    size?: 'normal' | 'small' | 'large' | 'mini';
    block?: boolean;
    round?: boolean;
    loading?: boolean;
    disabled?: boolean;
    icon?: string;
    /** Button text. The default slot wins when both are given. */
    label?: string;
  }>(),
  { variant: 'default', size: 'normal', block: false, round: false, loading: false, disabled: false, icon: '', label: '' },
);

const emit = defineEmits<{ (e: 'click'): void }>();

function mapType(): 'primary' | 'default' | 'danger' | 'warning' | 'success' {
  switch (props.variant) {
    case 'primary':
    case 'ghost':
      return 'primary';
    case 'danger':
      return 'danger';
    case 'warning':
      return 'warning';
    case 'success':
      return 'success';
    default:
      return 'default';
  }
}
</script>

<template>
  <Button
    :type="mapType()"
    :size="size"
    :block="block"
    :round="round"
    :loading="loading"
    :disabled="disabled"
    :icon="icon"
    :plain="variant === 'ghost'"
    class="fui-button"
    @click="emit('click')"
  >
    <slot>{{ label }}</slot>
  </Button>
</template>

<style scoped>
/* touch target >= 44px (UM-UX-001) */
.fui-button { min-height: var(--fui-tap-min); min-width: var(--fui-tap-min); }
.fui-button:deep(.van-button--small) { min-height: var(--fui-tap-min); min-width: var(--fui-tap-min); }
</style>
