<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiButton, FuiStatusTag, FuiField, FuiCell, FuiIcon, FuiRadioGroup, FuiRadio, FuiEmpty, FuiActionButton } from '@/fui';
import { useEnvironments } from '@/api/queries';
import { ACTION } from '@/api/actions';
import { useCreateProject } from '@/api/mutations';
import { useOperatorStore } from '@/stores/operator';
import { pushError } from '@/lib/toast';

const { t } = useI18n();
const router = useRouter();
const operator = useOperatorStore();
const envs = useEnvironments();
const create = useCreateProject();

const step = ref(1);
const form = reactive({
  name: '',
  description: '',
  ownerName: operator.display,
  deliveryMode: 'A',
  projectType: 'GREENFIELD',
  versionConstraints: '2.41.x',
  defaultLanguage: 'zh',
  translationLanguages: ['en'] as string[],
});

// The field promises "默认为我", and a deep link can mount this view before `/auth/session`
// answers — so keep following whoever "我" turns out to be, until the user names someone themselves.
const ownerEdited = ref(false);
watch(
  () => operator.display,
  (who) => {
    if (who && !ownerEdited.value) form.ownerName = who;
  },
  { immediate: true },
);
function onOwnerInput(v: string): void {
  ownerEdited.value = true;
  form.ownerName = v;
}

interface Sel {
  environmentKey: string;
  environmentClass?: string | null;
  isDesignReference: boolean;
}
const selected = ref<Sel[]>([]);

function toggleEnv(key: string, cls?: string | null): void {
  const idx = selected.value.findIndex((s) => s.environmentKey === key);
  if (idx >= 0) selected.value.splice(idx, 1);
  else selected.value.push({ environmentKey: key, environmentClass: cls, isDesignReference: false });
  // first non-production becomes the design reference (UM-PRJ-001)
  const firstNonProd = selected.value.find((s) => s.environmentClass !== 'PRODUCTION');
  for (const s of selected.value) s.isDesignReference = s === firstNonProd;
}

const hasNonProd = computed(() => selected.value.some((s) => s.environmentClass !== 'PRODUCTION'));
const envItems = computed(() => envs.data.value?.items ?? []);
const canNext = computed(() => (step.value === 1 ? !!form.name.trim() : step.value === 3 ? selected.value.length > 0 && hasNonProd.value : true));
// What the field says wins: it is editable precisely so a project can be created for somebody
// else, and it already carries the session identity when the user leaves it alone.
const ownerSelf = computed(() => form.ownerName.trim() || operator.display);

function createBody() {
  return {
    name: form.name.trim(),
    description: form.description,
    ownerName: ownerSelf.value,
    deliveryMode: form.deliveryMode,
    projectType: form.projectType,
    versionConstraints: form.versionConstraints,
    defaultLanguage: form.defaultLanguage,
    translationLanguages: form.translationLanguages,
    // openapi-typescript renders every defaulted field as required, so the body names them all.
    // An empty codePrefix means "use the Factory default"; this wizard does not ask for one,
    // because a run-scoped prefix is a deployment decision the desktop console exposes.
    codePrefix: '',
    targetEnvironments: selected.value.map((s, i) => ({ environmentKey: s.environmentKey, isDesignReference: s.isDesignReference, promotionOrder: (i + 1) * 100 })),
  };
}

function submit(): void {
  if (!operator.hasOperator) operator.setName(form.ownerName || t('projects.anonymousOperator'));
  create.mutate({ body: createBody() }, {
    onSuccess: (res) => {
      const id = (res as { id?: string }).id;
      if (id) router.replace(`/projects/${id}`);
      else router.replace('/projects');
    },
    onError: () => pushError(create.error.value ?? new Error('create failed')),
  });
}

const modes = [
  { key: 'A', name: 'wizard.modeAName', desc: 'wizard.modeADesc', tip: 'wizard.modeATip' },
  { key: 'B', name: 'wizard.modeBName', desc: 'wizard.modeBDesc', tip: 'wizard.modeBTip' },
];
</script>

<template>
  <div class="wiz">
    <div class="wiz__steps">
      <FuiStatusTag v-for="n in 3" :key="n" :tone="step >= n ? 'primary' : 'muted'" :label="t(`wizard.step`, { n }) + ' · ' + t(`wizard.step${n}`)" />
    </div>

    <FuiCard v-if="step === 1" :title="t('wizard.step1')">
      <FuiField v-model="form.name" :label="t('wizard.name')" :placeholder="t('wizard.namePlaceholder')" required />
      <FuiField :model-value="form.ownerName" :label="t('wizard.ownerName')" :placeholder="t('wizard.ownerDefaultSelf')" @update:model-value="onOwnerInput" />
      <FuiField v-model="form.description" :label="t('wizard.desc')" type="textarea" rows="2" />
    </FuiCard>

    <FuiCard v-if="step === 2" :title="t('wizard.step2')">
      <div class="wiz__modes">
        <button v-for="m in modes" :key="m.key" class="wiz__mode" :class="{ 'is-on': form.deliveryMode === m.key }" @click="form.deliveryMode = m.key">
          <div class="wiz__mode-name">{{ t(m.name) }}</div>
          <div class="wiz__mode-desc">{{ t(m.desc) }}</div>
          <div class="wiz__mode-tip">{{ t(m.tip) }}</div>
        </button>
      </div>
      <FuiCell :title="t('wizard.projectType')">
        <FuiRadioGroup v-model="form.projectType" direction="horizontal">
          <FuiRadio name="GREENFIELD">{{ t('projects.greenfield') }}</FuiRadio>
          <FuiRadio name="BROWNFIELD">{{ t('projects.brownfield') }}</FuiRadio>
        </FuiRadioGroup>
      </FuiCell>
      <FuiField v-model="form.versionConstraints" :label="t('wizard.targetVersion')" />
    </FuiCard>

    <FuiCard v-if="step === 3" :title="t('wizard.step3')">
      <!-- UM-SET-004: this step used to be a blank card with a dead 创建 button when nothing was
           registered. The prerequisite is now named, and there is a way to satisfy it. -->
      <FuiEmpty
        v-if="!envItems.length"
        :title="t('wizard.noEnvironments')"
        :hint="t('wizard.noEnvironmentsHint')"
        :action-text="t('wizard.goEnvironments')"
        @action="router.push('/environments')"
      />
      <p v-if="!hasNonProd && selected.length" class="wiz__warn">{{ t('wizard.envsNeedNonProd') }}</p>
      <FuiCell v-for="e in envItems" :key="e.environmentKey" :title="e.name || e.environmentKey" :label="e.environmentClass" clickable @click="toggleEnv(e.environmentKey, e.environmentClass)">
        <FuiIcon :name="selected.some((s) => s.environmentKey === e.environmentKey) ? 'check' : ''" />
      </FuiCell>
      <div v-if="selected.length" class="wiz__sel">
        <div v-for="(s, i) in selected" :key="s.environmentKey" class="wiz__selrow">
          <span>{{ i + 1 }}. {{ s.environmentKey }}</span>
          <FuiStatusTag v-if="s.isDesignReference" tone="primary" :label="t('wizard.designRef')" />
        </div>
      </div>
    </FuiCard>

    <div v-if="step === 3 && selected.length" class="wiz__sum">
      <p class="wiz__sumhead">{{ t('wizard.review') }}</p>
        <div><b>{{ form.name }}</b></div>
        <div>{{ t('projects.deliveryMode') }}: {{ form.deliveryMode }} · {{ form.projectType }}</div>
        <div>{{ t('wizard.targetVersion') }}: {{ form.versionConstraints }}</div>
        <div>{{ t('wizard.envs') }}: {{ selected.map((s) => s.environmentKey).join(', ') }}</div>
    </div>

    <div class="wiz__bar">
      <FuiButton v-if="step > 1" :label="t('common.prev')" @click="step--" />
      <FuiButton v-if="step < 3" variant="primary" data-bud="wizard-next" :label="t('common.next')" :disabled="!canNext" @click="step++" />
      <FuiActionButton
        v-else
        variant="primary"
        platform
        :action="ACTION.projectCreate"
        :loading="create.isPending.value"
        :label="t('wizard.createProject')"
        data-bud="wizard-create"
        @click="submit"
      />
    </div>
  </div>
</template>

<style scoped>
.wiz { padding: var(--fui-sp-4); }
.wiz__steps { display: flex; gap: 8px; margin-bottom: var(--fui-sp-3); }
.wiz__modes { display: grid; grid-template-columns: 1fr 1fr; gap: var(--fui-sp-2); margin-bottom: var(--fui-sp-3); }
.wiz__mode { text-align: left; border: 1px solid var(--fui-border); border-radius: var(--fui-radius-card); padding: var(--fui-sp-3); background: var(--fui-surface); }
.wiz__mode.is-on { border-color: var(--fui-primary); box-shadow: 0 0 0 2px var(--fui-info-bg); }
.wiz__mode-name { font-weight: 700; }
.wiz__mode-desc { font-size: var(--fui-fs-sm); margin-top: 4px; }
.wiz__mode-tip { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); margin-top: 4px; }
.wiz__warn { color: var(--fui-warning); font-size: var(--fui-fs-sm); }
.wiz__sel { margin-top: var(--fui-sp-3); }
.wiz__selrow { display: flex; justify-content: space-between; padding: 6px 0; font-size: var(--fui-fs-sm); }
.wiz__sum { font-size: var(--fui-fs-sm); display: flex; flex-direction: column; gap: 6px; margin-top: var(--fui-sp-2); }
.wiz__sumhead { margin: 0; font-weight: 600; color: var(--fui-text-secondary); }
.wiz__bar { display: flex; gap: 8px; position: sticky; bottom: 0; padding: var(--fui-sp-3) 0 calc(var(--fui-sp-3) + var(--fui-safe-bottom)); background: var(--fui-bg); }
.wiz__bar .fui-button { flex: 1; }
</style>
