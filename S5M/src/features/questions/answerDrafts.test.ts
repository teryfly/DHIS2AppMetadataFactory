import { describe, expect, it } from 'vitest';
import { ref } from 'vue';
import type * as T from '@/api/types';
import { answerFor, recommendedDraft, useAnswerDrafts } from './answerDrafts';

function question(overrides: Partial<T.QuestionView> = {}): T.QuestionView {
  return {
    id: 'Q-1',
    text: 'Which option?',
    severity: 'MEDIUM',
    answerMode: 'SINGLE',
    blocking: false,
    status: 'OPEN',
    workflowId: 'WF-1',
    stageId: 'ST-1',
    suggestions: [
      { id: 'S-1', text: 'First', recommended: false },
      { id: 'S-2', text: 'Second', recommended: true },
    ],
    ...overrides,
  };
}

describe('the questionnaire draft', () => {
  it('accepts every recommendation in one step and submits one answer per question', () => {
    const single = question({ id: 'Q-1', blocking: true });
    const multi = question({
      id: 'Q-2',
      answerMode: 'MULTIPLE',
      suggestions: [
        { id: 'F-1', text: 'Facility A', recommended: true },
        { id: 'F-2', text: 'Facility B', recommended: true },
        { id: 'F-3', text: 'New facility', recommended: false },
      ],
    });
    const drafts = useAnswerDrafts(ref([single, multi]));

    drafts.acceptAllRecommended();

    expect(drafts.answers.value).toEqual([
      { questionId: 'Q-1', suggestionIds: ['S-2'] },
      { questionId: 'Q-2', suggestionIds: ['F-1', 'F-2'] },
    ]);
    expect(drafts.unansweredBlocking.value).toEqual([]);
  });

  it('lets a partially answered questionnaire produce a payload without touching the other drafts', () => {
    const single = question({ id: 'Q-1', blocking: true });
    const other = question({ id: 'Q-2', blocking: true });
    const drafts = useAnswerDrafts(ref([single, other]));

    drafts.draftFor('Q-1').choice = 'S-2';

    expect(drafts.answers.value).toEqual([{ questionId: 'Q-1', suggestionIds: ['S-2'] }]);
    expect(drafts.unansweredBlocking.value.map((item) => item.id)).toEqual(['Q-2']);
  });

  it('falls back to the first suggestion when the model marked none', () => {
    const noRecommendation = question({ suggestions: [{ id: 'S-1', text: 'Only' }] });
    expect(recommendedDraft(noRecommendation)).toEqual({ choice: 'S-1', choices: [], custom: '' });
    expect(answerFor(noRecommendation, recommendedDraft(noRecommendation))).toEqual({ suggestionIds: ['S-1'] });
  });
});
