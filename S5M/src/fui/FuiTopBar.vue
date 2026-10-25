<script setup lang="ts">
import { useRouter } from 'vue-router';
import { NavBar } from 'vant';
import 'vant/es/nav-bar/style';
withDefaults(defineProps<{ title?: string; showBack?: boolean; subtitle?: string }>(), { showBack: false, title: '', subtitle: '' });
const emit = defineEmits<{ (e: 'back'): void }>();
const router = useRouter();
function onBack(): void {
  emit('back');
  if (window.history.length > 1) router.back();
}
</script>

<template>
  <NavBar class="fui-topbar" :title="title" :left-arrow="showBack" fixed placeholder safe-area-inset-top @click-left="onBack">
    <template v-if="subtitle" #title>
      <div class="fui-topbar__title">{{ title }}</div>
      <div class="fui-topbar__subtitle">{{ subtitle }}</div>
    </template>
    <template #right><slot name="right" /></template>
  </NavBar>
</template>

<style scoped>
.fui-topbar__title { font-size: var(--fui-fs-md); font-weight: 600; }
.fui-topbar__subtitle { font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); font-weight: 400; }
</style>
