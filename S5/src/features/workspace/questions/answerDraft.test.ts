import { describe, expect, it } from 'vitest';
import type { QuestionView } from '@/api/types';
import { answerFor, emptyDraft, recommendedDraft, recommendedSuggestionIds } from './answerDraft';

function question(overrides: Partial<QuestionView> = {}): QuestionView {
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

describe('recommendedDraft', () => {
  it('selects the model recommendation for a single-choice question', () => {
    expect(recommendedSuggestionIds(question())).toEqual(['S-2']);
    expect(recommendedDraft(question())).toEqual({ choice: 'S-2', choices: [], custom: '' });
  });

  it('selects every recommended option for a multi-choice question', () => {
    const multi = question({
      answerMode: 'MULTIPLE',
      suggestions: [
        { id: 'F-1', text: 'Facility A', recommended: true },
        { id: 'F-2', text: 'Facility B', recommended: true },
        { id: 'F-3', text: 'New facility', recommended: false },
      ],
    });
    expect(recommendedDraft(multi)).toEqual({ choice: '', choices: ['F-1', 'F-2'], custom: '' });
    expect(answerFor(multi, recommendedDraft(multi))).toEqual({ suggestionIds: ['F-1', 'F-2'], customText: undefined });
  });

  it('falls back to the first option when an older row carries no recommendation', () => {
    const noRecommendation = question({ suggestions: [{ id: 'S-1', text: 'Only' }] });
    expect(recommendedSuggestionIds(noRecommendation)).toEqual(['S-1']);
    expect(recommendedDraft(noRecommendation).choice).toBe('S-1');
  });
});

describe('answerFor', () => {
  it('turns a chosen recommendation into the id the server joins into answer text', () => {
    const draft = { ...emptyDraft(), choice: 'S-2' };
    expect(answerFor(question(), draft)).toEqual({ suggestionIds: ['S-2'] });
  });

  it('keeps a custom answer when the reviewer picks Other', () => {
    const draft = { choice: '__fui_other__', choices: [], custom: '  something else  ' };
    expect(answerFor(question(), draft)).toEqual({ suggestionIds: [], customText: 'something else' });
  });

  it('reports an untouched draft as unanswered', () => {
    expect(answerFor(question(), emptyDraft())).toBeNull();
  });
});
