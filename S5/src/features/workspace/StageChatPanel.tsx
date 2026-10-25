/*
 * UI-WB-010 (AI-CHAT-001~003, FR-UI-002): the stage's Review Assistant.
 *
 * It is docked to the workbench area rather than put in the inspector, because the inspector is
 * "what you selected" (UI-WB-005) and is collapsible by design — a conversation is neither, and
 * FR-UI-002 asks for its context header to stay visible while it is open.  Mounted by
 * ProjectWorkspace, so navigating between stages does not discard the panel.
 *
 * The send control is gated by `stage.chat` and nothing else: when S1 blocks the action it names
 * the reason (stage type, or stage status), and that reason is what is shown (UI-BR-01).  The
 * context header comes from S1's own `context` object — never assembled from what the browser
 * happens to know (UI-APP-006).
 */
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@dhis2/ui';
import { ACTION } from '@/api/actions';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { NoticeBar } from '@/components/NoticeBar';
import { TextAreaInput } from '@/components/form';
import { Heading } from '@/components/typography';
import { chatTranscriptOptions } from '@/api/queries';
import { useChatTurn, useClearChat } from '@/api/mutations';
import { isApiError } from '@/api/errors';
import { translateErrorCode } from '@/i18n';

interface ContextHeader {
  projectId?: string;
  projectName?: string;
  stage?: string;
  stageType?: string;
  stageStatus?: string;
  activeDraftId?: string | null;
  deliveryMode?: string | null;
  targetEnvironments?: string[];
}

/**
 * AI-CHAT-001 scopes a dialogue to a design stage, and S1 refuses the read for any other stage
 * type (`domain/enums.py:DIALOGUE_STAGE_TYPES`). A panel over a DISCOVERY or compile stage would be
 * a control with nothing to talk to, announcing itself as a missing object.
 */
const DIALOGUE_STAGE_TYPES = new Set(['REQUIREMENTS', 'DOMAIN', 'MAPPING', 'PAGE_DESIGN']);

export function hasDialogue(stageType?: string | null): boolean {
  return !!stageType && DIALOGUE_STAGE_TYPES.has(stageType);
}

export function StageChatPanel({ stageId, workflowId }: { stageId: string; workflowId?: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(true);
  const [message, setMessage] = useState('');
  const transcript = useQuery(chatTranscriptOptions(stageId));
  const turn = useChatTurn(stageId, workflowId);
  const clear = useClearChat(stageId, workflowId);

  // the newest context S1 answered with, so the header is S1's and not a local guess
  const context = ((turn.data?.context ?? transcript.data?.context) ?? {}) as ContextHeader;
  const turns = (transcript.data?.turns ?? []) as { role: string; content: string }[];
  const questions = (turn.data?.questions ?? []) as {
    localId?: string;
    text?: string;
    severity?: string;
    blocking?: boolean;
  }[];
  const patchIds = (turn.data?.patchIds ?? []) as string[];
  const failure = turn.error ?? clear.error;

  const send = async () => {
    const text = message.trim();
    if (!text) return;
    await turn.mutateAsync({ message: text });
    setMessage('');
    // the transcript is the server's answer, so it is refetched rather than appended locally
    void transcript.refetch();
  };

  return (
    <section
      className="stage-chat"
      aria-label={t('stageChat.panel')}
      style={{ borderTop: '1px solid #ccc', background: '#fff', padding: '8px 12px' }}
    >
      <div className="row" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
        <Heading level={5} size="small">
          {t('stageChat.panel')}
        </Heading>
        <Button small secondary onClick={() => setOpen((value) => !value)}>
          {open ? t('stageChat.collapse') : t('stageChat.expand')}
        </Button>
      </div>

      {open ? (
        <div className="stack" style={{ gap: 8, marginTop: 8 }}>
          {/* FR-UI-002: project, stage, Blueprint version or draft, target environments */}
          <div className="row stage-chat__context" style={{ gap: 10, flexWrap: 'wrap' }}>
            <span>{t('workspace.contextProject')}: {context.projectName ?? context.projectId ?? '—'}</span>
            <span className="muted">·</span>
            <span>{t('workspace.contextStage')}: {context.stageType ?? context.stage ?? '—'} ({context.stageStatus ?? '—'})</span>
            <span className="muted">·</span>
            <span>
              {t('workspace.contextBlueprint')}:{' '}
              {context.activeDraftId ? `${t('workspace.contextDraft')} ${context.activeDraftId}` : '—'}
            </span>
            <span className="muted">·</span>
            <span>
              {t('workspace.contextEnvironment')}: {(context.targetEnvironments ?? []).join(', ') || '—'}
            </span>
          </div>

          {turns.length ? (
            <ul className="issue-list" aria-label={t('stageChat.history')}>
              {turns.map((item, index) => (
                <li key={`${item.role}-${index}`}>
                  <strong>{item.role === 'user' ? t('stageChat.you') : t('stageChat.assistant')}</strong>{' '}
                  {item.content}
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{t('stageChat.noHistory')}</p>
          )}

          {turn.data?.answer ? (
            <NoticeBar tone="info" title={t('stageChat.assistant')}>
              <div className="stack" style={{ gap: 4 }}>
                <span>{turn.data.answer}</span>
                {patchIds.length ? (
                  <span>
                    {t('stageChat.patches')}:{' '}
                    {patchIds.map((patchId) => (
                      <Link key={patchId} to={`/projects/${context.projectId ?? ''}/stages/${stageId}`}>
                        {patchId}
                      </Link>
                    ))}
                  </span>
                ) : null}
                {questions.length ? (
                  <ul className="issue-list">
                    {questions.map((question) => (
                      <li key={question.localId ?? question.text}>
                        <span className="mono">{question.severity ?? '—'}</span> {question.text}
                        {/* AI-CHAT-001: a dialogue question is advice, not a Gate blocker */}
                        {question.blocking ? null : <span className="muted"> · {t('stageChat.notABlocker')}</span>}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </div>
            </NoticeBar>
          ) : null}

          {failure && isApiError(failure) ? (
            <NoticeBar tone="error" title={t('error.title')}>
              <div className="stack" style={{ gap: 4 }}>
                <span>{translateErrorCode(failure.errorCode, failure.message)}</span>
                {failure.correlationId ? (
                  <span className="mono muted" style={{ fontSize: 12 }}>
                    {t('common.correlationId')}: {failure.correlationId}
                  </span>
                ) : null}
              </div>
            </NoticeBar>
          ) : null}

          <TextAreaInput label={t('stageChat.ask')} value={message} onChange={setMessage} rows={2} />
          <div className="row" style={{ gap: 8 }}>
            <AllowedActionButton
              workflowId={workflowId}
              action={ACTION.stageChat}
              targetId={stageId}
              onExecute={send}
              loading={turn.isPending}
              disabled={!message.trim()}
            >
              {t('stageChat.send')}
            </AllowedActionButton>
            <AllowedActionButton
              workflowId={workflowId}
              action={ACTION.stageChat}
              targetId={stageId}
              tone="secondary"
              onExecute={() => clear.mutate()}
              loading={clear.isPending}
            >
              {t('stageChat.clear')}
            </AllowedActionButton>
          </div>
        </div>
      ) : null}
    </section>
  );
}
