<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiButton, FuiStatusTag, FuiNotice } from '@/fui';
import { useInputs, useLlmLog, useLlmExchange } from '@/api/queries';
import { parseStatusLabel } from '@/lib/labels';
import { fmtRelTime } from '@/lib/time';
import { formatBytes } from '@/lib/media';

const { t, locale } = useI18n();
const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.id ?? ''));
const artifactId = computed(() => String(route.params.artifactId ?? ''));
const inputs = useInputs(projectId);
const log = useLlmLog(projectId, artifactId);
const openId = ref<string | null>(null);
const detail = useLlmExchange(projectId, artifactId, computed(() => openId.value));

const input = computed(() =>
  (inputs.data.value?.items ?? []).find((item) => String(item.artifactId) === artifactId.value),
);
const items = computed(() => log.data.value?.items ?? []);
const status = computed(() => log.data.value?.parseStatus ?? input.value?.parseStatus ?? '');
const d = computed(() => detail.data.value);
const lang = computed<'zh' | 'en'>(() => (locale.value === 'zh' ? 'zh' : 'en'));

function tone(s?: string): 'success' | 'danger' | 'warning' | 'muted' | 'info' {
  switch (s) {
    case 'OK':
      return 'success';
    case 'ERROR':
      return 'danger';
    case 'IN_FLIGHT':
      return 'info';
    default:
      return 'muted';
  }
}

function duration(ms?: number | null): string {
  if (ms === null || ms === undefined) return '';
  return ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function toggle(id: string): void {
  openId.value = openId.value === id ? null : id;
}

function back(): void {
  router.push(`/projects/${projectId.value}/inputs`);
}
</script>

<template>
  <div class="llm">
    <FuiCard :title="t('llmLog.title')">
      <template #extra>
        <FuiButton size="small" variant="ghost" :label="t('llmLog.back')" @click="back" />
      </template>

      <p class="llm__sub">{{ input?.filename ?? artifactId }}</p>
      <div class="llm__head">
        <FuiStatusTag :tone="status === 'PARSING' ? 'info' : 'muted'" :label="parseStatusLabel(status)" />
        <span v-if="status === 'PARSING'" class="llm__muted">{{ t('llmLog.polling') }}</span>
      </div>

      <FuiNotice v-if="log.isError.value" tone="danger">{{ t('llmLog.loadFailed') }}</FuiNotice>
      <p v-else-if="!items.length" class="llm__muted">{{ t('llmLog.empty') }}</p>

      <div v-for="item in items" :key="String(item.exchangeId)" class="llm__item">
        <div class="llm__row">
          <strong>#{{ item.sequence }}</strong>
          <FuiStatusTag :tone="tone(item.status)" :label="t(`llmLog.exchangeStatus.${item.status}`)" />
          <span class="llm__muted">{{ item.model }}</span>
          <span class="llm__muted">{{ duration(item.latencyMs) }}</span>
          <span class="llm__muted">
            {{ t('llmLog.tokens', { input: item.inputTokens ?? 0, output: item.outputTokens ?? 0 }) }}
          </span>
          <span class="llm__muted">{{ fmtRelTime(item.createdAt, lang) }}</span>
        </div>

        <FuiNotice v-if="item.errorCode" tone="danger">
          {{ item.errorMessage ? `${item.errorCode}: ${item.errorMessage}` : item.errorCode }}
        </FuiNotice>

        <FuiButton
          size="small"
          variant="ghost"
          :label="openId === item.exchangeId ? t('llmLog.hide') : t('llmLog.show')"
          @click="toggle(String(item.exchangeId))"
        />

        <div v-if="openId === item.exchangeId" class="llm__detail">
          <p v-if="detail.isLoading.value" class="llm__muted">{{ t('llmLog.loading') }}</p>
          <FuiNotice v-else-if="detail.isError.value" tone="danger">{{ t('llmLog.detailFailed') }}</FuiNotice>
          <template v-else-if="d">
            <FuiNotice v-if="d.status === 'IN_FLIGHT'" tone="info">{{ t('llmLog.inFlight') }}</FuiNotice>

            <h4>{{ t('llmLog.request') }}</h4>
            <div v-for="(message, i) in d.request.messages ?? []" :key="i" class="llm__msg">
              <div class="llm__row">
                <FuiStatusTag :label="String(message.role)" />
                <span v-for="image in message.images ?? []" :key="image.label" class="llm__muted">
                  {{ t('llmLog.image', { label: image.label, mediaType: image.mediaType, size: formatBytes(image.sizeBytes) }) }}
                </span>
              </div>
              <pre>{{ message.content }}</pre>
            </div>

            <h4>{{ t('llmLog.response') }}</h4>
            <FuiNotice v-if="d.response?.storageTruncated" tone="warning">{{ t('llmLog.storageTruncated') }}</FuiNotice>
            <pre v-if="d.response?.text">{{ d.response.text }}</pre>
            <p v-else class="llm__muted">{{ t('llmLog.noResponse') }}</p>

            <h4>{{ t('llmLog.metadata') }}</h4>
            <ul class="llm__meta">
              <li>{{ t('llmLog.schema', { name: d.request.schemaName ?? '—' }) }}</li>
              <li>{{ t('llmLog.temperature', { value: d.request.temperature ?? '—' }) }}</li>
              <li>{{ t('llmLog.maxTokens', { value: d.request.maxOutputTokens ?? '—' }) }}</li>
              <li v-if="d.response?.finishReason">
                {{ t('llmLog.finishReason', { value: d.response.finishReason }) }}
              </li>
              <li v-if="d.requestId">{{ t('llmLog.requestId', { value: d.requestId }) }}</li>
              <li v-if="d.response?.usage">
                {{ t('llmLog.usage', { input: d.response.usage.inputTokens ?? 0, output: d.response.usage.outputTokens ?? 0 }) }}
              </li>
            </ul>
          </template>
        </div>
      </div>
    </FuiCard>
  </div>
</template>

<style scoped>
.llm { padding: var(--fui-sp-4); }
.llm__sub { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin: 0 0 var(--fui-sp-2); }
.llm__head { display: flex; gap: 8px; align-items: center; margin-bottom: var(--fui-sp-3); }
.llm__item { border-bottom: 1px solid var(--fui-border); padding: var(--fui-sp-3) 0; }
.llm__row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.llm__muted { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.llm__detail { margin-top: var(--fui-sp-3); }
.llm__msg { background: var(--fui-bg-subtle, #f5f7f8); border-radius: 6px; padding: 8px; margin-bottom: 8px; }
.llm__msg pre, .llm__detail pre { white-space: pre-wrap; word-break: break-word; margin: 6px 0 0; font-size: var(--fui-fs-xs); }
.llm__meta { padding-left: 18px; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
</style>
