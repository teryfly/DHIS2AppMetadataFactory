import type { QuestionView } from '@/api/types';

export const OTHER = '__fui_other__';

export interface Draft {
  choice: string;
  choices: string[];
  custom: string;
}

export function emptyDraft(): Draft {
  return { choice: '', choices: [], custom: '' };
}

export function modeOf(question: QuestionView): 'SINGLE' | 'MULTIPLE' {
  return question.answerMode === 'MULTIPLE' ? 'MULTIPLE' : 'SINGLE';
}

export function answerFor(question: QuestionView, draft: Draft | undefined) {
  if (!draft) return null;
  if (modeOf(question) === 'MULTIPLE') {
    const suggestionIds = draft.choices.filter((value) => value !== OTHER);
    const customText = draft.choices.includes(OTHER) ? draft.custom.trim() : '';
    if (!suggestionIds.length && !customText) return null;
    return { suggestionIds, customText: customText || undefined };
  }
  if (!draft.choice) return null;
  if (draft.choice === OTHER) {
    const customText = draft.custom.trim();
    return customText ? { suggestionIds: [], customText } : null;
  }
  return { suggestionIds: [draft.choice] };
}

/** The ids the console adopts for "accept every recommendation".
 *
 * A SINGLE question takes its one recommended suggestion; a MULTIPLE question takes every
 * recommended one. Older rows (and a model that forgot the flag) fall back to the first
 * option so the button still has a deterministic answer.
 */
export function recommendedSuggestionIds(question: QuestionView): string[] {
  const suggestions = question.suggestions ?? [];
  const recommended = suggestions.filter((suggestion) => suggestion.recommended).map((suggestion) => suggestion.id);
  if (recommended.length) return recommended;
  return suggestions[0]?.id ? [suggestions[0].id] : [];
}

export function recommendedDraft(question: QuestionView): Draft {
  const ids = recommendedSuggestionIds(question);
  return modeOf(question) === 'MULTIPLE'
    ? { choice: '', choices: [...ids], custom: '' }
    : { choice: ids[0] ?? '', choices: [], custom: '' };
}
