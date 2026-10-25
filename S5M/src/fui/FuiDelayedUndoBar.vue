<script setup lang="ts">
import { ref, watch, onBeforeUnmount } from 'vue';

// "点一下建议即回答" — selection commits after a 5s undo window, or immediately when the page
// unmounts (UM-Q-002, UM-AC-03/04). This bar is presentational: the parent decides when to submit.
const props = withDefaults(defineProps<{ visible: boolean; text: string; seconds?: number; running?: boolean }>(), { seconds: 5, running: true });
const emit = defineEmits<{ (e: 'elapsed'): void; (e: 'undo'): void }>();

const left = ref(props.seconds);
let timer: ReturnType<typeof setInterval> | null = null;

function stop(): void {
  if (timer) {
    clearInterval(timer);
    timer = null;
  }
}

watch(
  () => [props.visible, props.running],
  ([vis, run]) => {
    stop();
    if (vis && run) {
      left.value = props.seconds;
      timer = setInterval(() => {
        left.value -= 1;
        if (left.value <= 0) {
          stop();
          emit('elapsed');
        }
      }, 1000);
    }
  },
  { immediate: true },
);

onBeforeUnmount(stop);
</script>

<template>
  <transition name="fui-slide-up">
    <div v-if="visible" class="fui-delay">
      <div class="fui-delay__info">
        <span class="fui-delay__text">{{ $t('delayed.selected', { text }) }}</span>
        <span class="fui-delay__cd">{{ $t('delayed.countdown', { n: left }) }}</span>
      </div>
      <button class="fui-delay__undo" @click="stop(); emit('undo')">{{ $t('delayed.undo') }}</button>
      <div class="fui-delay__bar" :style="{ width: (left / seconds) * 100 + '%' }" />
    </div>
  </transition>
</template>

<style scoped>
.fui-delay { position: sticky; bottom: calc(var(--fui-sp-2) + var(--fui-safe-bottom)); z-index: 40; background: var(--fui-info-bg); border: 1px solid var(--fui-primary); border-radius: var(--fui-radius-card); padding: var(--fui-sp-3) var(--fui-sp-4); display: flex; align-items: center; gap: var(--fui-sp-3); overflow: hidden; }
.fui-delay__info { flex: 1; display: flex; flex-direction: column; }
.fui-delay__text { font-size: var(--fui-fs-sm); font-weight: 600; color: var(--fui-text); }
.fui-delay__cd { font-size: var(--fui-fs-xs); color: var(--fui-info); }
.fui-delay__undo { min-height: var(--fui-tap-min); padding: 0 var(--fui-sp-4); border: 0; background: var(--fui-primary); color: var(--fui-on-primary); border-radius: var(--fui-radius-btn); font-size: var(--fui-fs-sm); font-weight: 600; }
.fui-delay__bar { position: absolute; left: 0; bottom: 0; height: 3px; background: var(--fui-primary); transition: width 1s linear; }
.fui-slide-up-enter-active, .fui-slide-up-leave-active { transition: transform 0.15s ease, opacity 0.15s ease; }
.fui-slide-up-enter-from, .fui-slide-up-leave-to { transform: translateY(10px); opacity: 0; }
</style>
