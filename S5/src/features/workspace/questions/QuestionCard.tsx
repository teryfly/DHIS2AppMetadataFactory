/*
 * CP-Q-005 / UI-Q-001: the stage's questions as one questionnaire. The agent proposes structured
 * answers; the reviewer picks one (radio) or a set (checkbox) per question, or writes an "Other"
 * answer, and submits the whole page once. The submission resumes the run once, after its last
 * blocking answer; a turn that raises new questions returns them to the same card.
 */
import { useEffect, useMemo, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Button, Card, Checkbox, Radio, Tag } from '@dhis2/ui';
import { TextAreaInput } from '@/components/form';
import { NoticeBar } from '@/components/NoticeBar';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { pushToast } from '@/components/toast';
import { useAnswerQuestions } from '@/api/mutations';
import { actionIntent, payloadDigest } from '@/api/idempotency';
import { severityLabel } from '@/lib/labels';
import type { AnswerQuestionsBody, QuestionView } from '@/api/types';
import { OTHER, answerFor, emptyDraft, modeOf, recommendedDraft, type Draft } from './answerDraft';


export function QuestionCard({ questions, workflowId }: { questions: QuestionView[]; workflowId?: string }) {
  const { t } = useTranslation();
  const answer = useAnswerQuestions(workflowId ?? '');
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});

  useEffect(() => {
    setDrafts((previous) => {
      const next = { ...previous };
      let changed = false;
      for (const question of questions) {
        if (!next[question.id]) {
          next[question.id] = emptyDraft();
          changed = true;
        }
      }
      return changed ? next : previous;
    });
  }, [questions]);

  const update = (id: string, patch: Partial<Draft>) => {
    setDrafts((previous) => ({
      ...previous,
      [id]: { ...(previous[id] ?? emptyDraft()), ...patch },
    }));
  };

  const adoptAllRecommended = () =>
    setDrafts(Object.fromEntries(questions.map((question) => [question.id, recommendedDraft(question)])));

  const answered = useMemo(
    () => questions.filter((question) => answerFor(question, drafts[question.id]) !== null),
    [questions, drafts],
  );
  const unansweredBlocking = questions.filter(
    (question) => question.blocking && answerFor(question, drafts[question.id]) === null,
  );
  const hasSuggestions = questions.some((question) => (question.suggestions ?? []).length > 0);
  const payload = {
    answers: answered.map((question) => ({
      questionId: question.id,
      ...(answerFor(question, drafts[question.id]) ?? { suggestionIds: [] }),
    })),
  } as AnswerQuestionsBody;

  const submit = () =>
    answer.mutateAsync({
      body: payload,
      intent: actionIntent('POST', '/questions/answers', workflowId ?? '', payloadDigest(JSON.stringify(payload))),
    });

  return (
    <Card>
      <div className="stack" style={{ gap: 12 }}>
        <NoticeBar tone="info" title={t('questions.questionnaireHint')}>
          {t('questions.answeredCount', { done: answered.length, total: questions.length })}
        </NoticeBar>
        {unansweredBlocking.length ? (
          <NoticeBar tone="error" title={t('questions.missingBlockingHint', { count: unansweredBlocking.length })}>
            {t('questions.highBlockingBody')}
          </NoticeBar>
        ) : null}

        {questions.map((question, index) => {
          const draft = drafts[question.id] ?? emptyDraft();
          const multiple = modeOf(question) === 'MULTIPLE';
          const other = draft.choice === OTHER || draft.choices.includes(OTHER);
          const toggle = (id: string, checked: boolean) => {
            if (!checked) {
              update(question.id, { choices: draft.choices.filter((value) => value !== id) });
              return;
            }
            update(question.id, {
              choices: draft.choices.includes(id) ? draft.choices : [...draft.choices, id],
            });
          };
          return (
            <div
              key={question.id}
              className="stack"
              style={{ gap: 8, borderTop: index ? '1px solid var(--color-neutral-light)' : undefined, paddingTop: index ? 12 : 0 }}
            >
              <div className="row" style={{ justifyContent: 'space-between', alignItems: 'flex-start', gap: 8 }}>
                <strong>{`${index + 1}. ${question.text}`}</strong>
                <div className="row" style={{ gap: 4, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                  <Tag negative={question.severity === 'HIGH'} neutral={question.severity !== 'HIGH'}>
                    {severityLabel(question.severity)}
                  </Tag>
                  <Tag neutral>{multiple ? t('questions.multipleHint') : t('questions.singleHint')}</Tag>
                  {question.blocking ? <Tag negative>{t('questions.blockingTag')}</Tag> : <Tag neutral>{t('questions.optional')}</Tag>}
                </div>
              </div>
              {question.affects?.length ? (
                <span className="muted" style={{ fontSize: 12 }}>
                  {t('questions.impact')}: {question.affects.join(', ')}
                </span>
              ) : null}

              <div className="stack" style={{ gap: 6 }}>
                {(question.suggestions ?? []).map((suggestion) => {
                  const label = (
                    <span className="stack" style={{ gap: 2 }}>
                      <span className="row" style={{ gap: 4, flexWrap: 'wrap', alignItems: 'center' }}>
                        {suggestion.text}
                        {suggestion.recommended ? <Tag positive>{t('questions.recommended')}</Tag> : null}
                        {suggestion.changesUid ? <Tag negative>{t('questions.whyChangesUid')}</Tag> : null}
                      </span>
                      {suggestion.rationale ? (
                        <span className="muted" style={{ fontSize: 12 }}>
                          {t('questions.rationale')}: {suggestion.rationale}
                        </span>
                      ) : null}
                      {suggestion.regenerates?.length ? (
                        <span className="muted" style={{ fontSize: 12 }}>
                          {t('questions.regenerates')}: {suggestion.regenerates.join(', ')}
                        </span>
                      ) : null}
                    </span>
                  );
                  return multiple ? (
                    <Checkbox
                      key={suggestion.id}
                      name={`q-${question.id}`}
                      value={suggestion.id}
                      checked={draft.choices.includes(suggestion.id)}
                      onChange={({ checked }) => toggle(suggestion.id, checked)}
                      label={label}
                    />
                  ) : (
                    <Radio
                      key={suggestion.id}
                      name={`q-${question.id}`}
                      value={suggestion.id}
                      checked={draft.choice === suggestion.id}
                      onChange={() => update(question.id, { choice: suggestion.id })}
                      label={label}
                    />
                  );
                })}
                {multiple ? (
                  <Checkbox
                    name={`q-${question.id}`}
                    value={OTHER}
                    checked={draft.choices.includes(OTHER)}
                    onChange={({ checked }) => toggle(OTHER, checked)}
                    label={t('questions.other')}
                  />
                ) : (
                  <Radio
                    name={`q-${question.id}`}
                    value={OTHER}
                    checked={draft.choice === OTHER}
                    onChange={() => update(question.id, { choice: OTHER })}
                    label={t('questions.other')}
                  />
                )}
              </div>

              {(question.suggestions ?? []).length === 0 ? (
                <span className="muted" style={{ fontSize: 12 }}>{t('questions.noOptions')}</span>
              ) : null}
              {other ? (
                <TextAreaInput
                  label={t('questions.otherLabel')}
                  value={draft.custom}
                  onChange={(value) => update(question.id, { custom: value })}
                  rows={3}
                  required={question.blocking}
                />
              ) : null}
              {question.blocking && answerFor(question, draft) === null ? (
                <span style={{ color: 'var(--color-error-600)', fontSize: 12 }}>{t('questions.missingBlocking')}</span>
              ) : null}
            </div>
          );
        })}

        <div className="questionnaire-footer">
          <Button secondary small onClick={adoptAllRecommended} disabled={!hasSuggestions}>
            {t('questions.acceptAllRecommended')}
          </Button>
          <span className="muted" style={{ fontSize: 12, flex: '1 1 auto' }}>
            {unansweredBlocking.length
              ? t('questions.missingBlockingHint', { count: unansweredBlocking.length })
              : t('questions.submitAllHint')}
          </span>
          <AllowedActionButton
            workflowId={workflowId}
            action="question.answer"
            targetId={questions[0]?.stageId ?? undefined}
            loading={answer.isPending}
            disabled={!payload.answers.length}
            onExecute={() =>
              submit().then((result) => {
                const next = result.questions ?? [];
                pushToast({
                  tone: 'success',
                  title: next.length
                    ? result.resumed
                      ? t('questions.nextRound', { count: next.length })
                      : t('questions.partialSaved', { count: next.length })
                    : t('questions.allAnswered'),
                });
              })
            }
          >
            {t('questions.submitAll')}
          </AllowedActionButton>
        </div>
      </div>
    </Card>
  );
}
