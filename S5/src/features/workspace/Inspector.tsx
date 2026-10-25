/*
 * UI-WB-005: the right-hand inspector reflects the object selected in the main workspace. The
 * selection is UI state (Zustand); every field it shows is read from S1 (registry / artifact),
 * never inferred (UI-BR-02).
 */
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@dhis2/ui';
import { Heading } from '@/components/typography';
import { useUiStore } from '@/stores/ui';
import { registryOptions } from '@/api/queries';
import { dispositionLabel, statusLabel } from '@/lib/labels';

export function Inspector({ projectId }: { projectId: string }) {
  const { t } = useTranslation();
  const selectedKey = useUiStore((s) => s.selectedObjectKey);
  const open = useUiStore((s) => s.inspectorOpen);
  const toggle = useUiStore((s) => s.toggleInspector);
  const registry = useQuery(registryOptions(projectId));

  if (!open) {
    return (
      <aside className="inspector inspector--collapsed">
        <Button secondary small onClick={toggle} aria-label={t('inspector.expand')}>
          »
        </Button>
      </aside>
    );
  }

  const entry = (registry.data?.items ?? []).find((e) => e.businessKey === selectedKey);

  return (
    <aside className="inspector" aria-label={t('inspector.title')}>
      <div className="row" style={{ justifyContent: 'space-between' }}>
        <Heading level={5} size="small">{t('inspector.title')}</Heading>
        <Button secondary small onClick={toggle} aria-label={t('inspector.collapse')}>
          «
        </Button>
      </div>

      {!selectedKey ? (
        <p className="muted" style={{ marginTop: 16 }}>{t('common.selectToContinue')}</p>
      ) : !entry ? (
        <p className="muted" style={{ marginTop: 16 }}>{selectedKey}</p>
      ) : (
        <dl className="stack" style={{ gap: 10, marginTop: 16 }}>
          <Field label={t('inspector.businessKey')} value={entry.businessKey} />
          <Field label={t('inspector.objectType')} value={entry.dhis2Type ?? '—'} />
          <Field label={t('inspector.disposition')} value={dispositionLabel(entry.disposition) || '—'} />
          <Field label={t('inspector.uid')} value={entry.uid ?? '—'} mono />
          <Field label={t('inspector.code')} value={entry.code ?? '—'} mono />
          <Field label={t('inspector.state')} value={statusLabel(entry.status) || '—'} />
          <Field label={t('inspector.source')} value={entry.sourceEnvironment ?? '—'} />
          <Field label={t('inspector.decision')} value={entry.decisionId ?? '—'} />
          {entry.controlledFields && entry.controlledFields.length > 0 ? (
            <Field label={t('inspector.dependencies')} value={entry.controlledFields.join(', ')} />
          ) : null}
        </dl>
      )}
    </aside>
  );
}

function Field({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="muted" style={{ fontSize: 12 }}>{label}</dt>
      <dd className={mono ? 'mono' : undefined} style={{ margin: '2px 0 0' }}>{value}</dd>
    </div>
  );
}
