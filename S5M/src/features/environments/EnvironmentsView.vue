<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiButton, FuiAsync, FuiBottomSheet, FuiActionButton, FuiChecklist, FuiField, FuiNotice } from '@/fui';
import { useEnvironments, useExpiringCredentials, usePlatformActions } from '@/api/queries';
import { useDisableEnvironment, useEmergencyLock, useProbeCapabilities, useTestConnection } from '@/api/mutations';
import { ACTION, indexActions, resolveAvailability } from '@/api/actions';
import { envClassLabel, stageStatusLabel } from '@/lib/labels';
import { REQUIRED_CREDENTIAL_SCOPES } from '@/lib/enums';
import EnvironmentFormSheet from './EnvironmentFormSheet.vue';
import type * as T from '@/api/types';

const { t } = useI18n();
const router = useRouter();
const envs = useEnvironments();
const expiring = useExpiringCredentials();
const platformActions = usePlatformActions();
const testConn = useTestConnection();
const lock = useEmergencyLock();
const probe = useProbeCapabilities();
const disable = useDisableEnvironment();

const list = computed(() => envs.data.value?.items ?? []);
const expiringKeys = computed(() => new Set(((expiring.data.value as { items?: { environmentKey?: string }[] })?.items ?? []).map((e) => e.environmentKey)));

/**
 * The emergency lock is not a status: S1's `EnvironmentStatus` is ACTIVE or DISABLED, and the lock
 * is answered per environment as `details.locked` on the very action this card's button is gated
 * by. Reading `status === 'LOCKED'` would show every environment as unlocked.
 */
const availability = computed(() => indexActions(platformActions.data.value));
function isLocked(key: string): boolean {
  return resolveAvailability(availability.value, ACTION.environmentEmergencyLock, key).details?.locked === true;
}

const formSheet = ref(false);
const formTarget = ref<T.EnvironmentView | null>(null);
function openForm(environment: T.EnvironmentView | null): void {
  formTarget.value = environment;
  formSheet.value = true;
}
function onSaved(): void {
  formTarget.value = null;
  // Registering an environment is not the same as being able to use one: the validation stage
  // refuses any environment that never passed a connection test (UM-SET-003).
  savedHint.value = true;
  void envs.refetch();
}

const savedHint = ref(false);

const testResult = ref<Record<string, { ok: boolean; detail: string }>>({});
async function onTest(key: string): Promise<void> {
  try {
    const res = (await testConn.mutateAsync({ environmentKey: key, body: { requiredScopes: [...REQUIRED_CREDENTIAL_SCOPES] } })) as Record<string, unknown>;
    // S1 answers with `state` per check (PASSED/FAILED/SKIPPED) and `missingAuthorities` as a
    // map of scope to the authorities that lacked it — the shape the operator has to read.
    const checks = (res.checks ?? []) as { name?: string; state?: string; detail?: string }[];
    const missing = Object.entries((res.missingAuthorities ?? {}) as Record<string, unknown>);
    // `state` is S4's spelling: PASSED / FAILED / SKIPPED. A skipped check is not a failure, and
    // reporting it as one would teach the operator to distrust the panel.
    const ok = Boolean(res.ok) && checks.every((c) => c.state !== 'FAILED');
    const detail = [
      res.testedAt ? `${t('environments.testedAt')}: ${String(res.testedAt)}` : '',
      ...checks.map((c) => `${c.state === 'PASSED' ? '✓' : c.state === 'SKIPPED' ? '–' : '✕'} ${c.name ?? ''}${c.detail ? ` — ${c.detail}` : ''}`),
      ...missing.map(([scope, authorities]) => `${t('environments.missingAuthorities')} ${scope}: ${String(authorities)}`),
    ].filter(Boolean).join('\n');
    testResult.value = { ...testResult.value, [key]: { ok, detail } };
  } catch {
    /* surfaced by the global error toast (api/queryClient.ts) */
  }
}

const disableSheet = ref(false);
const disableTarget = ref('');
const disableReason = ref('');
function openDisable(key: string): void {
  disableTarget.value = key;
  disableReason.value = '';
  disableSheet.value = true;
}
function confirmDisable(): void {
  disable.mutate(
    { environmentKey: disableTarget.value, reason: disableReason.value.trim() || undefined },
    { onSuccess: () => { disableSheet.value = false; void envs.refetch(); } },
  );
}

/** The probe answers with the version S4 read off the instance; it is worth naming, not just "ok". */
const probeResult = ref('');
async function onProbe(key: string): Promise<void> {
  try {
    const res = (await probe.mutateAsync({ environmentKey: key })) as Record<string, unknown>;
    probeResult.value = `${key}: ${String(res.version ?? '—')}`;
  } catch {
    /* surfaced by the global error toast (api/queryClient.ts) */
  }
}

const lockSheet = ref(false);
const lockTarget = ref<{ key: string; lock: boolean }>({ key: '', lock: true });
const lockReason = ref('');
function openLock(key: string): void {
  lockTarget.value = { key, lock: !isLocked(key) };
  lockReason.value = '';
  lockSheet.value = true;
}
function confirmLock(): void {
  // S1 requires a reason to lock and none to release; asking for one on the way out would turn
  // the recovery path into a second form nobody fills in honestly
  if (lockTarget.value.lock && !lockReason.value.trim()) return;
  lock.mutate(
    { environmentKey: lockTarget.value.key, locked: lockTarget.value.lock, reason: lockReason.value.trim() },
    { onSuccess: () => { lockSheet.value = false; void envs.refetch(); } },
  );
}
</script>

<template>
  <div class="env">
    <div class="env__bar">
      <FuiActionButton :action="ACTION.environmentCreate" platform variant="primary" round :label="t('environments.new')" @click="openForm(null)" />
    </div>

    <FuiNotice v-if="savedHint" tone="info">
      <span>{{ t('environments.savedTestFirst') }}</span>
      <FuiButton size="mini" :label="t('common.close')" @click="savedHint = false" />
    </FuiNotice>

    <FuiAsync
      :loading="envs.isLoading.value"
      :empty="!list.length"
      :empty-title="t('environments.emptyTitle')"
      :empty-hint="t('environments.emptyHint')"
      :empty-action="t('environments.new')"
      @action="openForm(null)"
    >
      <FuiCard v-for="e in list" :key="e.environmentKey" :title="e.name || e.environmentKey">
        <template #extra>
          <div class="env__tags">
            <FuiStatusTag tone="info" :label="envClassLabel(e.environmentClass)" />
            <FuiStatusTag :tone="e.status === 'DISABLED' ? 'muted' : 'success'" :label="stageStatusLabel(String(e.status ?? ''))" />
            <FuiStatusTag v-if="isLocked(e.environmentKey)" tone="danger" :label="t('environments.lockedNow')" />
          </div>
        </template>
        <div class="env__code">
          <span class="env__eyebrow">{{ e.environmentKey }}</span>
          <span v-if="expiringKeys.has(e.environmentKey)" class="env__warn">⚠ {{ t('environments.expiring') }}</span>
        </div>
        <div class="env__actions">
          <FuiActionButton :action="ACTION.environmentEdit" :target-id="e.environmentKey" platform size="small" :label="t('environments.edit')" @click="openForm(e)" />
          <!-- UM-BR-01: a test and a lock are state-changing calls S1 may refuse, so they are
               gated by the same answer as every other control — not drawn as free buttons -->
          <FuiActionButton :action="ACTION.environmentTestConnection" :target-id="e.environmentKey" platform size="small" :label="t('environments.testConnection')" @click="onTest(e.environmentKey)" />
          <FuiActionButton
            :action="ACTION.environmentEmergencyLock"
            :target-id="e.environmentKey"
            platform
            size="small"
            :variant="isLocked(e.environmentKey) ? 'primary' : 'danger'"
            :label="t(isLocked(e.environmentKey) ? 'environments.releaseLock' : 'environments.emergencyLock')"
            @click="openLock(e.environmentKey)"
          />
          <FuiActionButton
            :action="ACTION.environmentProbeCapabilities"
            :target-id="e.environmentKey"
            platform
            size="small"
            :label="t('environments.probeCapabilities')"
            @click="onProbe(e.environmentKey)"
          />
          <FuiActionButton
            :action="ACTION.environmentDisable"
            :target-id="e.environmentKey"
            platform
            size="small"
            :label="t('environments.disable')"
            @click="openDisable(e.environmentKey)"
          />
          <FuiButton size="small" :label="t('common.details')" @click="router.push(`/environments/${e.environmentKey}`)" />
        </div>
        <FuiChecklist
          v-if="testResult[e.environmentKey]"
          :items="[{ key: 'r', label: testResult[e.environmentKey]!.ok ? t('environments.connOk') : t('environments.connFail'), state: testResult[e.environmentKey]!.ok ? 'pass' : 'fail' }]"
        />
        <pre v-if="testResult[e.environmentKey]?.detail" class="env__detail">{{ testResult[e.environmentKey]!.detail }}</pre>
      </FuiCard>
    </FuiAsync>

    <FuiBottomSheet v-model="lockSheet" :title="lockTarget.lock ? t('environments.lockTitle') : t('environments.releaseLockTitle')">
      <FuiField v-model="lockReason" type="textarea" rows="2" :label="t('environments.lockReason')" :placeholder="lockTarget.lock ? t('common.required') : ''" />
      <template #footer>
        <FuiButton
          :variant="lockTarget.lock ? 'danger' : 'primary'"
          block
          :label="t('common.confirm')"
          :disabled="lockTarget.lock && !lockReason.trim()"
          :loading="lock.isPending.value"
          @click="confirmLock"
        />
      </template>
    </FuiBottomSheet>

    <FuiBottomSheet v-model="disableSheet" :title="t('environments.disableTitle')">
      <p class="env__warn">{{ t('environments.disableBody') }}</p>
      <FuiField v-model="disableReason" type="textarea" rows="2" :label="t('environments.disableReason')" />
      <template #footer>
        <FuiButton variant="danger" block :label="t('common.confirm')" :loading="disable.isPending.value" @click="confirmDisable" />
      </template>
    </FuiBottomSheet>

    <p v-if="probeResult" class="env__detail">{{ probeResult }}</p>

    <EnvironmentFormSheet v-model="formSheet" :environment="formTarget" @saved="onSaved" />
  </div>
</template>

<style scoped>
.env { padding: var(--fui-sp-4); }
.env__bar { display: flex; justify-content: flex-end; margin-bottom: var(--fui-sp-3); }
.env__tags { display: flex; gap: 6px; }
.env__code { display: flex; gap: 8px; align-items: center; }
.env__eyebrow { font-family: var(--fui-font-mono, monospace); font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); }
.env__warn { color: var(--fui-warning); font-size: var(--fui-fs-xs); }
.env__actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.env__detail { white-space: pre-wrap; font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); margin: 6px 0 0; }
</style>
