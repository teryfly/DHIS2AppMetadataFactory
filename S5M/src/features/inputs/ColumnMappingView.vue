<script setup lang="ts">
import { computed, reactive } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiAsync, FuiStatusTag, FuiIcon, FuiField, FuiActionButton, FuiButton } from '@/fui';
import { useColumnMapping, useWorkflowOfProject } from '@/api/queries';
import { useParseInput } from '@/api/mutations';
import { pushSuccess } from '@/lib/toast';
import { ACTION } from '@/api/actions';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const projectId = computed(() => String(route.params.id ?? ''));
const artifactId = computed(() => String(route.params.artifactId ?? ''));
const workflow = useWorkflowOfProject(projectId);
const wfId = computed(() => workflow.data.value?.id ?? '');

const cm = useColumnMapping(projectId, artifactId);
const parse = useParseInput();

interface Proposal { columnMapping?: Record<string, string>; headers?: string[]; sheet?: string; affectedKeys?: string[] }
const proposals = computed<Proposal[]>(() => ((cm.data.value as unknown as { proposals?: Proposal[] })?.proposals ?? []));
// local editable copy keyed by sheet+source column
const edits = reactive<Record<string, Record<string, string>>>({});
function ensure(sheet: string, p: Proposal): Record<string, string> {
  if (!edits[sheet]) {
    edits[sheet] = { ...(p.columnMapping ?? {}) };
    for (const h of p.headers ?? []) if (!(h in edits[sheet])) edits[sheet][h] = '';
  }
  return edits[sheet];
}
function digest(sheet: string): string {
  const m = edits[sheet] ?? {};
  return Object.keys(m).sort().map((k) => `${k}=${m[k]}`).join('&');
}

// Unmapped columns must not be sent as empty targets: the desktop drops them, and sending `""` made
// the two consoles mean different things by the same action. At least one mapped column is required.
function compact(sheet: string): { mapping: Record<string, string>; ok: boolean } {
  const src = edits[sheet] ?? {};
  const mapping: Record<string, string> = {};
  for (const [column, target] of Object.entries(src)) {
    const value = String(target ?? '').trim();
    if (value) mapping[column] = value;
  }
  return { mapping, ok: Object.keys(mapping).length > 0 };
}

function confirm(sheet: string): void {
  const { mapping, ok } = compact(sheet);
  if (!ok) return;
  parse.mutate(
    { projectId: projectId.value, artifactId: artifactId.value, body: { options: { columnMapping: mapping, sheet } as never }, mappingDigest: digest(sheet) },
    { onSuccess: () => { pushSuccess(t('inputs.confirmMapping')); router.push(`/projects/${projectId.value}/inputs`); } },
  );
}
</script>

<template>
  <div class="cm">
    <FuiAsync :loading="cm.isLoading.value" :error="cm.isError.value" @retry="cm.refetch()">
      <FuiCard v-for="(p, i) in proposals" :key="i" :title="`${t('inputs.sheet')}: ${p.sheet ?? i + 1}`">
        <p class="cm__hint">{{ t('inputs.prefillHint') }}</p>
        <div v-for="h in p.headers ?? []" :key="h" class="cm__row">
          <span class="cm__src">{{ h }}</span>
          <FuiIcon name="arrow" class="cm__arrow" />
          <FuiField v-model="ensure(p.sheet ?? String(i), p)[h]" class="cm__tgt" :placeholder="t('inputs.targetField')" />
        </div>
        <FuiStatusTag v-if="p.affectedKeys?.length" tone="info" :label="t('changeRequests.affectedObjects') + ': ' + p.affectedKeys.length" />
        <FuiActionButton
          :workflow-id="wfId"
          :action="ACTION.inputConfirmColumnMapping"
          :target-id="artifactId"
          variant="primary"
          block
          class="cm__confirm"
          :loading="parse.isPending.value"
          :disabled="!compact(p.sheet ?? String(i)).ok"
          :label="t('inputs.confirmMapping')"
          @click="confirm(p.sheet ?? String(i))"
        />
      </FuiCard>
      <div v-if="!proposals.length && !cm.isLoading.value" class="cm__empty">
        <p>{{ t('inputs.mappingEmpty') }}</p>
        <p>{{ t('inputs.mappingOnlyTabular') }}</p>
        <FuiButton size="small" :label="t('inputs.mappingBack')" @click="router.push(`/projects/${projectId}/inputs`)" />
      </div>
    </FuiAsync>
  </div>
</template>

<style scoped>
.cm { padding: var(--fui-sp-4); }
.cm__hint { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.cm__row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-bottom: 1px solid var(--fui-border); }
.cm__src { flex: 1; font-size: var(--fui-fs-sm); font-family: ui-monospace, monospace; }
.cm__arrow { color: var(--fui-text-tertiary); }
.cm__tgt { flex: 1.2; }
.cm__confirm { margin-top: var(--fui-sp-3); }
.cm__empty { text-align: center; color: var(--fui-text-tertiary); }
</style>
