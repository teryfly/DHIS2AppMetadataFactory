<script setup lang="ts">
import { toasts, dismiss } from '@/lib/toast';
import { copyText } from '@/lib/clipboard';
import { translateErrorCode } from '@/i18n';
import type { ToastItem } from '@/lib/toast';

function title(t: ToastItem): string {
  if (t.errorCode) return translateErrorCode(t.errorCode, t.text, t.details);
  return t.text ?? '';
}

function bgFor(kind: ToastItem['kind']): string {
  switch (kind) {
    case 'error':
      return 'var(--fui-danger-bg)';
    case 'warn':
      return 'var(--fui-warning-bg)';
    case 'success':
      return 'var(--fui-success-bg)';
    default:
      return 'var(--fui-info-bg)';
  }
}
function fgFor(kind: ToastItem['kind']): string {
  switch (kind) {
    case 'error':
      return 'var(--fui-danger)';
    case 'warn':
      return 'var(--fui-warning)';
    case 'success':
      return 'var(--fui-success)';
    default:
      return 'var(--fui-info)';
  }
}
</script>

<template>
  <Teleport to="body">
    <div class="fui-toasts" aria-live="assertive">
      <transition-group name="fui-toast">
        <div v-for="t in toasts" :key="t.id" class="fui-toast" :style="{ background: bgFor(t.kind), color: fgFor(t.kind) }">
          <div class="fui-toast__main">
            <span class="fui-toast__glyph" aria-hidden="true">{{ t.kind === 'success' ? '✓' : t.kind === 'error' ? '✕' : '!' }}</span>
            <span class="fui-toast__title">{{ title(t) }}</span>
          </div>
          <div v-if="t.correlationId" class="fui-toast__foot">
            <button class="fui-toast__copy" @click="copyText(t.correlationId!)">{{ $t('common.copyCorrelationId') }}</button>
          </div>
          <button class="fui-toast__close" :aria-label="$t('a11y.closeDialog')" @click="dismiss(t.id)">×</button>
        </div>
      </transition-group>
    </div>
  </Teleport>
</template>

<style scoped>
.fui-toasts { position: fixed; top: calc(var(--fui-safe-top) + var(--fui-sp-2)); left: var(--fui-sp-3); right: var(--fui-sp-3); z-index: 3000; display: flex; flex-direction: column; gap: 8px; pointer-events: none; }
.fui-toast { position: relative; pointer-events: auto; border-radius: var(--fui-radius-card); padding: var(--fui-sp-3) var(--fui-sp-4); box-shadow: var(--fui-shadow-2); font-size: var(--fui-fs-sm); }
.fui-toast__main { display: flex; gap: 8px; align-items: flex-start; }
.fui-toast__glyph { font-weight: 700; }
.fui-toast__title { flex: 1; }
.fui-toast__foot { margin-top: 6px; text-align: right; }
.fui-toast__copy { border: 0; background: none; color: inherit; font-size: var(--fui-fs-xs); text-decoration: underline; min-height: var(--fui-tap-min); }
.fui-toast__close { position: absolute; top: 4px; right: 8px; border: 0; background: none; color: inherit; font-size: 18px; line-height: 1; }
.fui-toast-enter-active, .fui-toast-leave-active { transition: opacity 0.2s, transform 0.2s; }
.fui-toast-enter-from, .fui-toast-leave-to { opacity: 0; transform: translateY(-8px); }
</style>
