/*
 * UI-MAN-001..004: every agent stage offers BOTH a structured form and a JSON editor; content is
 * submitted as a manual patch (same Schema as agent output — UI-MAN-002). On rejection the issues
 * are listed path-by-path, newly assigned UIDs are shown, UID_POOL_EXHAUSTED reads as "wait", and
 * the content can go straight to approval without running the agent (UI-MAN-004).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, Button, SegmentedControl, NoticeBox } from '@dhis2/ui';
import { TextInput, SelectInput } from '@/components/form';
import { JsonEditor } from '@/components/JsonEditor';
import { useQuery } from '@tanstack/react-query';
import { applySectionSchema, sectionModelPath, validateJson } from '@/lib/monacoSetup';
import { sectionSchemaOptions } from '@/api/queries';
import type { Monaco } from '@monaco-editor/react';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { NoticeBar } from '@/components/NoticeBar';
import { useSubmitPatch, useSubmitForApproval } from '@/api/mutations';
import { actionIntent } from '@/api/idempotency';
import { qk } from '@/api/queryKeys';
import { useQueryClient } from '@tanstack/react-query';
import type { DraftView, PatchSubmitView } from '@/api/types';
import { nrrPriorityLabel } from '@/lib/labels';

interface Props {
  stageId: string;
  workflowId: string;
  projectId: string;
  /** S1 answers ``null`` until the project's first design stage opens one (S5IF-BP05, CP-UI-022). */
  draft: DraftView | null | undefined;
  /** The stage's S2 section, when the stage writes one that can be authored by hand. */
  stageType?: string;
}

/**
 * Which Blueprint section a design stage edits. Only these four are hand-authored today, and a
 * stage that is not listed gets no Schema rather than a guessed one: attaching the wrong document
 * would mark valid content as an error.
 */
export const SECTION_BY_STAGE_TYPE: Record<string, string> = {
  REQUIREMENTS: 'requirements',
  DOMAIN: 'domain',
  MAPPING: 'mapping',
  PAGE_DESIGN: 'pages',
};

type Entry = { businessKey: string; text: string; priority: string };

export function ManualAuthoring({ stageId, workflowId, projectId, draft, stageType }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const patch = useSubmitPatch(workflowId, projectId);
  const approval = useSubmitForApproval(workflowId, projectId);
  const initialDoc = useMemo(() => (draft?.document as { requirements?: Entry[] } | undefined), [draft]);

  const [tab, setTab] = useState<'form' | 'json'>('form');
  const [entries, setEntries] = useState<Entry[]>(initialDoc?.requirements ?? []);
  const [json, setJson] = useState(() => JSON.stringify({ requirements: initialDoc?.requirements ?? [] }, null, 2));
  const [result, setResult] = useState<PatchSubmitView | null>(null);

  const jsonError = tab === 'json' ? validateJson(json) : null;

  // UI-MAN-002: Monaco validates and completes against S2's section Schema, delivered unchanged
  // by S1 (S5IF-BP02). The Schema usually arrives after the editor mounted, so the instance is
  // kept in a ref and configured when the document is there.
  const section = SECTION_BY_STAGE_TYPE[stageType ?? ''] ?? '';
  const schemaQuery = useQuery(sectionSchemaOptions(projectId, section || undefined));
  const monacoRef = useRef<Monaco | null>(null);
  useEffect(() => {
    const document = schemaQuery.data;
    if (monacoRef.current && section && document) {
      applySectionSchema(monacoRef.current, section, document as Record<string, unknown>);
    }
  }, [schemaQuery.data, section]);

  const buildPatch = (): Record<string, unknown> | null => {
    if (tab === 'json') {
      if (jsonError) return null;
      return JSON.parse(json) as Record<string, unknown>;
    }
    return { requirements: entries };
  };

  const submitPatch = () => {
    const body = buildPatch();
    if (!body) return;
    const intent = actionIntent('POST', '/stages/patches', stageId, String(Object.keys(body).length));
    return patch.mutateAsync({ stageId, patch: body, intent }).then((res) => {
      setResult(res);
      void qc.invalidateQueries({ queryKey: qk.blueprintDraft(projectId) });
    });
  };

  const submitApproval = () => {
    approval.mutate({ stageId });
    void qc.invalidateQueries({ queryKey: qk.allowedActions(workflowId) });
  };

  const uidPoolWaiting = result?.patchStatus === 'REJECTED' && (result.issues ?? []).some((i) => (i.description ?? '').includes('UID_POOL'));

  return (
    <Card>
      <div className="stack">
        <SegmentedControl
          selected={tab}
          options={[
            { label: t('manual.tabForm'), value: 'form' },
            { label: t('manual.tabJson'), value: 'json' },
          ]}
          onChange={({ value }: { value: string }) => setTab(value === 'json' ? 'json' : 'form')}
        />

        {tab === 'form' ? (
          <div className="stack">
            {entries.map((e, i) => (
              <div key={e.businessKey || i} className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
                <div style={{ flex: 2 }}>
                  <TextInput
                    label={t('inspector.businessKey')}
                    value={e.text}
                    onChange={(v) => setEntries((prev) => prev.map((x, j) => (j === i ? { ...x, text: v } : x)))}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <SelectInput
                    label={t('common.type')}
                    value={e.priority}
                    onChange={(v) => setEntries((prev) => prev.map((x, j) => (j === i ? { ...x, priority: v } : x)))}
                    options={[
                      { value: 'MUST', label: nrrPriorityLabel('MUST') },
                      { value: 'SHOULD', label: nrrPriorityLabel('SHOULD') },
                      { value: 'COULD', label: nrrPriorityLabel('COULD') },
                      { value: 'WONT', label: nrrPriorityLabel('WONT') },
                    ]}
                  />
                </div>
              </div>
            ))}
            <Button secondary onClick={() => setEntries((prev) => [...prev, { businessKey: '', text: '', priority: 'SHOULD' }])}>
              {t('wizard.addEnvironment')}
            </Button>
          </div>
        ) : (
          <div className="stack">
            <p className="muted" style={{ fontSize: 13 }}>{t('manual.jsonHint')}</p>
            <JsonEditor
              value={json}
              onChange={setJson}
              height="320px"
              modelPath={section ? sectionModelPath(section) : undefined}
              onMount={(monaco) => {
                monacoRef.current = monaco;
                const document = schemaQuery.data;
                if (section && document) applySectionSchema(monaco, section, document as Record<string, unknown>);
              }}
            />
            {jsonError ? <NoticeBox error title={t('manual.invalidJson')}>{jsonError}</NoticeBox> : null}
          </div>
        )}

        {/* patch result feedback (UI-MAN-003) */}
        {result ? (
          result.patchStatus === 'ACCEPTED' ? (
            <NoticeBox valid title={t('common.ok')}>
              {Object.keys(result.assignedUids ?? {}).length > 0 ? (
                <div className="stack">
                  <span>{t('manual.newlyAssignedUids')}</span>
                  <ul>
                    {Object.entries(result.assignedUids ?? {}).map(([k, v]) => (
                      <li key={k} className="mono">{k} → {String(v)}</li>
                    ))}
                  </ul>
                </div>
              ) : (
                <span>{t('common.ok')}</span>
              )}
            </NoticeBox>
          ) : (
            <NoticeBar tone="error" title={t('manual.patchRejectedTitle')}>
              {uidPoolWaiting ? (
                <span>{t('manual.uidPoolWaiting')}</span>
              ) : (
                <ul>
                  {(result.issues ?? []).map((iss) => (
                    <li key={iss.issueId}>
                      <span className="mono">{iss.location ?? iss.objectKey ?? iss.businessKey ?? ''}</span> — {iss.description}
                    </li>
                  ))}
                </ul>
              )}
            </NoticeBar>
          )
        ) : null}

        <div className="row">
          <AllowedActionButton workflowId={workflowId} action="patch.submit" targetId={stageId} onExecute={submitPatch} loading={patch.isPending}>
            {t('manual.submitPatch')}
          </AllowedActionButton>
          <AllowedActionButton workflowId={workflowId} action="stage.submit_for_approval" targetId={stageId} tone="secondary" onExecute={submitApproval} loading={approval.isPending}>
            {t('workspace.submitApproval')}
          </AllowedActionButton>
        </div>
        <p className="muted" style={{ fontSize: 12 }}>{t('manual.submitApprovalHint')}</p>
      </div>
    </Card>
  );
}
