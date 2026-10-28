/*
 * UI-MAN-001..004: every agent stage offers BOTH a structured form and a JSON editor; content is
 * submitted as a manual patch — real RFC 6902 operations, which is all S2's submit_patch accepts
 * (UI-MAN-002). The editor seeds from the *draft entries read* (S5IF-BP04), because S1 pins
 * blueprint/draft's `document` to null by ruling (S5IF-BP05); a hand-authored body of `{requirements:
 * [...]}` used to look fine against the fake and 422 against real S2. On rejection the issues are
 * listed path-by-path, newly assigned UIDs are shown, UID_POOL_EXHAUSTED reads as "wait", and the
 * content can go straight to approval without running the agent (UI-MAN-004).
 */
import { useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { Card, Button, SegmentedControl, NoticeBox } from '@dhis2/ui';
import { Loading } from '@/lib/PageContainer';
import { TextInput, SelectInput } from '@/components/form';
import { JsonEditor } from '@/components/JsonEditor';
import { useQuery } from '@tanstack/react-query';
import { applySectionSchema, sectionModelPath, validateJson } from '@/lib/monacoSetup';
import { blueprintEntriesOptions, sectionSchemaOptions } from '@/api/queries';
import type { Monaco } from '@monaco-editor/react';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { NoticeBar } from '@/components/NoticeBar';
import { useSubmitPatch, useSubmitForApproval } from '@/api/mutations';
import { actionIntent } from '@/api/idempotency';
import { qk } from '@/api/queryKeys';
import { useQueryClient } from '@tanstack/react-query';
import type { PatchSubmitView } from '@/api/types';
import { nrrPriorityLabel } from '@/lib/labels';

interface Props {
  stageId: string;
  workflowId: string;
  projectId: string;
  /** The stage's S2 section, when the stage writes one that can be authored by hand. */
  stageType?: string;
}

/**
 * Which Blueprint sections a design stage writes — the read seed for both the workbench's
 * entry table and this editor. Only these are hand-authored today, and a stage that is not
 * listed gets no Schema rather than a guessed one: attaching the wrong document would mark
 * valid content as an error.
 */
export const STAGE_SECTIONS: Record<string, string[]> = {
  REQUIREMENTS: ['requirements'],
  DOMAIN: ['domain', 'useCases', 'actors'],
  MAPPING: ['mapping', 'registry', 'architecture'],
  PAGE_DESIGN: ['pages', 'apiIntents', 'uiIntents', 'rules', 'fixtures', 'architecture'],
};

/** The section whose Schema the JSON editor attaches to: the stage's first authored section.
 *  A stage that writes no section (DISCOVERY, COMMIT…) gets none rather than a guessed one. */
export const SECTION_BY_STAGE_TYPE: Record<string, string> = Object.fromEntries(
  Object.entries(STAGE_SECTIONS).flatMap(([stage, list]) => (list[0] ? [[stage, list[0]]] : [])),
);

/** The section whose policy the MAPPING draft may amend (stage_map STAGE_SCOPE). */
const GENERATION_POLICY = 'generationPolicy';
const NAMING_CONVENTIONS = ['PREFIX_TYPE_KEY', 'PREFIX_TYPE_INDEX', 'TYPE_KEY'];

type RawEntry = Record<string, unknown> & { _section?: string; _path?: string };
type SeedDoc = Record<string, Record<string, unknown[]>>;

/** Rebuild `{section: {container: [...]}}` from the draft entries' own `_path` locators. */
function seedFromEntries(entries: RawEntry[]): SeedDoc {
  const doc: SeedDoc = {};
  for (const raw of entries) {
    const segments = String(raw._path ?? '').split('/');
    const section = raw._section ?? segments[1] ?? '';
    const container = segments[2] ?? 'items';
    if (!section) continue;
    const { _section, _path, ...entry } = raw;
    const byContainer = (doc[section] ??= {});
    (byContainer[container] ??= []).push(entry);
  }
  return doc;
}

type Entry = { businessKey: string; text: string; priority: string; raw: Record<string, unknown> };

export function ManualAuthoring({ stageId, workflowId, projectId, stageType }: Props) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const patch = useSubmitPatch(workflowId, projectId);
  const approval = useSubmitForApproval(workflowId, projectId);

  const sections = STAGE_SECTIONS[stageType ?? ''] ?? [];
  const isRequirements = stageType === 'REQUIREMENTS';
  const mayAmendPolicy = stageType === 'MAPPING';

  const entriesQuery = useQuery({
    ...blueprintEntriesOptions(projectId, sections.length ? sections : undefined, 'DRAFT'),
    enabled: sections.length > 0,
  });
  const seed = useMemo(() => seedFromEntries((entriesQuery.data?.entries ?? []) as RawEntry[]), [entriesQuery.data]);

  const [tab, setTab] = useState<'form' | 'json'>('form');
  const [entries, setEntries] = useState<Entry[]>([]);
  const [json, setJson] = useState('');
  const [touched, setTouched] = useState(false);
  const [codePrefix, setCodePrefix] = useState('');
  const [namingConvention, setNamingConvention] = useState('');
  const [result, setResult] = useState<PatchSubmitView | null>(null);
  const seededOnce = useRef(false);

  useEffect(() => {
    if (entriesQuery.isFetching || seededOnce.current) return;
    seededOnce.current = true;
    setJson(JSON.stringify(seed, null, 2));
    setEntries(
      (seed.requirements?.items ?? []).map((item) => {
        const record = item as Record<string, unknown>;
        return {
          businessKey: String(record.businessKey ?? ''),
          text: String(record.text ?? record.statement ?? ''),
          priority: String(record.priority ?? 'SHOULD'),
          raw: record,
        };
      }),
    );
    if (!isRequirements) setTab('json');
  }, [entriesQuery.isFetching, seed, isRequirements]);

  const jsonError = tab === 'json' && touched ? validateJson(json) : null;

  // UI-MAN-002: Monaco validates and completes against S2's section Schema, delivered unchanged
  // by S1 (S5IF-BP02). The Schema usually arrives after the editor mounted, so the instance is
  // kept in a ref and configured when the document is there.
  const section = sections[0] ?? '';
  const schemaQuery = useQuery(sectionSchemaOptions(projectId, section || undefined));
  const monacoRef = useRef<Monaco | null>(null);
  useEffect(() => {
    const document = schemaQuery.data;
    if (monacoRef.current && section && document) {
      applySectionSchema(monacoRef.current, section, document as Record<string, unknown>);
    }
  }, [schemaQuery.data, section]);

  /**
   * One `replace` per container the operator actually changed (against the seeded draft), so an
   * untouched section is never rewritten — and, for `generationPolicy`, only the single field it
   * replaces. Container paths come from S2's `_path` locators, never guessed.
   */
  const buildOperations = (): Record<string, unknown>[] | null => {
    if (tab === 'json' && validateJson(json)) return null;
    let doc: SeedDoc;
    try {
      doc = tab === 'json' ? (JSON.parse(json) as SeedDoc) : {};
    } catch {
      return null;
    }
    if (tab === 'form') doc = { requirements: { items: entries.map((e) => ({ ...e.raw, businessKey: e.businessKey, text: e.text, priority: e.priority })) } };
    const ops: Record<string, unknown>[] = [];
    const sectionsTouched = new Set([...Object.keys(doc), ...Object.keys(seed)]);
    for (const sec of sectionsTouched) {
      if (sec === GENERATION_POLICY) continue;
      const value = (doc[sec] ?? {}) as Record<string, unknown[]>;
      const seedValue = seed[sec] ?? {};
      for (const container of new Set([...Object.keys(value), ...Object.keys(seedValue)])) {
        const next = Array.isArray(value[container]) ? value[container] : [];
        const base = seedValue[container] ?? [];
        if (JSON.stringify(base) !== JSON.stringify(next)) ops.push({ op: 'replace', path: `/${sec}/${container}`, value: next });
      }
    }
    if (mayAmendPolicy && codePrefix.trim()) ops.push({ op: 'replace', path: `/${GENERATION_POLICY}/codePrefix`, value: codePrefix.trim() });
    if (mayAmendPolicy && namingConvention) ops.push({ op: 'replace', path: `/${GENERATION_POLICY}/namingConvention`, value: namingConvention });
    return ops.length ? ops : null;
  };

  const operations = buildOperations();

  const submitPatch = () => {
    if (!operations) return;
    const intent = actionIntent('POST', '/stages/patches', stageId, String(operations.length));
    return patch.mutateAsync({ stageId, patch: { operations }, intent }).then((res) => {
      setResult(res);
      setTouched(false);
      seededOnce.current = false;
      void qc.invalidateQueries({ queryKey: qk.blueprintDraft(projectId) });
      void qc.invalidateQueries({ queryKey: qk.blueprintEntries(projectId, sections, 'DRAFT') });
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
        <p className="muted" style={{ fontSize: 12.5 }}>{t('manual.codeOwnershipHint')}</p>
        {isRequirements ? (
          <SegmentedControl
            selected={tab}
            options={[
              { label: t('manual.tabForm'), value: 'form' },
              { label: t('manual.tabJson'), value: 'json' },
            ]}
            onChange={({ value }: { value: string }) => {
              setTouched(true);
              setTab(value === 'json' ? 'json' : 'form');
            }}
          />
        ) : null}

        {sections.length > 0 && entriesQuery.isLoading ? (
          <Loading />
        ) : tab === 'form' ? (
          <div className="stack">
            {entries.map((e, i) => (
              <div key={e.businessKey || i} className="row" style={{ gap: 8, alignItems: 'flex-start' }}>
                <div style={{ flex: 2 }}>
                  <TextInput
                    label={t('inspector.businessKey')}
                    value={e.businessKey}
                    onChange={(v) => { setTouched(true); setEntries((prev) => prev.map((x, j) => (j === i ? { ...x, businessKey: v } : x))); }}
                  />
                </div>
                <div style={{ flex: 2 }}>
                  <TextInput
                    label={t('common.description')}
                    value={e.text}
                    onChange={(v) => { setTouched(true); setEntries((prev) => prev.map((x, j) => (j === i ? { ...x, text: v } : x))); }}
                  />
                </div>
                <div style={{ flex: 1 }}>
                  <SelectInput
                    label={t('common.type')}
                    value={e.priority}
                    onChange={(v) => { setTouched(true); setEntries((prev) => prev.map((x, j) => (j === i ? { ...x, priority: v } : x))); }}
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
            <Button secondary onClick={() => { setTouched(true); setEntries((prev) => [...prev, { businessKey: '', text: '', priority: 'SHOULD', raw: {} }]); }}>
              {t('wizard.addEnvironment')}
            </Button>
          </div>
        ) : (
          <div className="stack">
            <p className="muted" style={{ fontSize: 13 }}>{t('manual.jsonHint')}</p>
            <JsonEditor
              value={json}
              onChange={(v) => { setTouched(true); setJson(v); }}
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

        {mayAmendPolicy ? (
          <div className="stack" style={{ gap: 8 }}>
            <strong style={{ fontSize: 13 }}>{t('manual.policyTitle')}</strong>
            <div className="row" style={{ gap: 8, alignItems: 'flex-start', flexWrap: 'wrap' }}>
              <div style={{ flex: 1, minWidth: 180 }}>
                <TextInput
                  label={t('manual.codePrefixLabel')}
                  value={codePrefix}
                  onChange={setCodePrefix}
                  placeholder={t('manual.policyKeepBlankHint')}
                />
              </div>
              <div style={{ flex: 1, minWidth: 180 }}>
                <SelectInput
                  label={t('manual.namingConventionLabel')}
                  value={namingConvention}
                  onChange={setNamingConvention}
                  options={[
                    { value: '', label: t('manual.policyKeepBlankHint') },
                    ...NAMING_CONVENTIONS.map((c) => ({ value: c, label: c })),
                  ]}
                />
              </div>
            </div>
          </div>
        ) : null}

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
          <AllowedActionButton
            workflowId={workflowId}
            action="patch.submit"
            targetId={stageId}
            onExecute={submitPatch}
            loading={patch.isPending}
            disabled={!operations}
          >
            {t('manual.submitPatch')}
          </AllowedActionButton>
          <AllowedActionButton workflowId={workflowId} action="stage.submit_for_approval" targetId={stageId} tone="secondary" onExecute={submitApproval} loading={approval.isPending}>
            {t('workspace.submitApproval')}
          </AllowedActionButton>
        </div>
        {!operations ? <p className="muted" style={{ fontSize: 12 }}>{t('manual.noChangesHint')}</p> : null}
        <p className="muted" style={{ fontSize: 12 }}>{t('manual.submitApprovalHint')}</p>
      </div>
    </Card>
  );
}
