<script setup lang="ts">
import { ref, computed, onBeforeUnmount } from 'vue';
import { Icon } from 'vant';
import 'vant/es/icon/style';

const props = withDefaults(
  defineProps<{ tone?: 'primary' | 'danger'; text: string; disabled?: boolean }>(),
  { tone: 'primary', disabled: false },
);
const emit = defineEmits<{ (e: 'confirm'): void }>();

const track = ref<HTMLElement | null>(null);
const x = ref(0);
const dragging = ref(false);
const done = ref(false);
let startX = 0;
let pointerId = -1;

const maxTravel = computed(() => (track.value ? track.value.clientWidth - 64 : 240));

function onDown(e: PointerEvent): void {
  if (props.disabled || done.value) return;
  dragging.value = true;
  startX = e.clientX - x.value;
  pointerId = e.pointerId;
  (e.target as HTMLElement).setPointerCapture?.(pointerId);
}

function onMove(e: PointerEvent): void {
  if (!dragging.value) return;
  x.value = Math.max(0, Math.min(maxTravel.value, e.clientX - startX));
}

function onUp(): void {
  if (!dragging.value) return;
  dragging.value = false;
  if (x.value >= maxTravel.value - 6) {
    done.value = true;
    x.value = maxTravel.value;
    emit('confirm');
  } else {
    x.value = 0;
  }
}

onBeforeUnmount(() => {
  dragging.value = false;
});
</script>

<!-- Slide-to-confirm for irreversible writes (UM-DEP-003, UM-AC-06). A plain confirm button is
     rendered as the keyboard / accessibility fallback (UM-UX-002). -->
<template>
  <div class="fui-slide" :class="{ 'fui-slide--danger': tone === 'danger', 'fui-slide--done': done, 'fui-slide--disabled': disabled }">
    <div ref="track" class="fui-slide__track">
      <span class="fui-slide__label">{{ done ? '✓ ' + $t('slide.confirm') : text }}</span>
      <div class="fui-slide__handle" :style="{ transform: `translateX(${x}px)` }" @pointerdown="onDown" @pointermove="onMove" @pointerup="onUp" @pointercancel="onUp" role="slider" aria-label="slide to confirm" tabindex="0">
        <Icon name="arrow" />
      </div>
    </div>
    <button v-if="!done && !disabled" class="fui-slide__fallback" @click="done = true; emit('confirm')">{{ $t('common.confirm') }}</button>
  </div>
</template>

<style scoped>
.fui-slide__track { position: relative; height: 52px; background: var(--fui-surface-2); border-radius: var(--fui-radius-pill); overflow: hidden; touch-action: none; }
.fui-slide--danger .fui-slide__track { background: var(--fui-danger-bg); }
.fui-slide__label { position: absolute; inset: 0; display: flex; align-items: center; justify-content: center; color: var(--fui-text-secondary); font-size: var(--fui-fs-sm); pointer-events: none; }
.fui-slide--danger .fui-slide__label { color: var(--fui-danger); font-weight: 600; }
.fui-slide__handle { position: absolute; left: 4px; top: 4px; width: 44px; height: 44px; border-radius: 50%; background: var(--fui-primary); color: #fff; display: flex; align-items: center; justify-content: center; cursor: grab; box-shadow: var(--fui-shadow-2); transition: transform 0.05s linear; }
.fui-slide--danger .fui-slide__handle { background: var(--fui-danger); }
.fui-slide--done .fui-slide__handle { background: var(--fui-success); }
.fui-slide--disabled { opacity: 0.5; }
.fui-slide--disabled .fui-slide__handle { cursor: not-allowed; }
.fui-slide__fallback { display: block; width: 100%; margin-top: 8px; min-height: var(--fui-tap-min); background: none; border: 1px dashed var(--fui-border); border-radius: var(--fui-radius-btn); color: var(--fui-text-secondary); font-size: var(--fui-fs-sm); }
</style>
