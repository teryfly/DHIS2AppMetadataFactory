/*
 * UI-DEP-003 (BP-REG-010): the hand-binding interface for objects the compiler could not
 * resolve in this environment. The candidate list comes from the stored discovery result and
 * is searched server-side (S5IF-BP11), and setting one needs the note that says why this is the
 * right object — S2 refuses a manual binding without it, so the form asks first. Setting stays
 * clickable only while S1 lists registry_binding.set (FR-UI-009).
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Card, DataTable, DataTableHead, DataTableBody, DataTableRow, DataTableCell, Tag, Button } from '@dhis2/ui';
import { Heading } from '@/components/typography';
import { NoticeBar } from '@/components/NoticeBar';
import { Loading } from '@/lib/PageContainer';
import { TextInput, TextAreaInput } from '@/components/form';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { ACTION } from '@/api/actions';
import { bindingsOptions, bindingCandidatesOptions } from '@/api/queries';
import { useSetBinding } from '@/api/mutations';
import { isApiError } from '@/api/errors';
import type { BindingView } from '@/api/types';

export function BindingPanel({ runId, workflowId }: { runId: string; workflowId: string }) {
  const { t } = useTranslation();
  const bindings = useQuery(bindingsOptions(runId));
  const [picking, setPicking] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [uid, setUid] = useState('');
  const [note, setNote] = useState('');
  const [error, setError] = useState<string | null>(null);
  const candidates = useQuery(bindingCandidatesOptions(runId, picking ?? undefined, search.trim()));
  const setBinding = useSetBinding(workflowId, runId);

  if (bindings.isLoading) return <Loading />;
  const rows = bindings.data?.items ?? [];
  if (rows.length === 0) return null;
  const unresolved = rows.filter((r) => !r.uid);

  const startPicking = (businessKey: string) => {
    setPicking(businessKey);
    setUid('');
    setNote('');
    setSearch('');
    setError(null);
  };

  const submit = () => {
    if (!picking) return;
    if (!uid) {
      setError(t('deployments.bindingNeedCandidate'));
      return;
    }
    if (!note.trim()) {
      setError(t('deployments.bindingNeedNote'));
      return;
    }
    return setBinding
      .mutateAsync({ businessKey: picking, body: { uid, note: note.trim() } })
      .then(() => setPicking(null))
      .catch((error_: unknown) => {
        setError(isApiError(error_) ? error_.message : t('error.title'));
      });
  };

  return (
    <Card>
      {/* a named region: the panel sits in the console beside COMMIT, and the operator has to
          be able to reach "the thing that is blocking this run" by name */}
      <section aria-label={t('deployments.bindings')} className="stack">
        <div className="row" style={{ justifyContent: 'space-between' }}>
          <Heading level={4} size="small">{t('deployments.bindings')}</Heading>
          {unresolved.length > 0 ? (
            <span className="text-warning">{t('deployments.bindingsPending', { count: unresolved.length })}</span>
          ) : null}
        </div>
        <p className="muted" style={{ fontSize: 13 }}>{t('deployments.bindingsHint')}</p>

        <DataTable>
          <DataTableHead>
            <DataTableRow>
              <DataTableCell tag="th">{t('deployments.bindingBusinessKey')}</DataTableCell>
              <DataTableCell tag="th">{t('deployments.bindingUid')}</DataTableCell>
              <DataTableCell tag="th">{t('deployments.bindingResolution')}</DataTableCell>
              <DataTableCell tag="th">{t('deployments.bindingNote')}</DataTableCell>
              <DataTableCell tag="th"> </DataTableCell>
            </DataTableRow>
          </DataTableHead>
          <DataTableBody>
            {rows.map((row: BindingView) => (
              <DataTableRow key={String(row.businessKey)}>
                <DataTableCell className="mono">{row.businessKey}</DataTableCell>
                <DataTableCell className="mono">{row.uid || '—'}</DataTableCell>
                <DataTableCell>{row.resolution || '—'}</DataTableCell>
                <DataTableCell>
                  <div className="stack" style={{ gap: 2 }}>
                    <span>{row.note || '—'}</span>
                    {row.pendingReparse ? <span className="text-warning">{t('deployments.bindingPendingReparse')}</span> : null}
                  </div>
                </DataTableCell>
                <DataTableCell>
                  {row.uid ? null : (
                    /* no targetId: S1 answers this action for the workflow, not per key
                       (domain/actions.py "None of these carries a targetId"), and borrowing the
                       business key here would resolve to "unknown" and grey the control out. */
                    <AllowedActionButton
                      workflowId={workflowId}
                      action={ACTION.registryBindingSet}
                      tone="secondary"
                      onExecute={() => startPicking(String(row.businessKey))}
                    >
                      {t('deployments.bindingSet')}
                    </AllowedActionButton>
                  )}
                </DataTableCell>
              </DataTableRow>
            ))}
          </DataTableBody>
        </DataTable>

        {picking ? (
          <div className="stack" style={{ gap: 10 }}>
            <Heading level={5} size="small">{`${t('deployments.bindingCandidates')} · ${picking}`}</Heading>
            <div style={{ maxWidth: 420 }}>
              <TextInput
                label={t('deployments.bindingSearch')}
                value={search}
                onChange={setSearch}
                placeholder={t('common.search')}
              />
            </div>

            {candidates.isLoading ? (
              <Loading />
            ) : (candidates.data?.items ?? []).length === 0 ? (
              <NoticeBar tone="warning">{t('deployments.bindingNoCandidates')}</NoticeBar>
            ) : (
              <fieldset className="candidate-set" tabIndex={0} aria-label={t('deployments.bindingCandidates')}>
                <legend className="muted" style={{ fontSize: 13 }}>{t('deployments.bindingCandidates')}</legend>
                {(candidates.data?.items ?? []).map((c) => {
                  const value = String(c.uid ?? '');
                  return (
                    <label key={value} className="candidate">
                      <input
                        type="radio"
                        name="binding-candidate"
                        value={value}
                        checked={uid === value}
                        onChange={() => {
                          setUid(value);
                          setError(null);
                        }}
                      />
                      <span className="mono">{value}</span>
                      <span>
                        {c.code ?? ''} · {c.name ?? ''}
                        {c.matching ? <Tag>{t('deployments.bindingMatch')}</Tag> : null}
                      </span>
                      {/* the recorded hash tells an unchanged object from one re-parsed since (S5IF-BP11) */}
                      <span className="muted mono">{t('deployments.bindingHash')}: {c.contentHash ?? c.lastUpdated ?? '—'}</span>
                    </label>
                  );
                })}
              </fieldset>
            )}

            <div style={{ maxWidth: 560 }}>
              <TextAreaInput
                label={t('deployments.bindingNote')}
                value={note}
                onChange={setNote}
                required
                helpText={t('deployments.bindingNoteHelp')}
                error={error && !uid ? error : undefined}
              />
            </div>

            {error ? <NoticeBar tone="error">{error}</NoticeBar> : null}

            <div className="row">
              <AllowedActionButton
                workflowId={workflowId}
                action={ACTION.registryBindingSet}
                tone="primary"
                loading={setBinding.isPending}
                onExecute={submit}
              >
                {t('deployments.bindingSet')}
              </AllowedActionButton>
              <Button secondary onClick={() => setPicking(null)}>
                {t('common.cancel')}
              </Button>
            </div>
          </div>
        ) : null}
      </section>
    </Card>
  );
}
