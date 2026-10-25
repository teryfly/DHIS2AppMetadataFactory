<script setup lang="ts">
import { computed, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiButton, FuiAsync, FuiFilterBar, FuiSwipeCell } from '@/fui';
import { useGatesPending, useOpenQuestions, useProjects } from '@/api/queries';
import { useUiStore } from '@/stores/ui';
import { usePrefsStore } from '@/stores/prefs';
import { fmtRelTime } from '@/lib/time';
import { stageTypeLabel, gateTypeLabel } from '@/lib/labels';

const { t } = useI18n();
const route = useRoute();
const router = useRouter();
const ui = useUiStore();
const prefs = usePrefsStore();
const lang = computed(() => (prefs.language === 'en' ? 'en' : 'zh'));

const gates = useGatesPending();
const questions = useOpenQuestions();
const projects = useProjects(false);
// The filter is part of the address, not just of this component: 侧栏的「待审批」入口是一个带着
// `?kind=gate` 的链接，点开就该只看审批节点，回来时也还是那一句筛选。非法值回 `all`——地址栏是
// 人会改的东西，猜一个筛选项不如按没筛处理。
const KINDS = ['all', 'gate', 'question', 'blocked', 'manual'];
const initialKind = KINDS.includes(String(route.query.kind ?? '')) ? String(route.query.kind) : 'all';
const tab = ref(initialKind);
watch(tab, (kind) => {
  void router.replace({ query: kind === 'all' ? {} : { kind } });
});

interface Todo {
  key: string;
  kind: 'gate' | 'question' | 'blocked' | 'manual';
  urgency: number;
  title: string;
  subtitle?: string;
  time?: string | null;
  to: string;
  action: string;
}

/** The four categories UM-HOME-002 merges, and the words the chip uses for each. */
const KIND_LABEL = {
  gate: 'todo.gate',
  question: 'todo.question',
  blocked: 'todo.blocked',
  manual: 'todo.manualCheck',
} as const;

function kindTone(kind: Todo['kind']): 'warning' | 'danger' | 'info' {
  if (kind === 'blocked') return 'danger';
  if (kind === 'manual') return 'info';
  return 'warning';
}

const rows = computed<Todo[]>(() => {
  const out: Todo[] = [];
  const projectRows = projects.data.value?.items ?? [];
  const projectNames = new Map(projectRows.map((p) => [p.id, p.name]));
  for (const g of gates.data.value?.items ?? []) {
    out.push({ key: `gate-${g.id}`, kind: 'gate', urgency: 1, title: `${t('todo.gate')} · ${gateTypeLabel(g.type)}`, subtitle: g.environmentKey ?? g.projectName ?? undefined, time: g.createdAt, to: `/gates/${g.id}?approve=1`, action: t('todo.goApprove') });
  }
  for (const q of questions.data.value?.items ?? []) {
    const project = projectRows.find((p) => p.id === q.projectId);
    const stage = project?.stages?.find((s) => s.id === q.stageId);
    const subtitle = [project?.name ?? projectNames.get(String(q.projectId ?? '')), stage ? stageTypeLabel(stage.stageType) : '']
      .filter(Boolean)
      .join(' · ');
    out.push({
      key: `q-${q.id}`,
      kind: 'question',
      urgency: q.blocking || q.severity === 'HIGH' ? 0 : q.severity === 'MEDIUM' ? 1 : 2,
      title: `${t('todo.question')} · ${q.text}`,
      subtitle: subtitle || undefined,
      time: q.createdAt,
      to: `/questions/${q.id}`,
      action: t('todo.chooseAnswer'),
    });
  }
  for (const p of projectRows) {
    for (const s of p.stages ?? []) {
      if (s.status === 'BLOCKED') out.push({ key: `blk-${s.id}`, kind: 'blocked', urgency: 0, title: `${t('todo.blocked')} · ${stageTypeLabel(s.stageType)}`, subtitle: p.name, to: `/stages/${s.id}`, action: t('todo.viewStage') });
      else if (s.status === 'MANUAL_CHECK') out.push({ key: `mc-${s.id}`, kind: 'manual', urgency: 2, title: `${t('todo.manualCheck')} · ${stageTypeLabel(s.stageType)}`, subtitle: p.name, to: `/stages/${s.id}`, action: t('stage.markResult') });
    }
  }
  return out
    .filter((r) => !ui.isSnoozed(r.key))
    .filter((r) => tab.value === 'all' || r.kind === tab.value)
    .sort((a, b) => {
      if (a.urgency !== b.urgency) return a.urgency - b.urgency;
      // UM-HOME-002: within one urgency, the item that has waited longest is read first.
      return (Date.parse(a.time ?? '') || 0) - (Date.parse(b.time ?? '') || 0);
    });
});

const filterOptions = computed(() => [
  { value: 'all', label: t('common.all'), count: rows.value.length },
  { value: 'gate', label: t('todo.gate') },
  { value: 'question', label: t('todo.question') },
  { value: 'blocked', label: t('todo.blocked') },
  { value: 'manual', label: t('todo.manualCheck') },
]);
const loading = computed(() => gates.isLoading.value || questions.isLoading.value || projects.isLoading.value);
</script>

<template>
  <div class="todo">
    <FuiFilterBar v-model="tab" :options="filterOptions" />
    <FuiAsync :loading="loading" :empty="!rows.length" :empty-title="t('todo.title')" :empty-hint="t('home.noTodo')">
      <FuiSwipeCell v-for="r in rows" :key="r.key">
        <FuiCard clickable :data-bud="`todo-${r.key}`" @click="router.push(r.to)">
          <template #title>{{ r.title }}</template>
          <div class="todo__row">
            <FuiStatusTag :tone="kindTone(r.kind)" :label="t(KIND_LABEL[r.kind])" />
            <span v-if="r.time" class="todo__time">{{ fmtRelTime(r.time, lang) }}</span>
          </div>
          <div v-if="r.subtitle" class="todo__sub">{{ r.subtitle }}</div>
          <FuiButton variant="primary" size="small" round :data-bud="`act-${r.key}`" :label="r.action" @click.stop="router.push(r.to)" />
        </FuiCard>
        <template #right>
          <div class="todo__snooze" @click="ui.snooze(r.key)">{{ t('todo.snooze') }}</div>
        </template>
      </FuiSwipeCell>
    </FuiAsync>
  </div>
</template>

<style scoped>
.todo { padding: var(--fui-sp-2) 0; }
.todo__row { display: flex; justify-content: space-between; align-items: center; }
.todo__time { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.todo__sub { color: var(--fui-text-secondary); font-size: var(--fui-fs-sm); margin: 4px 0 8px; }
.todo__snooze { height: 100%; width: 88px; background: var(--fui-warning); color: #fff; display: flex; align-items: center; justify-content: center; }
</style>
