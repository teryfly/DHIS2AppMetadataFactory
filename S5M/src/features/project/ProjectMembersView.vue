<script setup lang="ts">
/*
 * FR-SEC-004 / UI-AUTH-003 — who belongs to this project, and in what role.
 *
 * Membership is written as a *set*: S1's route is `PUT /projects/{id}/members` carrying the whole
 * list, so this screen keeps a draft of the set and sends it in one intent. A per-row add would have
 * been a read-modify-write with a lost-update window in which somebody ends up with a role and no
 * membership row.
 *
 * Availability comes from `member.manage` on the workflow's allowed-actions answer, which is where
 * S1 puts this project's own three rights (`domain/actions.py:project_actions()`): the platform
 * answer is evaluated with no project, so it would tell a project's own member manager that they may
 * not manage it.
 */
import { computed, reactive, ref } from 'vue';
import { useRoute } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiAsync, FuiBottomSheet, FuiCard, FuiCell, FuiNotice, FuiStatusTag } from '@/fui';
import FuiActionButton from '@/fui/FuiActionButton.vue';
import { ACTION } from '@/api/actions';
import { useProjectMembers, useRoles, useUsers } from '@/api/queries';
import { useRemoveMember, useReplaceMembers } from '@/api/mutations';
import { useProjectWorkflow } from '@/features/common/useProjectWorkflow';
import { pushSuccess } from '@/lib/toast';
import { translateErrorCode } from '@/i18n';
import type { MemberView } from '@/api/types';
import { roleLabel } from '@/lib/labels';

const { t } = useI18n();
const route = useRoute();
const projectId = computed(() => String(route.params.id ?? ''));
const { workflowId } = useProjectWorkflow(projectId);

const members = useProjectMembers(projectId);
// The two lists the add form chooses from are server reads, not things this screen may invent:
// accounts come from `GET /users`, role names from `GET /roles`.
const users = useUsers(() => ({ limit: 100 }));
const roles = useRoles();

const replace = useReplaceMembers();
const remove = useRemoveMember();

const rows = computed<MemberView[]>(() => members.data.value?.items ?? []);
const people = computed(() => users.data.value?.items ?? []);
const roleNames = computed(() => (roles.data.value?.items ?? []).map((r) => String(r.name ?? '')).filter(Boolean));

const failure = computed(() => replace.error.value ?? remove.error.value);
const failureText = computed(() => {
  const err = failure.value as { errorCode?: string; message?: string } | null;
  return translateErrorCode(err?.errorCode ?? '', err?.message ?? '');
});

const pendingUserId = ref('');

// ---- add one member: draft the next set, send it whole
const sheet = ref(false);
const draft = reactive({ userId: '', role: '' });
const chosen = computed(() => people.value.find((p) => p.id === draft.userId));
const canSubmit = computed(() => !!draft.userId && !!draft.role);

function openAdd(): void {
  draft.userId = '';
  draft.role = '';
  sheet.value = true;
}

function submitAdd(): void {
  const person = chosen.value;
  if (!person || !draft.role) return;
  const next: MemberView[] = [
    // every row as S1 returned it — the set is replaced, so an untouched row has to travel too
    ...rows.value.map((row) => ({ userId: row.userId, role: row.role ?? null, subject: row.subject })),
    { userId: person.id, role: draft.role, subject: person.subject },
  ];
  replace.mutate(
    { projectId: projectId.value, body: { members: next } },
    {
      onSuccess: () => {
        sheet.value = false;
        pushSuccess(t('admin.memberAdded'));
      },
    },
  );
}

function submitRemove(row: MemberView): void {
  pendingUserId.value = row.userId;
  remove.mutate(
    { projectId: projectId.value, userId: row.userId },
    {
      onSuccess: () => pushSuccess(t('admin.memberRemoved')),
      onSettled: () => (pendingUserId.value = ''),
    },
  );
}
</script>

<template>
  <div class="pm">
    <FuiCard :title="t('admin.membersTitle')">
      <p class="pm__note">{{ t('admin.membersNote') }}</p>
    </FuiCard>

    <FuiNotice v-if="failure" tone="danger" :text="failureText" />

    <FuiCard>
      <template #title>{{ t('admin.membersHeading') }}</template>
      <template #extra>
        <FuiActionButton
          :workflow-id="workflowId"
          :action="ACTION.projectMemberManage"
          size="mini"
          variant="primary"
          :label="t('admin.addMember')"
          @click="openAdd"
        />
      </template>
      <FuiAsync
        :loading="members.isLoading.value"
        :error="members.isError.value"
        :empty="!rows.length"
        :empty-title="t('admin.noMembers')"
        :empty-hint="t('admin.noMembersHint')"
        @retry="members.refetch()"
      >
        <FuiCell v-for="row in rows" :key="`${row.userId}:${row.role ?? ''}`">
          <div class="pm__row">
            <div class="pm__who">
              <!-- `subject` is a required string on the contract and may legitimately come back
                   empty for an account S1 cannot resolve; the id underneath is what was read. -->
              <b>{{ row.subject || row.userId }}</b>
              <span class="pm__code">{{ row.userId }}</span>
            </div>
            <div class="pm__right">
              <FuiStatusTag :tone="row.role ? 'info' : 'muted'" :label="row.role ? roleLabel(row.role) : '—'" />
              <FuiActionButton
                :workflow-id="workflowId"
                :action="ACTION.projectMemberManage"
                size="mini"
                variant="danger"
                :label="t('admin.remove')"
                :loading="remove.isPending.value && pendingUserId === row.userId"
                @click="submitRemove(row)"
              />
            </div>
          </div>
        </FuiCell>
      </FuiAsync>
    </FuiCard>

    <FuiBottomSheet v-model="sheet" :title="t('admin.addMember')">
      <p class="pm__caption">{{ t('admin.subject') }}</p>
      <FuiAsync
        :loading="users.isLoading.value"
        :error="users.isError.value"
        :empty="!people.length"
        :empty-title="t('admin.noSubjects')"
        @retry="users.refetch()"
      >
        <div class="pm__picks">
          <button
            v-for="person in people"
            :key="person.id"
            class="pm__pick"
            :class="{ 'is-on': draft.userId === person.id }"
            @click="draft.userId = person.id"
          >
            {{ person.displayName || person.subject }}
            <span class="pm__pick-code">{{ person.subject }}</span>
          </button>
        </div>
      </FuiAsync>

      <p class="pm__caption">{{ t('admin.role') }}</p>
      <FuiAsync
        :loading="roles.isLoading.value"
        :error="roles.isError.value"
        :empty="!roleNames.length"
        :empty-title="t('admin.noRoles')"
        @retry="roles.refetch()"
      >
        <div class="pm__picks">
          <button
            v-for="name in roleNames"
            :key="name"
            class="pm__pick"
            :class="{ 'is-on': draft.role === name }"
            @click="draft.role = name"
          >{{ name }}</button>
        </div>
      </FuiAsync>

      <!-- The set is replaced as a whole, so what travels with the new row has to be readable. -->
      <p class="pm__hint">{{ t('admin.replaceSetHint', { n: rows.length }) }}</p>

      <template #footer>
        <FuiActionButton
          :workflow-id="workflowId"
          :action="ACTION.projectMemberManage"
          variant="primary"
          block
          :label="t('admin.addMember')"
          :disabled="!canSubmit"
          :loading="replace.isPending.value"
          @click="submitAdd"
        />
      </template>
    </FuiBottomSheet>
  </div>
</template>

<style scoped>
.pm { padding: var(--fui-sp-4); }
.pm__note { margin: 0; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.pm__row { display: flex; justify-content: space-between; align-items: center; gap: 8px; width: 100%; }
.pm__who { display: flex; flex-direction: column; gap: 2px; min-width: 0; }
.pm__code { font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.pm__right { display: flex; align-items: center; gap: 8px; flex: none; }
.pm__caption { margin: var(--fui-sp-3) 0 6px; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.pm__hint { margin: var(--fui-sp-3) 0 0; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.pm__picks { display: flex; flex-direction: column; gap: 8px; }
.pm__pick { min-height: var(--fui-tap-min); border: 1px solid var(--fui-border); border-radius: var(--fui-radius-btn); background: var(--fui-surface); text-align: left; padding: 0 12px; font-size: var(--fui-fs-sm); display: flex; gap: 8px; align-items: center; }
.pm__pick.is-on { border-color: var(--fui-primary); background: var(--fui-info-bg); color: var(--fui-primary); font-weight: 600; }
.pm__pick-code { font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
</style>
