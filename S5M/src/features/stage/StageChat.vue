<script setup lang="ts">
import { computed, nextTick, ref } from 'vue';
import { useI18n } from 'vue-i18n';
import { FuiCard, FuiStatusTag, FuiAsync, FuiField } from '@/fui';
import FuiActionButton from '@/fui/FuiActionButton.vue';
import { ACTION } from '@/api/actions';
import { useChat } from '@/api/queries';
import { useSendChat, useClearChat } from '@/api/mutations';
import { toneForSeverity, severityLabel } from '@/lib/labels';
import type { ChatTurnView } from '@/api/types';

// The workflow is passed in because `stage.chat` is answered per workflow: without it this button
// could only guess at availability, and UM-BR-01 does not allow guessing.
const props = defineProps<{ stageId: string; workflowId?: string }>();

const { t } = useI18n();
const chat = useChat(() => props.stageId);
const send = useSendChat();
const clear = useClearChat();

const text = ref('');
const pending = ref(false);
const listEl = ref<HTMLElement | null>(null);

const serverTurns = computed<{ role: string; content: string }[]>(() =>
  (chat.data.value?.turns ?? []).map((turn) => ({ role: String(turn.role), content: String(turn.content ?? '') })),
);
const draftReply = computed<string>(() => (pending.value && text.value ? text.value : ''));

const last = computed<ChatTurnView | undefined>(() => send.data.value as unknown as ChatTurnView | undefined);

async function onSend(): Promise<void> {
  const m = text.value.trim();
  if (!m || pending.value) return;
  pending.value = true;
  const outgoing = m;
  text.value = '';
  try {
    await send.mutateAsync({ stageId: props.stageId, message: outgoing });
    await chat.refetch();
  } catch {
    text.value = outgoing;
  } finally {
    pending.value = false;
    await nextTick();
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight;
  }
}
function onClear(): void {
  clear.mutate(props.stageId, { onSuccess: () => chat.refetch() });
}
</script>

<template>
  <FuiCard :title="t('chat.title')">
    <template #extra>
      <FuiActionButton
        v-if="serverTurns.length"
        size="mini"
        :action="ACTION.stageChat"
        :workflow-id="workflowId"
        :label="t('chat.clear')"
        @click="onClear"
      />
    </template>

    <FuiAsync :loading="chat.isLoading.value" :empty="!serverTurns.length" :empty-title="t('chat.title')" :empty-hint="t('chat.empty')">
      <div ref="listEl" class="chat__list">
        <div v-for="(turn, i) in serverTurns" :key="i" class="chat__msg" :class="`chat__msg--${turn.role}`">
          <div class="chat__bubble">{{ turn.content }}</div>
        </div>
        <div v-if="pending" class="chat__msg chat__msg--user"><div class="chat__bubble">{{ draftReply }}</div></div>
        <div v-if="pending" class="chat__thinking">{{ t('chat.thinking') }}</div>
      </div>

      <div v-if="last && (last.questions?.length || last.patchIds?.length)" class="chat__last">
        <div v-if="last.patchIds?.length" class="chat__row">
          <span class="chat__lbl">{{ t('chat.patchIds') }}</span>
          <FuiStatusTag v-for="p in last.patchIds" :key="p" tone="primary" :label="p" />
        </div>
        <div v-for="q in last.questions ?? []" :key="q.localId" class="chat__row">
          <FuiStatusTag :tone="toneForSeverity(q.severity)" :label="severityLabel(q.severity)" />
          <span class="chat__qtext">{{ q.text }}</span>
        </div>
      </div>
    </FuiAsync>

    <div class="chat__composer">
      <FuiField v-model="text" rows="1" autosize type="textarea" :placeholder="t('chat.placeholder')" @keyup.enter.exact.prevent="onSend" />
      <FuiActionButton
        variant="primary"
        :action="ACTION.stageChat"
        :workflow-id="workflowId"
        :label="t('chat.send')"
        :loading="pending"
        :disabled="!text.trim()"
        @click="onSend"
      />
    </div>
  </FuiCard>
</template>

<style scoped>
.chat__list { max-height: 46vh; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; padding: 4px 0; }
.chat__msg { display: flex; }
.chat__msg--user { justify-content: flex-end; }
.chat__bubble { max-width: 82%; padding: 8px 12px; border-radius: 12px; font-size: var(--fui-fs-sm); background: var(--fui-surface-2); color: var(--fui-text); white-space: pre-wrap; word-break: break-word; }
.chat__msg--user .chat__bubble { background: var(--fui-primary); color: var(--fui-on-primary); }
.chat__thinking { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.chat__last { margin-top: var(--fui-sp-2); padding-top: 8px; border-top: 1px dashed var(--fui-border); display: flex; flex-direction: column; gap: 6px; }
.chat__row { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
.chat__lbl { font-size: var(--fui-fs-xs); color: var(--fui-text-tertiary); }
.chat__qtext { font-size: var(--fui-fs-sm); }
.chat__composer { display: flex; gap: 8px; align-items: flex-end; margin-top: var(--fui-sp-2); }
.chat__composer :deep(.fui-field) { flex: 1; }
</style>
