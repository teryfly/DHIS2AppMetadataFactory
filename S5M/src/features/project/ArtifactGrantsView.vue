<script setup lang="ts">
/*
 * FR-SEC-003 / UI-AUTH-003 — authorising a person to read PHI_PII / RESTRICTED artifacts.
 *
 * The reason is part of the record, not a courtesy: the requirement says a restricted read is
 * separately authorised *and* audited, so the form refuses to submit without one. Nothing here shows
 * a credential or a masked value (S5IF-G09) — a grant names an account, a classification, a reason
 * and an expiry.
 *
 * The classification choices are S1's own accepted pair (`identity/service.py:GRANT_CLASSIFICATIONS`,
 * enforced by `GrantCreateRequest._classification_known`); anything else is a 422, so the field is a
 * choice rather than a text box.
 */
import { computed, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiAsync, FuiBottomSheet, FuiCard, FuiCell, FuiField, FuiNotice, FuiStatusTag } from '@/fui';
import FuiActionButton from '@/fui/FuiActionButton.vue';
import { ACTION } from '@/api/actions';
import { useArtifactGrants, useUsers } from '@/api/queries';
import { useAddGrant, useRevokeGrant } from '@/api/mutations';
import { useProjectWorkflow } from '@/features/common/useProjectWorkflow';
import { pushSuccess } from '@/lib/toast';
import { translateErrorCode } from '@/i18n';
import { fmtAbs } from '@/lib/time';
import type { GrantCreateRequest, GrantView } from '@/api/types';
import { classificationLabel } from '@/lib/labels';

/** The two classifications S1 will accept on this route — read from the contract, not guessed. */
const CLASSIFICATIONS = ['PHI_PII', 'RESTRICTED'] as const;

const { t } = useI18n();
const route = useRoute();
const projectId = computed(() => String(route.params.id ?? ''));
const { workflowId } = useProjectWorkflow(projectId);

const grants = useArtifactGrants(projectId);
const users = useUsers(() => ({ limit: 100 }));

const add = useAddGrant();
const revoke = useRevokeGrant();

const rows = computed<GrantView[]>(() => grants.data.value?.items ?? []);
const people = computed(() => users.data.value?.items ?? []);
const pendingUserId = ref('');

const failure = computed(() => add.error.value ?? revoke.error.value);
const failureText = computed(() => {
  const err = failure.value as { errorCode?: string; message?: string } | null;
  return translateErrorCode(err?.errorCode ?? '', err?.message ?? '');
});

const sheet = ref(false);
const draft = reactive({ userId: '', classification: '', reason: '', expiresAt: '' });

/**
 * The body exactly as `GrantCreateRequest` defines it: `userId`, `classification`, `reason`,
 * `expiresAt`. An empty expiry is `null` (the field is nullable) rather than a key the server would
 * then have to default, and the reason is trimmed because a whitespace-only reason is not a reason.
 */
function body(): GrantCreateRequest {
  return {
    userId: draft.userId,
    classification: draft.classification,
    reason: draft.reason.trim(),
    expiresAt: draft.expiresAt ? new Date(draft.expiresAt).toISOString() : null,
  };
}

const canSubmit = computed(() => !!draft.userId && !!draft.classification && !!draft.reason.trim());

function openAdd(): void {
  draft.userId = '';
  draft.classification = '';
  draft.reason = '';
  draft.expiresAt = '';
  sheet.value = true;
}

function submitAdd(): void {
  if (!canSubmit.value) return;
  add.mutate(
    { projectId: projectId.value, body: body() },
    {
      onSuccess: () => {
        sheet.value = false;
        pushSuccess(t('admin.grantAdded'));
      },
    },
  );
}

function submitRevoke(row: GrantView): void {
  pendingUserId.value = row.userId;
  revoke.mutate(
    { projectId: projectId.value, userId: row.userId, classification: row.classification },
    {
      onSuccess: () => pushSuccess(t('admin.grantRevoked')),
      onSettled: () => (pendingUserId.value = ''),
    },
  );
}
</script>

<template>
  <div class="ag">
    <FuiCard :title="t('admin.grantsTitle')">
      <p class="ag__note">{{ t('admin.grantsNote') }}</p>
    </FuiCard>

    <FuiNotice v-if="failure" tone="danger" :text="failureText" />

    <FuiCard>
      <template #title>{{ t('admin.grantsHeading') }}</template>
      <template #extra>
        <FuiActionButton
          :workflow-id="workflowId"
          :action="ACTION.artifactGrantManage"
          size="mini"
          variant="primary"
          :label="t('admin.grantAdd')"
          @click="openAdd"
        />
      </template>
      <FuiAsync
        :loading="grants.isLoading.value"
        :error="grants.isError.value"
        :empty="!rows.length"
        :empty-title="t('admin.noGrants')"
        :empty-hint="t('admin.noGrantsHint')"
        @retry="grants.refetch()"
      >
        <FuiCell v-for="row in rows" :key="`${row.userId}:${row.classification}`">
          <div class="ag__row">
            <div class="ag__main">
              <div class="ag__who">
                <b>{{ row.subject || row.userId }}</b>
                <FuiStatusTag tone="warning" :label="classificationLabel(row.classification)" />
                <!-- `revoke_restricted` keeps the row and stamps it, so the list still carries a
                     revoked authorisation: without the state the screen would read as a grant that
                     is standing after it was taken away. -->
                <FuiStatusTag :tone="row.revoked ? 'muted' : 'success'" :label="row.revoked ? t('admin.revoked') : t('admin.active')" />
              </div>
              <!-- A grant that carries no reason is shown as nothing, not as a word that would make
                   it look complete: the reason is the auditable half of FR-SEC-003. -->
              <p class="ag__reason">{{ row.reason || '—' }}</p>
              <p class="ag__meta">
                <span>{{ row.grantedBy || '—' }}</span>
                <span>{{ row.expiresAt ? fmtAbs(row.expiresAt) : t('admin.noExpiry') }}</span>
              </p>
            </div>
            <FuiActionButton
              v-if="!row.revoked"
              :workflow-id="workflowId"
              :action="ACTION.artifactGrantManage"
              size="mini"
              variant="danger"
              :label="t('admin.revoke')"
              :loading="revoke.isPending.value && pendingUserId === row.userId"
              @click="submitRevoke(row)"
            />
            <span v-else class="ag__gone">—</span>
          </div>
        </FuiCell>
      </FuiAsync>
    </FuiCard>

    <FuiBottomSheet v-model="sheet" :title="t('admin.grantAdd')">
      <p class="ag__caption">{{ t('admin.subject') }}</p>
      <FuiAsync
        :loading="users.isLoading.value"
        :error="users.isError.value"
        :empty="!people.length"
        :empty-title="t('admin.noSubjects')"
        @retry="users.refetch()"
      >
        <div class="ag__picks">
          <button
            v-for="person in people"
            :key="person.id"
            class="ag__pick"
            :class="{ 'is-on': draft.userId === person.id }"
            @click="draft.userId = person.id"
          >
            {{ person.displayName || person.subject }}
            <span class="ag__pick-code">{{ person.subject }}</span>
          </button>
        </div>
      </FuiAsync>

      <p class="ag__caption">{{ t('admin.classification') }}</p>
      <div class="ag__picks ag__picks--row">
        <button
          v-for="c in CLASSIFICATIONS"
          :key="c"
          class="ag__pick"
          :class="{ 'is-on': draft.classification === c }"
          @click="draft.classification = c"
        >{{ classificationLabel(c) }}</button>
      </div>

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
        :help="t('admin.expiresHelp')"
      />

      <template #footer>
        <FuiActionButton
          :workflow-id="workflowId"
          :action="ACTION.artifactGrantManage"
          variant="primary"
          block
          :label="t('admin.grantAdd')"
          :disabled="!canSubmit"
          :loading="add.isPending.value"
          @click="submitAdd"
        />
      </template>
    </FuiBottomSheet>
  </div>
</template>

<style scoped>
.ag { padding: var(--fui-sp-4); }
.ag__note { margin: 0; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.ag__row { display: flex; justify-content: space-between; align-items: flex-start; gap: 8px; width: 100%; }
.ag__main { min-width: 0; }
.ag__who { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.ag__reason { margin: 4px 0 0; font-size: var(--fui-fs-sm); color: var(--fui-text); overflow-wrap: anywhere; }
.ag__meta { display: flex; gap: 10px; margin: 4px 0 0; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.ag__gone { color: var(--fui-text-tertiary); }
.ag__caption { margin: var(--fui-sp-3) 0 6px; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.ag__picks { display: flex; flex-direction: column; gap: 8px; }
.ag__picks--row { flex-direction: row; flex-wrap: wrap; }
.ag__pick { min-height: var(--fui-tap-min); border: 1px solid var(--fui-border); border-radius: var(--fui-radius-btn); background: var(--fui-surface); text-align: left; padding: 0 12px; font-size: var(--fui-fs-sm); display: flex; gap: 8px; align-items: center; }
.ag__pick.is-on { border-color: var(--fui-primary); background: var(--fui-info-bg); color: var(--fui-primary); font-weight: 600; }
.ag__pick-code { font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
</style>
