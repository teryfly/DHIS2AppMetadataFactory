<script setup lang="ts">
import { computed, ref } from 'vue';
import { useRoute } from 'vue-router';
import { FuiJsonEditor, FuiCard } from '@/fui';
import { modelSchema } from '@/lib/json/testSchema.fixture';
import type { Diagnostic } from '@/lib/json/sectionValidator';

// DEV-only harness for UM-AC-17. It exists so the editor can be driven by Playwright against a
// fixed schema without a live S1; `import.meta.env.DEV` keeps it out of the production route table.
// ?big=1 seeds a ~5,000-line document so the latency clause of UM-CAP-001 can be measured by
// *typing into* it, which is what the clause bounds, rather than by pasting it through the
// keyboard (90 KB of insertText is its own CDP bottleneck and says nothing about the editor).
const route = useRoute();
const big = route.query.big === '1';
const model = ref<Record<string, unknown>>(
  big
    ? {
        deliveryMode: 'A',
        projectType: 'GREENFIELD',
        defaultLanguage: { value: '姓名', language: 'zh-CN' },
        entities: Array.from({ length: 1600 }, (_, i) => ({ businessKey: `k.${String(i).padStart(4, '0')}`, displayName: '名称' })),
      }
    : { deliveryMode: 'A', entities: [] },
);
const issues = ref<Diagnostic[]>([]);
const lineCount = computed(() => JSON.stringify(model.value, null, 2).split('\n').length);
</script>

<template>
  <div class="dev-json">
    <FuiCard :title="'UM-AC-17 harness'">
      <FuiJsonEditor v-model="model" :schema="modelSchema" :height="420" @diagnostics="issues = $event" />
      <p class="dev-json__count" data-testid="issue-count">{{ issues.length }}</p>
      <p data-testid="line-count">{{ lineCount }}</p>
    </FuiCard>
  </div>
</template>

<style scoped>
.dev-json { padding: var(--fui-sp-4); }
.dev-json__count { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
</style>
