<script setup lang="ts">
/*
 * CP-WF-020 / CP-IAM-002 — production exemptions: the recorded waiver that lets a delivery proceed
 * in production outside the normal protection, with a reason and an expiry.
 *
 * Two things this screen refuses to do:
 *  · offer the environment as free text. A waiver is matched by name and by environment key
 *    (`identity/directory.py:active_exemption`), so a key typed from memory is a waiver that never
 *    applies. The choices come from `GET /environments`.
 *  · invent a waiver name. S1 validates it against `identity/service.py:EXEMPTION_WAIVERS` and a
 *    value outside that set is a 422, so the field is a choice list too, and leaving it unset omits
 *    the key so S1's own default is what gets recorded.
 *
 * Revocation is one DELETE whose target is named in the path, so no idempotency key is sent; the
 * grant carries one, because a retried POST that minted a second exemption would be a second waiver
 * on the record.
 */
import { computed, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiAsync, FuiBottomSheet, FuiCard, FuiCell, FuiField, FuiNotice, FuiStatusTag } from '@/fui';
import FuiActionButton from '@/fui/FuiActionButton.vue';
import { ACTION } from '@/api/actions';
import { useEnvironments, useProductionExemptions } from '@/api/queries';
import { useAddExemption, useRevokeExemption } from '@/api/mutations';
import { useProjectWorkflow } from '@/features/common/useProjectWorkflow';
import { envClassLabel, waiverLabel } from '@/lib/labels';
import { pushSuccess } from '@/lib/toast';
import { translateErrorCode } from '@/i18n';
import { fmtAbs } from '@/lib/time';
import type { ExemptionCreateRequest, ExemptionView } from '@/api/types';

/** The waivers S1 accepts today (`identity/service.py:EXEMPTION_WAIVERS`). */
const WAIVERS = ['NON_PRODUCTION_VERIFIED'] as const;

const { t } = useI18n();
const route = useRoute();
const projectId = computed(() => String(route.params.id ?? ''));
const { workflowId } = useProjectWorkflow(projectId);

const exemptions = useProductionExemptions(projectId);
const environments = useEnvironments();

const add = useAddExemption();
const revoke = useRevokeExemption();

const rows = computed<ExemptionView[]>(() => exemptions.data.value?.items ?? []);
const envRows = computed(() => environments.data.value?.items ?? []);

const failure = computed(() => add.error.value ?? revoke.error.value);
const failureText = computed(() => {
  const err = failure.value as { errorCode?: string; message?: string } | null;
  return translateErrorCode(err?.errorCode ?? '', err?.message ?? '');
});

const sheet = ref(false);
const draft = reactive({ environmentKey: '', waiver: '', reason: '', expiresAt: '' });
const canSubmit = computed(() => !!draft.reason.trim());
// Which waiver the revoke in flight names, so only that row's control reports itself busy.
const pendingId = ref('');

function openAdd(): void {
  draft.environmentKey = '';
  draft.waiver = '';
  draft.reason = '';
  draft.expiresAt = '';
  sheet.value = true;
}

/**
 * `reason` is the only field the contract requires (`docs/openapi.json` → ExemptionCreateRequest
 * `required: ["reason"]`), so an unset field is *omitted* instead of being filled in here: S1 applies
 * its own default and the re-read shows what was actually recorded. The generated types call these
 * fields non-optional because pydantic gives them defaults — the cast says what the wire allows.
 */
function body(): ExemptionCreateRequest {
  const parts: Record<string, unknown> = { reason: draft.reason.trim() };
  if (draft.waiver) parts.waiver = draft.waiver;
  if (draft.environmentKey) parts.environmentKey = draft.environmentKey;
  parts.expiresAt = draft.expiresAt ? new Date(draft.expiresAt).toISOString() : null;
  return parts as ExemptionCreateRequest;
}

function submitAdd(): void {
  if (!canSubmit.value) return;
  add.mutate(
    { projectId: projectId.value, body: body() },
    {
      onSuccess: () => {
        sheet.value = false;
        pushSuccess(t('admin.exemptionAdded'));
      },
    },
  );
}

function submitRevoke(row: ExemptionView): void {
  pendingId.value = row.id;
  revoke.mutate(
    { projectId: projectId.value, exemptionId: row.id },
    {
      onSuccess: () => pushSuccess(t('admin.exemptionRevoked')),
      onSettled: () => (pendingId.value = ''),
    },
  );
}
</script>

<template>
  <div class="ae">
    <FuiCard :title="t('admin.exemptionsTitle')">
      <p class="ae__note">{{ t('admin.exemptionsNote') }}</p>
    </FuiCard>

    <FuiNotice v-if="failure" tone="danger" :text="failureText" />

    <FuiCard>
      <template #title>{{ t('admin.exemptionsHeading') }}</template>
      <template #extra>
        <FuiActionButton
          :workflow-id="workflowId"
          :action="ACTION.productionExemptionManage"
          size="mini"
          variant="primary"
          :label="t('admin.exemptionAdd')"
          @click="openAdd"
        />
      </template>
      <FuiAsync
        :loading="exemptions.isLoading.value"
        :error="exemptions.isError.value"
        :empty="!rows.length"
        :empty-title="t('admin.noExemptions')"
        :empty-hint="t('admin.noExemptionsHint')"
        @retry="exemptions.refetch()"
      >
        <FuiCell v-for="row in rows" :key="row.id">
          <div class="ae__row">
            <div class="ae__main">
              <div class="ae__head">
                <span class="ae__waiver">{{ row.waiver ? waiverLabel(row.waiver) : '—' }}</span>
                <FuiStatusTag :tone="row.revoked ? 'muted' : 'warning'" :label="row.revoked ? t('admin.revoked') : t('admin.active')" />
              </div>
              <p class="ae__env">
                <span class="ae__k">{{ t('admin.environment') }}</span>{{ row.environmentKey || '—' }}
                <span class="ae__meta">· <span class="ae__k">{{ t('admin.grantedBy') }}</span>{{ row.grantedBy || '—' }}</span>
              </p>
              <p class="ae__reason">{{ row.reason || '—' }}</p>
              <!-- S1 gives an exemption a default window when the request sets no expiry, so an empty
                   field is read as "the server said nothing", never as "never expires". -->
              <p class="ae__meta">{{ row.expiresAt ? fmtAbs(row.expiresAt) : '—' }}</p>
            </div>
            <!-- A revoked waiver has nothing left to revoke; S1's row keeps it for the audit trail. -->
            <FuiActionButton
              v-if="!row.revoked"
              :workflow-id="workflowId"
              :action="ACTION.productionExemptionManage"
              size="mini"
              variant="danger"
              :label="t('admin.revoke')"
              :loading="revoke.isPending.value && pendingId === row.id"
              @click="submitRevoke(row)"
            />
            <span v-else class="ae__gone">—</span>
          </div>
        </FuiCell>
      </FuiAsync>
    </FuiCard>

    <FuiBottomSheet v-model="sheet" :title="t('admin.exemptionAdd')">
      <p class="ae__caption">{{ t('admin.environment') }}</p>
      <FuiAsync
        :loading="environments.isLoading.value"
        :error="environments.isError.value"
        :empty="!envRows.length"
        :empty-title="t('admin.noEnvironments')"
        :empty-hint="t('admin.noEnvironmentsHint')"
        @retry="environments.refetch()"
      >
        <div class="ae__picks">
          <button
            v-for="e in envRows"
            :key="e.environmentKey"
            class="ae__pick"
            :class="{ 'is-on': draft.environmentKey === e.environmentKey }"
            @click="draft.environmentKey = e.environmentKey"
          >
            {{ e.name || e.environmentKey }}
            <span class="ae__pick-code">{{ e.environmentKey }}</span>
            <FuiStatusTag tone="info" :label="envClassLabel(e.environmentClass)" />
          </button>
        </div>
      </FuiAsync>
      <p class="ae__hint">{{ t('admin.environmentHelp') }}</p>

      <p class="ae__caption">{{ t('admin.waiver') }}</p>
      <div class="ae__picks ae__picks--row">
        <button
          v-for="w in WAIVERS"
          :key="w"
          class="ae__pick"
          :class="{ 'is-on': draft.waiver === w }"
          @click="draft.waiver = draft.waiver === w ? '' : w"
        >{{ waiverLabel(w) }}</button>
      </div>
      <p class="ae__hint">{{ t('admin.waiverHelp') }}</p>

      <FuiField
        v-model="draft.reason"
        type="textarea"
        rows="2"
        :label="t('admin.reason')"
        :placeholder="t('admin.reasonHint')"
        :help="t('admin.reasonRequired')"
      />
      <FuiField
        v-model="draft.expiresAt"
        type="datetime-local"
        :label="t('admin.expiresAt')"
        :help="t('admin.exemptionExpiresHelp')"
      />

      <template #footer>
        <FuiActionButton
          :workflow-id="workflowId"
          :action="ACTION.productionExemptionManage"
          variant="primary"
          block
          :label="t('admin.exemptionAdd')"
          :disabled="!canSubmit"
          :loading="add.isPending.value"
          @click="submitAdd"
        />
      </template>
    </FuiBottomSheet>
  </div>
</template>

<style scoped>
.ae { padding: var(--fui-sp-4); }
.ae__note { margin: 0; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.ae__row { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; width: 100%; }
.ae__main { min-width: 0; }
.ae__head { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ae__waiver { font-family: ui-monospace, monospace; font-size: var(--fui-fs-sm); font-weight: 600; }
.ae__env { margin: 4px 0 0; font-size: var(--fui-fs-sm); }
.ae__k { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-right: 4px; }
.ae__reason { margin: 4px 0 0; font-size: var(--fui-fs-sm); color: var(--fui-text); overflow-wrap: anywhere; }
.ae__meta { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.ae__gone { color: var(--fui-text-tertiary); }
.ae__caption { margin: var(--fui-sp-3) 0 6px; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.ae__hint { margin: 6px 0 0; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.ae__picks { display: flex; flex-direction: column; gap: 8px; }
.ae__picks--row { flex-direction: row; flex-wrap: wrap; }
.ae__pick { min-height: var(--fui-tap-min); border: 1px solid var(--fui-border); border-radius: var(--fui-radius-btn); background: var(--fui-surface); text-align: left; padding: 0 12px; font-size: var(--fui-fs-sm); display: flex; gap: 8px; align-items: center; }
.ae__pick.is-on { border-color: var(--fui-primary); background: var(--fui-info-bg); color: var(--fui-primary); font-weight: 600; }
.ae__pick-code { font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
</style>
