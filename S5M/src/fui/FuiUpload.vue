<script setup lang="ts">
import { ref } from 'vue';
import { Icon, Progress } from 'vant';
import 'vant/es/icon/style';
import 'vant/es/progress/style';

withDefaults(defineProps<{ uploading?: boolean; progress?: number; disabled?: boolean; acceptHint?: string }>(), {
  uploading: false,
  disabled: false,
  progress: 0,
  acceptHint: '',
});
const emit = defineEmits<{ (e: 'pick', file: File): void }>();

const photoInput = ref<HTMLInputElement | null>(null);
const albumInput = ref<HTMLInputElement | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

function onFile(e: Event): void {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (file) emit('pick', file);
  input.value = '';
}

defineExpose({
  /** §6.1 budgets 上传 → 选文件 at 2 clicks, so the page's main button must reach the system
      picker directly; the three-source grid below stays as the alternative entry. */
  pickFile: () => fileInput.value?.click(),
  pickPhoto: () => photoInput.value?.click(),
  pickAlbum: () => albumInput.value?.click(),
});
</script>

<!-- Three sources (camera / album / file) as one-tap big buttons (UM-REQ-001). Auto-parse is the
     parent's concern after a pick. -->
<template>
  <div class="fui-upload">
    <div class="fui-upload__grid">
      <button class="fui-upload__btn" :disabled="disabled || uploading" @click="photoInput?.click()">
        <Icon name="photograph" size="22" /><span>{{ $t('inputs.takePhoto') }}</span>
      </button>
      <button class="fui-upload__btn" :disabled="disabled || uploading" @click="albumInput?.click()">
        <Icon name="photo-o" size="22" /><span>{{ $t('inputs.fromAlbum') }}</span>
      </button>
      <button class="fui-upload__btn" :disabled="disabled || uploading" @click="fileInput?.click()">
        <Icon name="description-o" size="22" /><span>{{ $t('inputs.fromFile') }}</span>
      </button>
    </div>
    <input ref="photoInput" type="file" accept="image/*" capture="environment" class="fui-upload__input" @change="onFile" />
    <input ref="albumInput" type="file" accept="image/*" class="fui-upload__input" @change="onFile" />
    <input ref="fileInput" type="file" class="fui-upload__input" @change="onFile" />
    <p v-if="acceptHint" class="fui-upload__hint">{{ acceptHint }}</p>
    <div v-if="uploading" class="fui-upload__progress"><Progress :percentage="Math.round((progress ?? 0) * 100)" /></div>
  </div>
</template>

<style scoped>
.fui-upload__grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: var(--fui-sp-2); }
.fui-upload__btn { min-height: 64px; border: 1px solid var(--fui-border); background: var(--fui-surface); border-radius: var(--fui-radius-card); display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 4px; color: var(--fui-text); font-size: var(--fui-fs-sm); }
.fui-upload__btn:disabled { opacity: 0.5; }
.fui-upload__input { display: none; }
.fui-upload__hint { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin: 8px 0 0; }
.fui-upload__progress { margin-top: 8px; }
</style>
