<script setup lang="ts">

import { Empty, Icon, Button } from 'vant';
import 'vant/es/empty/style';
import 'vant/es/icon/style';
import 'vant/es/button/style';
withDefaults(defineProps<{ title?: string; hint?: string; actionText?: string; icon?: string }>(), { icon: 'search', title: '', hint: '', actionText: '' });
const emit = defineEmits<{ (e: 'action'): void }>();
</script>

<template>
  <!-- Empty states always suggest a next step (UM-UX-003). -->
  <div class="fui-empty">
    <Empty :image-size="80" :description="title">
      <template #image><Icon :name="icon" size="48" color="var(--fui-muted)" /></template>
    </Empty>
    <p v-if="hint" class="fui-empty__hint">{{ hint }}</p>
    <Button v-if="actionText" type="primary" round size="small" class="fui-empty__action" @click="emit('action')">{{ actionText }}</Button>
    <slot />
  </div>
</template>

<style scoped>
.fui-empty { text-align: center; padding: var(--fui-sp-8) var(--fui-sp-4); color: var(--fui-text-secondary); }
.fui-empty__hint { font-size: var(--fui-fs-sm); margin: 0 var(--fui-sp-4) var(--fui-sp-4); }
.fui-empty__action { min-height: var(--fui-tap-min); }
:deep(.van-empty__description) { color: var(--fui-text); font-size: var(--fui-fs-md); }
</style>
