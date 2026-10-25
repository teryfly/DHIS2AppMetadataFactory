<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiAsync, FuiField, FuiNotice, FuiSwitch } from '@/fui';
import FuiActionButton from '@/fui/FuiActionButton.vue';
import { ACTION } from '@/api/actions';
import { useSystemSettings } from '@/api/queries';
import { useSaveSystemSettings } from '@/api/mutations';
import { pushSuccess } from '@/lib/toast';
import { translateErrorCode } from '@/i18n';
import { channelLabel, stageTypeLabel } from '@/lib/labels';

/**
 * `GET /settings` answers a page envelope; the typed half is `values` (S5IF-FLD10). Reading the
 * envelope as if it were the settings object is what let five fabricated defaults stand in for
 * server values on this screen.
 */
type Channel = { type?: string; target?: string; secretRef?: string };
type Values = {
  idempotencyRetentionHours?: number;
  eventRetentionDays?: number;
  defaultGateReminderHours?: number;
  maxUploadBytes?: number;
  archivedProjectRetentionDays?: number;
  allowedUploadTypes?: string[];
  platformNotificationChannels?: Channel[];
  stageAutoContinue?: string[];
};

/** Design stages that can follow a predecessor automatically. REQUIREMENTS is excluded: it is
 * seeded READY at project creation, before any material exists. */
const AUTO_CONTINUE_STAGES = ['DOMAIN', 'DISCOVERY', 'MAPPING', 'PAGE_DESIGN', 'BASELINE_COMPILE', 'HANDOFF'] as const;

const { t } = useI18n();
const settings = useSystemSettings();
const save = useSaveSystemSettings();

const fields: { key: keyof Values; labelKey: string }[] = [
  { key: 'idempotencyRetentionHours', labelKey: 'settings.idempotencyRetention' },
  { key: 'eventRetentionDays', labelKey: 'settings.eventRetention' },
  { key: 'defaultGateReminderHours', labelKey: 'settings.gateReminder' },
  { key: 'maxUploadBytes', labelKey: 'settings.maxUpload' },
  { key: 'archivedProjectRetentionDays', labelKey: 'settings.archivedRetention' },
];

const form = reactive<Record<string, string>>({});
const loaded = ref<Record<string, string>>({});
const autoContinue = reactive<Record<string, boolean>>({});
const loadedAuto = ref<Record<string, boolean>>({});

const values = computed<Values>(() => {
  const envelope = settings.data.value as unknown as { values?: Values } | undefined;
  return envelope?.values ?? {};
});
const channels = computed<Channel[]>(() => values.value.platformNotificationChannels ?? []);

watch(values, (v) => {
  const next: Record<string, string> = {};
  for (const f of fields) next[f.key] = v[f.key] == null ? '' : String(v[f.key]);
  for (const key of Object.keys(form)) delete form[key];
  Object.assign(form, next);
  loaded.value = next;
  const selected = new Set(v.stageAutoContinue ?? []);
  for (const stage of AUTO_CONTINUE_STAGES) autoContinue[stage] = selected.has(stage);
  loadedAuto.value = Object.fromEntries(AUTO_CONTINUE_STAGES.map((stage) => [stage, selected.has(stage)]));
}, { immediate: true });

// Only keys the operator actually moved go back, and an emptied field is not a value: sending
// `Number('')` would write 0, which S1 reads as a real retention period.
function changedBody(): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  for (const f of fields) {
    const raw = (form[f.key] ?? '').trim();
    if (raw === '' || raw === loaded.value[f.key]) continue;
    body[f.key] = Number(raw);
  }
  const selected = AUTO_CONTINUE_STAGES.filter((stage) => autoContinue[stage]);
  const before = AUTO_CONTINUE_STAGES.filter((stage) => loadedAuto.value[stage]);
  if (selected.join(',') !== before.join(',')) body.stageAutoContinue = selected;
  return body;
}

const dirty = computed(() => Object.keys(changedBody()).length > 0);

const saveFailedText = computed(() => {
  const err = save.error.value as { errorCode?: string; message?: string } | null;
  return translateErrorCode(err?.errorCode ?? '', err?.message ?? '');
});

function onSave(): void {
  const body = changedBody();
  if (Object.keys(body).length === 0) return;
  save.mutate(
    body as never,
    {
      onSuccess: () => {
        loaded.value = { ...form };
        loadedAuto.value = Object.fromEntries(AUTO_CONTINUE_STAGES.map((stage) => [stage, !!autoContinue[stage]]));
        pushSuccess(t('settings.saved'));
      },
    },
  );
}
</script>

<template>
  <div class="sv">
    <FuiAsync :loading="settings.isLoading.value" :error="settings.isError.value" @retry="settings.refetch()">
      <FuiCard :title="t('settings.title')">
        <FuiField v-for="f in fields" :key="f.key" v-model="form[f.key]" type="number" :label="t(f.labelKey)" />
        <p class="sv__muted">{{ t('settings.allowedTypes') }}：{{ (values.allowedUploadTypes ?? []).join(', ') || '—' }}</p>
      </FuiCard>

      <FuiCard :title="t('settings.autoContinueTitle')">
        <p class="sv__muted">{{ t('settings.autoContinueHint') }}</p>
        <div v-for="stage in AUTO_CONTINUE_STAGES" :key="stage" class="sv__auto">
          <span>{{ stageTypeLabel(stage) }}</span>
          <FuiSwitch v-model="autoContinue[stage]" :label="stageTypeLabel(stage)" />
        </div>
      </FuiCard>

      <FuiCard :title="t('settings.channels')">
        <p v-if="!channels.length" class="sv__muted">{{ t('settings.noChannels') }}</p>
        <div v-for="(c, i) in channels" :key="i" class="sv__chan">
          <span class="sv__type">{{ channelLabel(c.type) }}</span>
          <span class="sv__target">{{ c.target ?? '—' }}</span>
          <span v-if="c.secretRef" class="sv__ref">{{ c.secretRef }}</span>
        </div>
        <p class="sv__muted">{{ t('settings.channelsReadOnly') }}</p>
      </FuiCard>

      <FuiNotice v-if="save.isError.value" tone="danger" :text="saveFailedText" />

      <!-- UM-BR-01: the save is gated by `settings.manage`, not by "the page rendered". -->
      <FuiActionButton
        platform
        :action="ACTION.settingsManage"
        variant="primary"
        block
        :loading="save.isPending.value"
        :disabled="!dirty"
        :label="t('settings.save')"
        @click="onSave"
      />
      <p v-if="!dirty" class="sv__muted">{{ t('settings.nothingChanged') }}</p>
    </FuiAsync>
  </div>
</template>

<style scoped>
.sv {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.sv__muted {
  margin: 8px 0 0;
  font-size: var(--fs-sm, 13px);
  color: var(--fui-ink-muted);
}
.sv__auto { display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 4px 0; min-height: var(--fui-tap-min); }
.sv__chan {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  align-items: baseline;
  padding: 6px 0;
  border-bottom: 1px solid var(--fui-hairline);
}
.sv__type {
  font-weight: 600;
}
.sv__target {
  font-family: var(--fui-font-mono, monospace);
  word-break: break-all;
}
.sv__ref {
  font-size: var(--fs-sm, 13px);
  color: var(--fui-ink-muted);
}
</style>
