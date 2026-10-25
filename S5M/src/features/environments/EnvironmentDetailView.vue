<script setup lang="ts">
import { computed } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync } from '@/fui';
import { useEnvironment, useCapabilityProfile, useEnvironmentProtection } from '@/api/queries';
import { authModeLabel, envClassLabel } from '@/lib/labels';

const { t } = useI18n();
const route = useRoute();
const key = computed(() => String(route.params.key ?? ''));
const env = useEnvironment(key);
const cap = useCapabilityProfile(key);
const protection = useEnvironmentProtection(key);

const e = computed(() => env.data.value);
// Credentials are shown only as Secret references — never plaintext (UM-BR-03).
// The contract spells this as a record keyed by scope, not an array: `v-for` over an object yields
// its values, which is why the old `string[]` cast appeared to work while `.length` was undefined
// and the "nothing here" marker rendered on every environment, populated or not.
const secretRefs = computed(() => Object.entries(e.value?.secretRefs ?? {}));
</script>

<template>
  <div class="ed">
    <FuiAsync :loading="env.isLoading.value" :error="env.isError.value" @retry="env.refetch()">
      <template v-if="e">
        <FuiCard>
          <template #title>{{ e.name || e.environmentKey }}</template>
          <template #extra><FuiStatusTag tone="info" :label="envClassLabel(e.environmentClass)" /></template>
          <div class="ed__row"><span class="ed__k">{{ t('environments.baseUrl') }}</span>{{ e.baseUrl }}</div>
          <div class="ed__row"><span class="ed__k">{{ t('environments.authMode') }}</span>{{ authModeLabel(String(e.authMode ?? '')) }} · {{ t('environments.tlsRequired') }}: {{ e.tlsRequired }}</div>
          <div class="ed__row"><span class="ed__k">{{ t('environments.dhis2Version') }}</span>{{ e.dhis2Version }}</div>
        </FuiCard>

        <FuiCard :title="t('environments.secretRef')">
          <!-- No lock glyph, no mask: what is listed here is a reference name, and dressing it up as
               redacted would imply there is a value behind it that this screen is hiding (S5IF-G09). -->
          <div v-for="[scope, ref] in secretRefs" :key="scope" class="ed__secret">
            <span class="ed__scope">{{ scope }}</span>
            <code>{{ ref }}</code>
          </div>
          <p v-if="!secretRefs.length" class="ed__muted">—</p>
          <p class="ed__hint">{{ t('environments.secretRefHint') }}</p>
        </FuiCard>

        <FuiCard :title="t('environments.capabilityProfile')">
          <pre class="ed__pre" role="region" tabindex="0" :aria-label="t('environments.capabilityProfile')">{{ JSON.stringify(cap.data.value, null, 2) }}</pre>
        </FuiCard>

        <FuiCard v-if="protection.data.value" :title="t('deployments.checklist')">
          <div class="ed__prot">
            <FuiStatusTag :tone="protection.data.value.emergencyLocked ? 'danger' : 'success'" :label="protection.data.value.emergencyLocked ? t('environments.lockOn') : t('environments.lockOff')" />
            <span>{{ envClassLabel(protection.data.value.environmentClass) }}</span>
            <span>{{ t('deployments.commitWindow') }}: {{ protection.data.value.withinCommitWindow ? t('status.open') : t('status.waiting') }}</span>
            <span>{{ t('gate.approvalProgress', { n: 0, m: protection.data.value.requiredApprovals ?? 1 }) }}</span>
          </div>
          <div v-for="(cw, i) in (protection.data.value.commitWindows ?? [])" :key="i" class="ed__cw">{{ typeof cw === 'string' ? cw : JSON.stringify(cw) }}</div>
        </FuiCard>
      </template>
    </FuiAsync>
  </div>
</template>

<style scoped>
.ed { padding: var(--fui-sp-4); }
.ed__row { display: flex; gap: 12px; padding: 6px 0; font-size: var(--fui-fs-sm); }
.ed__k { color: var(--fui-text-tertiary); min-width: 100px; }
.ed__secret { display: flex; gap: 8px; align-items: baseline; font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); padding: 4px 0; }
.ed__scope { color: var(--fui-text-tertiary); font-family: inherit; min-width: 84px; }
.ed__hint { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-top: 8px; }
.ed__muted { color: var(--fui-text-tertiary); }
.ed__pre { white-space: pre-wrap; font-size: var(--fui-fs-xs); overflow: auto; max-height: 40vh; background: var(--fui-surface-2); padding: 8px; border-radius: 8px; }
.ed__prot { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; font-size: var(--fui-fs-sm); margin-bottom: 8px; }
.ed__cw { font-size: var(--fui-fs-xs); color: var(--fui-text-secondary); padding: 2px 0; }
</style>
