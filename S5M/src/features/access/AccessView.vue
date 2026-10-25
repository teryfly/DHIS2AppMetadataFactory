<script setup lang="ts">
import { computed, reactive, ref } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiButton, FuiAsync, FuiBottomSheet, FuiFilterBar, FuiField, FuiSwitch } from '@/fui';
// The session panel (sign in / out / change my own password) stays on plain buttons: those are not
// permission-gated writes, so there is no action for allowed-actions to answer.
import FuiActionButton from '@/fui/FuiActionButton.vue';
import { ACTION } from '@/api/actions';
import { useAuthStore } from '@/stores/auth';
import { useUserFeed, useRoles, useProjects } from '@/api/queries';
import { useCreateUser, useSetPassword, useAssignRole, useRevokeRole, usePatchUser } from '@/api/mutations';
import { pushSuccess } from '@/lib/toast';
import { roleLabel, stageStatusLabel } from '@/lib/labels';

// The three M4 pages named by the old UM-AC-21 carve-out are not missing any more: they are project
// routes now (`/projects/:id/members|grants|exemptions`), because each of them is about *this*
// project and S1 answers their rights per project (`domain/actions.py:project_actions()`). This view
// keeps what it always kept — the session, the accounts, the role catalogue — and does not grow a
// fourth, fifth and sixth tab: a platform screen has no business showing a project-scoped table, and
// the reader would have to pick the project anyway.
// History: docs/adr/004-m4-carve-out.md. Current judgement: src/test/m4Presence.test.ts.

const { t } = useI18n();
const auth = useAuthStore();
// S5IF-G04: the account list is paged, so the screen keeps the window it fetched and says how many
// accounts S1 counted — an access review that quietly stops at the first page is how a disabled or
// locked account is missed.
/**
 * S5IF-G04: the account list is one offset walk — `useInfiniteQuery` keeps the pages, and the screen
 * says how many accounts S1 counted. A list that shows 50 of 120 without saying so is how an access
 * review misses an account, and a hand-rolled page array re-appends the window it already had every
 * time a write invalidates the cache.
 */
const feed = useUserFeed();
const userRows = computed(() => feed.data.value?.pages.flatMap((page) => page.items ?? []) ?? []);
const userTotal = computed(() => Number(feed.data.value?.pages[0]?.total ?? userRows.value.length));
const userHasMore = computed(() => !!feed.hasNextPage.value);
function loadMoreUsers(): void {
  void feed.fetchNextPage();
}
const roles = useRoles();
// The scope of a grant is chosen from the projects that exist, so this screen needs the same list the
// projects tab shows. It is a small read, cached like everything else, and it replaces a free-text
// field whose only usable input was an id copied out of another screen.
const projects = useProjects();

const tab = ref('session');
const tabs = computed(() => [
  { value: 'session', label: t('auth.sessionTitle') },
  { value: 'users', label: t('access.users') },
  { value: 'roles', label: t('access.roles') },
]);

// ---- logout / change password
//
// There is deliberately no sign-in form here any more: the shell routes an operator with no session
// to /login, and a second copy of the same form on a second screen is a second set of wording to keep
// honest. What stays is everything that needs a session — ending it, and changing one's own password.
const router = useRouter();
function goSignIn(): void {
  void router.push({ name: 'login' });
}
async function doLogout(): Promise<void> {
  try { await auth.logout(); } catch { /* toast */ }
}
const pw = reactive({ value: '' });
async function doChangePw(): Promise<void> {
  if (!pw.value || !auth.userId) return;
  try {
    await setPw.mutateAsync({ userId: auth.userId, body: { password: pw.value, mustChangePassword: false } });
    // S1 revoked this account's sessions when the digest changed (`revoke_for_user`), so the cookie
    // in this browser is already dead. `refresh()` is the honest next read — it asks S1 who this is
    // instead of the app concluding it — and the answer is "nobody", which brings the sign-in panel
    // up. Saying so is the part the operator cannot infer: the page did not break, it signed them out.
    pushSuccess(t('auth.changedSignedOut'));
  } catch { /* the toast host names the reason; the value is dropped either way */ }
  pw.value = '';
  await auth.refresh();
}

// ---- users create
const cu = reactive({ subject: '', displayName: '', email: '', password: '', source: 'local', mustChange: false });
const createUser = useCreateUser();
function submitCreateUser(): void {
  createUser.mutate({ subject: cu.subject, displayName: cu.displayName, email: cu.email, password: cu.password || undefined, source: cu.source, mustChangePassword: cu.mustChange } as never, {
    onSuccess: () => { cu.subject = ''; cu.displayName = ''; cu.email = ''; cu.password = ''; cu.mustChange = false; },
  });
}

// ---- users edit and retire
//
// Two facts an operator has to be able to change after the account exists: the name people read,
// and whether the account is live at all. S1 offers both on one endpoint and, because there is no
// delete, 停用 *is* retirement — without it this console could create an account but never take one
// out of the system.
const patchUser = usePatchUser();
const pendingUser = ref('');
const editSheet = ref(false);
const editForm = reactive({ userId: '', displayName: '', email: '' });
function openEdit(u: { id: string; displayName?: string; email?: string }): void {
  editForm.userId = u.id;
  editForm.displayName = u.displayName ?? '';
  editForm.email = u.email ?? '';
  editSheet.value = true;
}
function submitEdit(): void {
  patchUser.mutate(
    { userId: editForm.userId, body: { displayName: editForm.displayName, email: editForm.email } as never },
    { onSuccess: () => (editSheet.value = false) },
  );
}
function toggleStatus(u: { id: string; status: string }): void {
  pendingUser.value = u.id;
  patchUser.mutate(
    { userId: u.id, body: { status: u.status === 'ACTIVE' ? 'DISABLED' : 'ACTIVE' } as never },
    { onSettled: () => (pendingUser.value = '') },
  );
}

// ---- role assignment
const roleSheet = ref(false);
const roleForm = reactive({ userId: '', role: '', projectId: '' as string });
function openAssign(userId: string, projectId = ''): void {
  roleForm.userId = userId; roleForm.role = ''; roleForm.projectId = projectId;
  roleSheet.value = true;
}
const assign = useAssignRole();
const revoke = useRevokeRole();
function submitRole(): void {
  if (!roleForm.role) return;
  assign.mutate({ userId: roleForm.userId, body: { role: roleForm.role, projectId: roleForm.projectId || undefined } }, { onSuccess: () => (roleSheet.value = false) });
}

// password reset on a user
const pwSheet = ref(false);
const pwTarget = ref('');
const pwNew = ref('');
// Resetting somebody else's password should leave them changing it at their next sign-in unless the
// admin says otherwise — and that has to be a choice on this console too, because S1 writes whatever
// the request omits (`false`), which quietly cleared the flag the desktop sets.
const pwMustChange = ref(true);
function openResetPw(id: string): void { pwTarget.value = id; pwNew.value = ''; pwMustChange.value = true; pwSheet.value = true; }
const setPw = useSetPassword();
function submitResetPw(): void {
  if (!pwNew.value) return;
  setPw.mutate({ userId: pwTarget.value, body: { password: pwNew.value, mustChangePassword: pwMustChange.value } as never }, { onSuccess: () => (pwSheet.value = false) });
}
</script>

<template>
  <div class="acc">
    <FuiFilterBar v-model="tab" :options="tabs" />

    <!-- session -->
    <template v-if="tab === 'session'">
      <FuiCard :title="t('auth.sessionTitle')">
        <div v-if="auth.authMode === 'off'" class="acc__note">{{ t('auth.authOff') }}</div>
        <template v-else>
          <div class="acc__row"><span class="acc__k">{{ t('auth.whoAmI') }}</span>{{ auth.session?.displayName || auth.subject || '—' }}</div>
          <div class="acc__row"><span class="acc__k">{{ t('auth.rolesLabel') }}</span>
            <FuiStatusTag v-for="r in auth.roles" :key="r" tone="info" :label="roleLabel(r)" />
            <span v-if="!auth.roles.length">—</span>
          </div>
          <!-- No form here: sign-in is one page with one wording (UM-AUTH-001). Reaching it from a
               screen that shows "not signed in" is the link the operator needs, not a second login. -->
          <div v-if="!auth.authenticated" class="acc__login">
            <p class="acc__note">{{ t('auth.notSignedIn') }}</p>
            <FuiButton variant="primary" block :label="t('auth.goSignIn')" @click="goSignIn" />
          </div>
          <div v-else class="acc__actions">
            <FuiButton :label="t('auth.logout')" @click="doLogout" />
          </div>
          <!-- Offered for any signed-in account, not only a flagged one: S1 lets a person change
               their own password without `user.manage`, and the only other way to change it is to
               ask an administrator — which is how a `mustChangePassword` account got stuck. -->
          <div v-if="auth.authenticated" class="acc__login">
            <p v-if="auth.mustChangePassword" class="acc__warn">{{ t('auth.mustChangePassword') }}</p>
            <FuiField v-model="pw.value" type="password" :label="t('auth.newPassword')" :help="t('auth.changeHint')" />
            <FuiButton variant="primary" block :label="t('auth.changePassword')" :disabled="!pw.value" @click="doChangePw" />
          </div>
        </template>
      </FuiCard>
    </template>

    <!-- users -->
    <template v-else-if="tab === 'users'">
      <FuiCard :title="t('access.newUser')">
        <FuiField v-model="cu.subject" :label="t('access.subject')" />
        <FuiField v-model="cu.displayName" :label="t('access.displayName')" />
        <FuiField v-model="cu.email" :label="t('access.email')" />
        <FuiField v-model="cu.password" type="password" :label="t('auth.password')" />
        <!-- Default off, and now a choice: this console used to never set the flag and to offer no
             way to read what it had set. Desktop defaults it on, because there an administrator is
             handing over an initial password. CP-IAM-005 ③ keeps the flag report-only either way. -->
        <div class="acc__switch">
          <FuiSwitch v-model="cu.mustChange" :label="t('access.mustChange')" />
          <span>{{ t('access.mustChange') }}</span>
        </div>
        <FuiActionButton platform :action="ACTION.userManage" variant="primary" block :label="t('access.newUser')" :loading="createUser.isPending.value" @click="submitCreateUser" />
      </FuiCard>
      <FuiAsync :loading="feed.isLoading.value" :empty="!userRows.length">
        <!-- S1 has no delete endpoint: retiring an account is the only exit, and the screen
             should say so instead of leaving a hole where a delete button would be. -->
        <p class="acc__hint">{{ t('access.noDeleteNote') }}</p>
        <FuiCard v-for="u in userRows" :key="String(u.id)" :title="String(u.displayName || u.subject)">
          <!-- `locked` is S1's state, not a guess: five failed attempts freeze the account for
               fifteen minutes and the login answer deliberately says nothing about it (an
               enumeration oracle), so this list is the only place the fact can be read. The tone
               alone was not enough — a red tag that still spells ACTIVE reads like a colour bug. -->
          <template #extra>
            <FuiStatusTag :tone="u.locked ? 'danger' : 'success'" :label="u.locked ? t('access.locked') : stageStatusLabel(String(u.status ?? ''))" />
            <!-- The desktop table has had a 需改口令 column all along and this list did not, so an
                 operator on the phone could set the flag but not see it. `mustChangePassword` is
                 report-only (CP-IAM-005 ③), which makes *showing* it the whole job. -->
            <FuiStatusTag v-if="u.mustChangePassword" tone="warning" :label="t('access.mustChangeTag')" />
          </template>
          <div class="acc__code">{{ u.subject }} · {{ u.id }}</div>
          <!-- Held roles, each revocable on its own. A revoke that names a role the account never
               had is indistinguishable from one that worked, so the list is the only source here. -->
          <div v-if="(u.roles ?? []).length" class="acc__held">
            <div v-for="r in u.roles ?? []" :key="`${r.role}:${r.projectId ?? ''}`" class="acc__row">
              <FuiStatusTag tone="info" :label="r.projectId ? `${roleLabel(String(r.role))} · ${r.projectId}` : roleLabel(String(r.role))" />
              <FuiActionButton
                platform
                :action="ACTION.userManage"
                size="mini"
                :label="t('access.revokeRole')"
                :loading="revoke.isPending.value"
                @click="revoke.mutate({ userId: u.id, role: String(r.role ?? ''), projectId: r.projectId ?? undefined })"
              />
            </div>
          </div>
          <div class="acc__actions">
            <FuiActionButton platform :action="ACTION.userManage" size="mini" :label="t('access.assignRole')" @click="openAssign(u.id)" />
            <FuiActionButton platform :action="ACTION.userManage" size="mini" :label="t('auth.changePassword')" @click="openResetPw(u.id)" />
            <FuiActionButton platform :action="ACTION.userManage" size="mini" :label="t('access.editAccount')" @click="openEdit(u)" />
            <!-- S1 has no delete: 停用 is how an account leaves the system, and it revokes the sessions
                 that account has open. Without this button the console could hire but never fire. -->
            <FuiActionButton
              platform
              :action="ACTION.userManage"
              size="mini"
              :label="u.status === 'ACTIVE' ? t('access.disable') : t('access.enable')"
              :loading="patchUser.isPending.value && pendingUser === u.id"
              @click="toggleStatus(u)"
            />
          </div>
        </FuiCard>
        <!-- S5IF-G04: the window S1 answered, and the number it counted. A list that shows 50 of
             120 accounts without saying so is how an access review misses an account. -->
        <p class="acc__pager">{{ userRows.length }} {{ t('common.of') }} {{ userTotal }}</p>
        <FuiButton v-if="userHasMore" size="small" block :label="t('common.loadMore')" :loading="feed.isFetching.value" @click="loadMoreUsers" />
      </FuiAsync>
    </template>

    <!-- roles -->
    <template v-else>
      <FuiAsync :loading="roles.isLoading.value" :empty="!(roles.data.value?.items?.length)">
        <FuiCard v-for="r in roles.data.value?.items ?? []" :key="r.name" :title="roleLabel(r.name)">
          <div class="acc__acts">{{ (r.actions ?? []).length }} {{ t('common.actions') }}</div>
          <div class="acc__chips"><span v-for="a in (r.actions ?? []).slice(0, 12)" :key="a" class="acc__chip">{{ a }}</span></div>
        </FuiCard>
      </FuiAsync>
    </template>

    <!-- role assignment sheet -->
    <FuiBottomSheet v-model="roleSheet" :title="t('access.assignRole')">
      <div class="acc__roles">
        <button v-for="r in roles.data.value?.items ?? []" :key="r.name" class="acc__role" :class="{ 'is-on': roleForm.role === r.name }" @click="roleForm.role = r.name">{{ roleLabel(r.name) }}</button>
      </div>
      <!-- Scope, chosen from the projects that exist rather than typed: an id nobody can see is not
           something an operator can transcribe correctly, and since S1 answers an unknown project with
           a 404 the only outcome of free text was a guess that failed. -->
      <p class="acc__caption">{{ t('access.projectId') }}</p>
      <div class="acc__roles">
        <button class="acc__role" :class="{ 'is-on': !roleForm.projectId }" @click="roleForm.projectId = ''">{{ t('access.orgWide') }}</button>
        <button
          v-for="p in projects.data.value?.items ?? []"
          :key="p.id"
          class="acc__role"
          :class="{ 'is-on': roleForm.projectId === p.id }"
          @click="roleForm.projectId = String(p.id)"
        >{{ p.name || p.id }}</button>
      </div>
      <p class="acc__hint">{{ t('access.projectIdHelp') }}</p>
      <template #footer><FuiActionButton platform :action="ACTION.userManage" variant="primary" block :label="t('common.confirm')" :disabled="!roleForm.role" :loading="assign.isPending.value" @click="submitRole" /></template>
    </FuiBottomSheet>

    <!-- reset password sheet: titled as a reset, because the same words as the self-service change
         made the two unreadable apart from each other. -->
    <FuiBottomSheet v-model="pwSheet" :title="t('access.resetPw')">
      <FuiField v-model="pwNew" type="password" :label="t('auth.newPassword')" />
      <div class="acc__switch">
        <FuiSwitch v-model="pwMustChange" :label="t('access.mustChange')" />
        <span>{{ t('access.mustChange') }}</span>
      </div>
      <p class="acc__hint">{{ t('access.mustChangeHelp') }}</p>
      <template #footer><FuiActionButton platform :action="ACTION.userManage" variant="primary" block :label="t('common.confirm')" :disabled="!pwNew" @click="submitResetPw" /></template>
    </FuiBottomSheet>

    <!-- edit account sheet: the name people read, and the address S1 keeps beside it. The login name
         is deliberately not editable here — it is the string in the audit trail and in approvals, so
         changing it would rewrite history rather than correct a label. -->
    <FuiBottomSheet v-model="editSheet" :title="t('access.editAccount')">
      <FuiField v-model="editForm.displayName" :label="t('access.displayName')" :help="t('access.displayNameHelp')" />
      <FuiField v-model="editForm.email" :label="t('access.email')" />
      <template #footer>
        <FuiActionButton
          platform
          :action="ACTION.userManage"
          variant="primary"
          block
          :label="t('access.saveAccount')"
          :loading="patchUser.isPending.value"
          @click="submitEdit"
        />
      </template>
    </FuiBottomSheet>
  </div>
</template>

<style scoped>
.acc { padding: var(--fui-sp-4); }
.acc__row { display: flex; gap: 8px; align-items: center; font-size: var(--fui-fs-sm); padding: 4px 0; }
/* The switch and its caption on one line: Vant gives the control no visible text, so the caption is
   what an operator reads, and `aria-label` on the switch is what a screen reader gets. */
.acc__switch { display: flex; gap: 8px; align-items: center; padding: 8px 0; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.acc__k { color: var(--fui-text-tertiary); min-width: 80px; }
.acc__note { color: var(--fui-text-secondary); font-size: var(--fui-fs-sm); }
.acc__login { margin-top: var(--fui-sp-2); display: flex; flex-direction: column; gap: 8px; }
.acc__actions { display: flex; gap: 8px; margin-top: var(--fui-sp-2); }
.acc__code { font-family: ui-monospace, monospace; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin: 4px 0; }
.acc__acts { font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); margin-bottom: 6px; }
.acc__chips { display: flex; flex-wrap: wrap; gap: 4px; }
.acc__chip { font-family: ui-monospace, monospace; font-size: 10px; background: var(--fui-surface-2); border-radius: 6px; padding: 1px 5px; color: var(--fui-text-secondary); }
.acc__roles { display: flex; flex-direction: column; gap: 8px; }
.acc__caption { margin: var(--fui-sp-3) 0 6px; font-size: var(--fui-fs-sm); color: var(--fui-text-secondary); }
.acc__hint { margin: 6px 0 0; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.acc__pager { display: flex; justify-content: space-between; align-items: center; gap: 8px; padding: 8px 0 4px; font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.acc__role { min-height: 44px; border: 1px solid var(--fui-border); border-radius: var(--fui-radius-btn); background: var(--fui-surface); text-align: left; padding: 0 12px; font-size: var(--fui-fs-sm); }
.acc__role.is-on { border-color: var(--fui-primary); background: var(--fui-info-bg); color: var(--fui-primary); font-weight: 600; }
.acc__warn { color: var(--fui-danger, #d34d4d); font-size: var(--fui-fs-xs); margin: 0 0 4px; }
</style>
