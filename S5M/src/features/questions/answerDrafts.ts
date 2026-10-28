import { computed, reactive, toValue, watch, type MaybeRefOrGetter } from 'vue';
import type * as T from '@/api/types';

export const OTHER = '__fui_other__';

export interface Draft {
  /** SINGLE: the chosen suggestion id, or OTHER. */
  choice: string;
  /** MULTIPLE: every chosen suggestion id, possibly including OTHER. */
  choices: string[];
  /** The free-text "Other" value. */
  custom: string;
}

export function emptyDraft(): Draft {
  return { choice: '', choices: [], custom: '' };
}

export function modeOf(item: T.QuestionView): 'SINGLE' | 'MULTIPLE' {
  return item.answerMode === 'MULTIPLE' ? 'MULTIPLE' : 'SINGLE';
}

export function answerFor(item: T.QuestionView, draft: Draft | undefined): { suggestionIds: string[]; customText?: string } | null {
  if (!draft) return null;
  if (modeOf(item) === 'MULTIPLE') {
    const ids = draft.choices.filter((value) => value !== OTHER);
    const custom = draft.choices.includes(OTHER) ? draft.custom.trim() : '';
    if (!ids.length && !custom) return null;
    return { suggestionIds: ids, customText: custom || undefined };
  }
  if (!draft.choice) return null;
  if (draft.choice === OTHER) {
    const custom = draft.custom.trim();
    return custom ? { suggestionIds: [], customText: custom } : null;
  }
  return { suggestionIds: [draft.choice] };
}

/** The ids "accept every recommendation" selects.
 *
 * A SINGLE question takes its one recommended suggestion; a MULTIPLE question takes every
 * recommended one. Older rows (and a model that forgot the flag) fall back to the first
 * option so the button still has a deterministic answer.
 */
export function recommendedSuggestionIds(item: T.QuestionView): string[] {
  const suggestions = item.suggestions ?? [];
  const recommended = suggestions.filter((suggestion) => suggestion.recommended).map((suggestion) => suggestion.id);
  if (recommended.length) return recommended;
  return suggestions[0]?.id ? [suggestions[0].id] : [];
}

export function recommendedDraft(item: T.QuestionView): Draft {
  const ids = recommendedSuggestionIds(item);
  return modeOf(item) === 'MULTIPLE'
    ? { choice: '', choices: [...ids], custom: '' }
    : { choice: ids[0] ?? '', choices: [], custom: '' };
}

/** The questionnaire's draft state, kept out of the view so each rule is testable. */
export function useAnswerDrafts(questions: MaybeRefOrGetter<T.QuestionView[]>) {
  const drafts = reactive<Record<string, Draft>>({});
  const items = computed(() => toValue(questions));

  watch(
    items,
    (list) => {
      for (const item of list) {
        if (!drafts[item.id]) drafts[item.id] = emptyDraft();
      }
    },
    { immediate: true },
  );

  function draftFor(id: string): Draft {
    if (!drafts[id]) drafts[id] = emptyDraft();
    return drafts[id];
  }

  function answerForQuestion(item: T.QuestionView) {
    return answerFor(item, draftFor(item.id));
  }

  const answered = computed(() => items.value.filter((item) => answerForQuestion(item) !== null));
  const unansweredBlocking = computed(
    () => items.value.filter((item) => item.blocking && answerForQuestion(item) === null),
  );
  const answers = computed(() =>
    items.value
      .map((item) => ({ questionId: item.id, ...(answerForQuestion(item) ?? { suggestionIds: [] }) }))
      .filter((item) => item.suggestionIds.length > 0 || item.customText),
  );

  function acceptAllRecommended(): void {
    for (const item of items.value) drafts[item.id] = recommendedDraft(item);
  }

  return {
    drafts,
    draftFor,
    answerForQuestion,
    answered,
    unansweredBlocking,
    answers,
    acceptAllRecommended,
  };
}
