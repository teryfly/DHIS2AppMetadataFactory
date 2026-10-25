/*
 * UI-ADM-003 (M1 slice): Governance — model registry (endpoint, key as Secret reference, name,
 * provider class, capabilities, price, enabled, applicable agents), providers, organisation data
 * policy with reason-required relaxation, and usage. Prompt activation is gated until an evaluation
 * exists.
 *
 * UI-ADM-002 as amended (契约 1.7.0): a credential *value* is never read back and never sits in a
 * field after submission, but it can now be entered once through `CredentialField` (S5IF-SEC01),
 * which posts it and fills in the `db://` reference it gets back. Everything else on this page
 * still takes a reference only.
 *
 * The provider card writes: S1 registers a provider from a Secret *reference* and edits it through
 * PATCH /governance/providers/{name}, which touches only the fields the body names (S5IF-GOV02/03).
 * The vendor dropdown is S1's catalogue (S5IF-GOV07), not a list this file maintains.
 */
import { useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Card, Button, DataTable, DataTableHead, DataTableBody, DataTableRow, DataTableCell, Tag, Modal, ModalTitle, ModalContent, ModalActions } from '@dhis2/ui';
import { PageContainer, Loading } from '@/lib/PageContainer';
import { RouteTabs } from '@/components/RouteTabs';
import { EmptyState } from '@/components/EmptyState';
import { Heading } from '@/components/typography';
import { NoticeBar } from '@/components/NoticeBar';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { ACTION } from '@/api/actions';
import { TextInput, TextAreaInput, SelectInput, CheckboxInput, HelpTip } from '@/components/form';
import { CredentialField } from '@/features/secrets/CredentialField';
import { FormShell } from '@/components/FormShell';
import { isApiError } from '@/api/errors';
import { translateErrorCode } from '@/i18n';
import { PROVIDER_CLASSES, isSecretReference } from '@/api/enums';
import {
  agentsOptions,
  governanceModelsOptions,
  governancePolicyOptions,
  governanceProvidersOptions,
  providerCatalogueOptions,
} from '@/api/queries';
import { useRegisterModel, useRegisterProvider, useRelaxPolicy, useUpdateProvider, useUpdateModel, useTestProvider } from '@/api/mutations';
import { CostPanel } from './CostPanel';
import { PromptPanel } from './PromptPanel';
import type * as T from '@/api/types';

/** The catalogue is a starting point, never a cage: this entry keeps both fields free text. */
const CUSTOM_VENDOR = 'custom';

/**
 * The model-name list is typed as one line and stored as an array.  Both half- and full-width
 * separators are accepted because that is what a paste from vendor documentation produces.
 */
function parseModels(text: string): string[] {
  return text
    .split(/[,;，；\n]/)
    .map((name) => name.trim())
    .filter(Boolean);
}

function modelsText(models?: readonly string[] | null): string {
  return (models ?? []).join(', ');
}

function sameModels(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((name, index) => name === b[index]);
}

/**
 * What the runtime itself falls back to when a registration says nothing about a value. The form
 * states them rather than leaving the boxes empty, because empty is not what the server reads: a
 * context window nobody filled in arrives as `0`, and `0` fits no request at all.
 */
const DEFAULT_CONTEXT = '8000';
const DEFAULT_PRIORITY = '100';

/** Every agent named at the fallback position: usable by all of them, taking no one's place. */
const namedForAll = (types: readonly string[]): Record<string, string> =>
  Object.fromEntries(types.map((type) => [type, DEFAULT_PRIORITY]));

/** Two agent→priority maps, compared as the sets of pairs they are. */
function sameMap(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].every((key) => Number(a[key]) === Number(b[key]));
}

/**
 * `applicableAgents` is a map from agent to what that agent may ask of the model (S3's own
 * shape, forwarded whole), never a list — calling `.join` on it takes the page down.
 *
 * Listed in the order the runtime walks it (lower priority first), and named the way the
 * registration form names the same agent, so a row and the box that wrote it say one thing.
 */
function agentSummary(agents: Record<string, unknown> | undefined, label: (type: string) => string): string {
  return Object.entries(agents ?? {})
    .map(([type, value]) => ({
      type,
      order: Number(value),
      text: Array.isArray(value) ? `${label(type)} ${value.join(', ')}` : `${label(type)} ${String(value)}`,
    }))
    .sort((a, b) => a.order - b.order || a.type.localeCompare(b.type))
    .map((row) => row.text)
    .join('; ');
}

/**
 * The governance surface, split into sections. One is on screen at a time and the address names it,
 * so the guide, the sidebar and a quoted link all land on the thing the operator asked for instead
 * of the top of a page six cards deep.
 */
export const SECTION_KEYS = ['models', 'providers', 'policy', 'prompts', 'cost'];

export function GovernanceView() {
  const { t } = useTranslation();
  const params = useParams();
  const section = SECTION_KEYS.includes(params.section ?? '') ? (params.section as string) : 'models';
  // An agent type this build has no words for still reads as itself, not as a missing-key marker.
  const agentLabel = (type: string) => String(t(`agents.${type}`, { defaultValue: type }));
  const models = useQuery(governanceModelsOptions());
  const policy = useQuery(governancePolicyOptions());
  const providers = useQuery(governanceProvidersOptions());
  const catalogue = useQuery(providerCatalogueOptions());
  const [relaxOpen, setRelaxOpen] = useState(false);
  const [editing, setEditing] = useState<T.ProviderView | null>(null);
  const [creating, setCreating] = useState(false);
  const [creatingModel, setCreatingModel] = useState(false);
  /** the row the form is correcting, or nothing when it is registering (S5IF-GOV06) */
  const [modelTarget, setModelTarget] = useState<T.ModelView | null>(null);
  const toggle = useUpdateProvider();
  const updateModel = useUpdateModel();
  /**
   * S5IF-GOV20 — one provider's reachability answer, kept against the row that asked.
   *
   * The button exists because `model endpoint unreachable: ConnectError` on a failed parse says
   * nothing about which of three problems it is: an address nobody filled in, a key the vendor
   * refuses, or a model slower than the request timeout. Asking the endpoint is the only way an
   * operator can tell them apart without reading the model-call table.
   */
  const probe = useTestProvider();
  const [probed, setProbed] = useState<{ name: string; healthy: boolean; detail: string } | null>(null);

  if (models.isLoading) return <Loading />;
  const rows = models.data?.items ?? [];
  const providerRows = providers.data?.items ?? [];
  /**
   * The catalogue names a preset; a stored row only carries its key.  Reading the label from
   * the same document the form used keeps the table and the form from disagreeing, and an
   * unknown key (a preset removed after the row was written) still reads as itself.
   */
  const vendorLabel = (key?: string | null): string => {
    if (!key || key === CUSTOM_VENDOR) return t('governance.vendorCustom');
    return catalogue.data?.items?.find((entry) => entry.key === key)?.displayName ?? key;
  };

  return (
    <PageContainer
      title={t('governance.title')}
      description={t('page.governance')}
      helpKey="governance"
      tabs={
        <RouteTabs
          label={t('governance.title')}
          tabs={SECTION_KEYS.map((key) => ({
            key,
            to: `/governance/${key}`,
            label: t(`governance.section.${key}`),
            active: section === key,
          }))}
        />
      }
    >
      <div className="stack">
        {section === 'models' ? (<>
        <Card>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <Heading level={5} size="small">{t('governance.models')}</Heading>
            {/* S1 has answered `governance.model.register` in the platform list from the start; only
                the interface was missing, which left this a curl-only step (UI-SET-005). */}
            <AllowedActionButton
              workflowId={undefined}
              scope="platform"
              action={ACTION.governanceModelRegister}
              onExecute={() => { setModelTarget(null); setCreatingModel(true); }}
              tone="secondary"
            >
              {t('governance.registerModel')}
            </AllowedActionButton>
          </div>
          {models.isLoading ? (
            <Loading />
          ) : rows.length === 0 ? (
            <EmptyState>{t('governance.modelsEmpty')}</EmptyState>
          ) : (
            <DataTable>
              <DataTableHead>
                <DataTableRow>
                  <DataTableCell tag="th">{t('governance.modelName')}</DataTableCell>
                  <DataTableCell tag="th">{t('governance.provider')}</DataTableCell>
                  <DataTableCell tag="th">{t('governance.endpoint')}</DataTableCell>
                  <DataTableCell tag="th">{t('governance.apiKeyRef')}</DataTableCell>
                  <DataTableCell tag="th">{t('governance.applicableAgents')}</DataTableCell>
                  <DataTableCell tag="th">{t('governance.enabled')}</DataTableCell>
                </DataTableRow>
              </DataTableHead>
              <DataTableBody>
                {rows.map((m) => (
                  <DataTableRow key={m.ref ?? m.id}>
                    <DataTableCell>{m.model}</DataTableCell>
                    <DataTableCell>{m.provider}</DataTableCell>
                    <DataTableCell className="mono">{m.endpoint}</DataTableCell>
                    <DataTableCell className="mono">{m.apiKeySecretRef ?? '—'}</DataTableCell>
                    <DataTableCell>{agentSummary(m.applicableAgents, agentLabel) || t('governance.noAgentsYet')}</DataTableCell>
                    <DataTableCell>
                      <span className="row">
                        {m.enabled ? <Tag positive>{t('common.yes')}</Tag> : <Tag neutral>{t('common.no')}</Tag>}
                        {/* Editing is the same verb as the switch (one PATCH whose body names only
                            what changed), so it is gated by the same action the guard consults. */}
                        <AllowedActionButton
                          workflowId={undefined}
                          scope="platform"
                          action={ACTION.governanceModelEnable}
                          tone="secondary"
                          onExecute={() => { setModelTarget(m); setCreatingModel(true); }}
                        >
                          {t('governance.editModel')}
                        </AllowedActionButton>
                        <AllowedActionButton
                          workflowId={undefined}
                          scope="platform"
                          action={ACTION.governanceModelEnable}
                          tone="secondary"
                          loading={updateModel.isPending}
                          onExecute={() =>
                            updateModel.mutateAsync({
                              ref: String(m.ref ?? m.id),
                              body: { enabled: m.enabled !== true },
                            })
                          }
                        >
                          {m.enabled ? t('governance.disableModel') : t('governance.enableModel')}
                        </AllowedActionButton>
                      </span>
                    </DataTableCell>
                  </DataTableRow>
                ))}
              </DataTableBody>
            </DataTable>
          )}
        </Card>

        {creatingModel ? (
          <ModelForm
            providers={providerRows}
            target={modelTarget}
            onClose={() => setCreatingModel(false)}
            onSaved={() => { setCreatingModel(false); void models.refetch(); }}
          />
        ) : null}
        </>) : null}

        {section === 'policy' ? (<>
        <Card>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <Heading level={5} size="small">{t('governance.policy')}</Heading>
            {/* UI-ADM-003 / FR-UI-009: widening what may reach a model is S1's call to allow,
                read from the platform answer — the page does not decide it locally. */}
            <AllowedActionButton
              workflowId={undefined}
              scope="platform"
              action={ACTION.governancePolicyRelax}
              onExecute={() => setRelaxOpen(true)}
              tone="secondary"
            >
              {t('governance.relaxPolicy')}
            </AllowedActionButton>
          </div>
          {/* S1 answers with the matrix itself: one row per data classification, listing the
              outbound policies that classification may use. Rendering `effective` as a string
              printed [object Object], which is not a policy anybody can read. */}
          <Heading level={6} size="small">{t('governance.policyView')}</Heading>
          <DataTable>
            <DataTableHead>
              <DataTableRow>
                <DataTableCell tag="th">{t('governance.classification')}</DataTableCell>
                <DataTableCell tag="th">{t('governance.allowedPolicies')}</DataTableCell>
              </DataTableRow>
            </DataTableHead>
            <DataTableBody>
              {Object.entries(policy.data?.effective?.allowed ?? {}).map(([classification, policies]) => (
                <DataTableRow key={classification}>
                  <DataTableCell>
                    {t(`governance.classification${classification}`, { defaultValue: classification })}
                  </DataTableCell>
                  <DataTableCell className="mono">
                    {policies.length
                      ? policies.map((value) => t(`environments.policy${value}`, { defaultValue: value })).join(' · ')
                      : t('governance.nothingAllowed')}
                  </DataTableCell>
                </DataTableRow>
              ))}
            </DataTableBody>
          </DataTable>
          {policy.data?.effective?.warnings?.length ? (
            <p className="field__error">{policy.data.effective.warnings.map(String).join('; ')}</p>
          ) : null}
        </Card>
        </>) : null}

        {section === 'prompts' ? (<>
        <div className="row" style={{ gap: 24, alignItems: 'flex-start' }}>
          <PromptPanel />
        </div>
        </>) : null}

        {section === 'cost' ? (<>
        {/* UI-ADM-005: the usage rollup and the budget switch (AI-COST-001~003, S5IF-GOV18) */}
        <CostPanel />
        </>) : null}

        {section === 'providers' ? (<>
        <Card>
          <div className="row" style={{ justifyContent: 'space-between' }}>
            <Heading level={5} size="small">{t('governance.providers')}</Heading>
            <AllowedActionButton
              workflowId={undefined}
              scope="platform"
              action={ACTION.governanceProviderRegister}
              tone="secondary"
              onExecute={() => setCreating(true)}
            >
              {t('governance.registerProvider')}
            </AllowedActionButton>
          </div>
          {providerRows.length === 0 ? <p className="muted">{t('governance.providersEmpty')}</p> : null}
          <DataTable>
            <DataTableHead>
              <DataTableRow>
                <DataTableCell tag="th">{t('governance.providerName')}</DataTableCell>
                <DataTableCell tag="th">{t('governance.vendor')}</DataTableCell>
                <DataTableCell tag="th">{t('governance.providerClass')}</DataTableCell>
                <DataTableCell tag="th">{t('governance.endpoint')}</DataTableCell>
                <DataTableCell tag="th">{t('governance.apiKeyRef')}</DataTableCell>
                <DataTableCell tag="th">{t('governance.enabled')}</DataTableCell>
                <DataTableCell tag="th">{t('common.actions')}</DataTableCell>
              </DataTableRow>
            </DataTableHead>
            <DataTableBody>
              {providerRows.map((p) => (
                <DataTableRow key={p.name}>
                  <DataTableCell>{p.name}</DataTableCell>
                  <DataTableCell>
                    {vendorLabel(p.vendor)}
                    {p.suggestedModels?.length ? (
                      <span className="muted" style={{ fontSize: 12 }}>
                        {' · '}
                        {t('governance.suggestedModelsCount', { count: p.suggestedModels.length })}
                      </span>
                    ) : null}
                  </DataTableCell>
                  <DataTableCell>{t(`governance.class${p.providerClass}`, { defaultValue: String(p.providerClass) })}</DataTableCell>
                  <DataTableCell className="mono">{p.endpoint}</DataTableCell>
                  <DataTableCell className="mono">{p.apiKeySecretRef ?? '—'}</DataTableCell>
                  <DataTableCell>{p.enabled ? <Tag positive>{t('common.yes')}</Tag> : <Tag neutral>{t('common.no')}</Tag>}</DataTableCell>
                  <DataTableCell>
                    <div className="row">
                      <AllowedActionButton
                        workflowId={undefined}
                        scope="platform"
                        action={ACTION.governanceProviderEnable}
                        tone="secondary"
                        onExecute={() => setEditing(p)}
                      >
                        {t('governance.editProvider')}
                      </AllowedActionButton>
                      {/* An edit that names only `enabled` leaves the address and the reference
                          alone — that is what makes switching a provider off safe here. */}
                      <AllowedActionButton
                        workflowId={undefined}
                        scope="platform"
                        action={ACTION.governanceProviderEnable}
                        tone="destructive"
                        loading={toggle.isPending}
                        onExecute={async () => {
                          await toggle.mutateAsync({ name: p.name ?? '', body: { enabled: !p.enabled } });
                          void providers.refetch();
                        }}
                      >
                        {p.enabled ? t('governance.disableProvider') : t('governance.enableProvider')}
                      </AllowedActionButton>
                      <Button
                        secondary
                        small
                        loading={probe.isPending && probed?.name === p.name}
                        onClick={() => {
                          const name = p.name ?? '';
                          if (!name) return;
                          void probe
                            .mutateAsync({ name })
                            .then((answer) => setProbed({ name, healthy: !!answer?.healthy, detail: String(answer?.detail ?? '') }))
                            .catch(() => setProbed({ name, healthy: false, detail: t('governance.probeFailed') }));
                        }}
                      >
                        {t('governance.probe')}
                      </Button>
                      {probed && probed.name === p.name ? (
                        <div className={probed.healthy ? 'muted' : 'text-critical'} style={{ fontSize: 12 }} role="status">
                          {probed.healthy ? t('governance.probeOk') : t('governance.probeBad')}
                          {probed.detail ? ` — ${probed.detail}` : ''}
                        </div>
                      ) : null}
                    </div>
                  </DataTableCell>
                </DataTableRow>
              ))}
            </DataTableBody>
          </DataTable>
        </Card>
        </>) : null}
      </div>

      {(editing || creating) && (
        <ProviderForm
          provider={editing}
          onClose={() => { setEditing(null); setCreating(false); }}
          onSaved={() => { setEditing(null); setCreating(false); void providers.refetch(); }}
        />
      )}

      {relaxOpen ? <RelaxationForm policy={policy.data} onClose={() => setRelaxOpen(false)} /> : null}
    </PageContainer>
  );
}

/**
 * UI-ADM-003: ask for a widening of the organisation's data policy.
 *
 * The choices come from the two matrices S1 answers with, never from a list typed into this
 * file: what a level may still reach is `ceiling[level]` minus `allowed[level]`, and offering
 * anything else is inviting the 403 S3 reserves for a request past the ceiling. A level with an
 * empty difference has nothing to ask for, so the form says that instead of presenting a
 * checkbox that cannot be legally ticked — PHI_PII is that level by construction (AC-POL-02).
 */
function RelaxationForm({ policy, onClose }: { policy: T.PolicyView | undefined; onClose: () => void }) {
  const { t } = useTranslation();
  const allowed = policy?.effective?.allowed ?? {};
  const ceiling = policy?.ceiling?.allowed ?? {};
  const levels = Object.keys(allowed);
  const [level, setLevel] = useState(
    levels.find((name) => (ceiling[name]?.length ?? 0) > (allowed[name]?.length ?? 0)) ?? levels[0] ?? '',
  );
  const [picked, setPicked] = useState<string[]>([]);
  const [reason, setReason] = useState('');
  const relax = useRelaxPolicy();

  const candidates = (ceiling[level] ?? []).filter((value) => !(allowed[level] ?? []).includes(value));
  // a widening picked for one level must not ride along when the operator changes their mind
  const chosen = picked.filter((value) => candidates.includes(value));
  const valid = level !== '' && chosen.length > 0 && reason.trim() !== '' && !relax.isPending;

  return (
    <Modal position="middle" hide={false} onClose={onClose}>
      <ModalTitle>{t('governance.relaxTitle')}</ModalTitle>
      <ModalContent>
        <div className="stack">
          <NoticeBar tone="warning" title={t('governance.relaxPermanentTitle')}>
            {t('governance.relaxPermanentBody')}
          </NoticeBar>
          {relax.error && isApiError(relax.error) ? (
            <NoticeBar tone="error" title={t('error.title')}>
              <span>{translateErrorCode(relax.error.errorCode, relax.error.message)}</span>
            </NoticeBar>
          ) : null}
          <SelectInput
            label={t('governance.relaxLevel')}
            value={level}
            onChange={setLevel}
            options={levels.map((name) => ({
              value: name,
              label: t(`governance.classification${name}`, { defaultValue: name }),
            }))}
            helpText={t('governance.relaxLevelHint')}
          />
          {candidates.length ? (
            candidates.map((value) => (
              <CheckboxInput
                key={value}
                label={t(`environments.policy${value}`, { defaultValue: value })}
                checked={chosen.includes(value)}
                onChange={(on) => setPicked(on ? [...chosen, value] : chosen.filter((item) => item !== value))}
              />
            ))
          ) : (
            <p className="field__error">
              {level === 'PHI_PII' ? t('governance.relaxPhiNever') : t('governance.relaxCeilingReached')}
            </p>
          )}
          <TextAreaInput
            label={t('governance.relaxReason')}
            value={reason}
            rows={2}
            onChange={setReason}
            required
            error={reason || !chosen.length ? undefined : t('governance.relaxReasonMissing')}
          />
        </div>
      </ModalContent>
      <ModalActions>
        <Button secondary onClick={onClose}>{t('common.cancel')}</Button>
        <AllowedActionButton
          workflowId={undefined}
          scope="platform"
          action={ACTION.governancePolicyRelax}
          loading={relax.isPending}
          disabled={!valid}
          onExecute={async () => {
            await relax.mutateAsync({
              change: { level, addClasses: chosen },
              reason: reason.trim(),
              scope: 'organization',
            });
            onClose();
          }}
        >
          {t('common.confirm')}
        </AllowedActionButton>
      </ModalActions>
    </Modal>
  );
}

/**
 * Register or edit one model provider. A registration needs every field; an edit sends what the
 * operator touched, because S1's PATCH reads an absent field as "unchanged" — sending the whole
 * row back would be harmless, sending a partial one through the *register* path would not.
 */
export function ProviderForm({
  provider,
  onClose,
  onSaved,
  embedded,
}: {
  provider: T.ProviderView | null;
  onClose: () => void;
  onSaved: () => void;
  /** Rendered inside the getting-started step rather than as a modal (UI-SET-006). */
  embedded?: boolean;
}) {
  const { t } = useTranslation();
  const catalogue = useQuery(providerCatalogueOptions());
  const presets = catalogue.data?.items ?? [];
  // A stored row keeps the preset it was started from, even after its address was edited to a
  // proxy; the legacy default is `custom`, which reads honestly as "no preset recorded".
  const [vendorKey, setVendorKey] = useState(provider?.vendor || CUSTOM_VENDOR);
  const [name, setName] = useState(provider?.name ?? '');
  const [providerClass, setProviderClass] = useState(String(provider?.providerClass ?? 'PRIVATE_NETWORK'));
  const [endpoint, setEndpoint] = useState(provider?.endpoint ?? '');
  const [apiKeySecretRef, setApiKeySecretRef] = useState(provider?.apiKeySecretRef ?? '');
  // One line in the form, an array on the wire.  Suggestions only: `ai.model` remains the registry.
  const [suggestedModels, setSuggestedModels] = useState(modelsText(provider?.suggestedModels));
  // S3 stores this column and ignores it: `provider_for()` constructs the one client that exists
  // whatever the row says (openai_compat.py:248). An editable box here would promise a choice that
  // changes nothing, so it is shown as what it is and sent unchanged.
  const adapter = provider?.adapter ?? 'openai_compat';
  const [enabled, setEnabled] = useState(provider?.enabled ?? true);

  const create = useRegisterProvider();
  const edit = useUpdateProvider();
  const failure = create.error ?? edit.error;
  const pending = create.isPending || edit.isPending;

  const preset = presets.find((row) => row.key === vendorKey);

  /**
   * Picking a vendor fills the address and model list that vendor's documentation states — but
   * only where the operator has not already typed something of their own.  Comparing against the
   * *previous* preset's values is what carries a hand-edited proxy address across a vendor change;
   * the new preset must not silently overwrite a value nobody asked it to.
   */
  const chooseVendor = (key: string) => {
    const previous = presets.find((row) => row.key === vendorKey);
    const next = presets.find((row) => row.key === key);
    if (endpoint.trim() === '' || endpoint === (previous?.endpoint ?? '')) {
      setEndpoint(next?.endpoint ?? endpoint);
    }
    const currentModels = suggestedModels.trim();
    if (currentModels === '' || currentModels === modelsText(previous?.models)) {
      setSuggestedModels(modelsText(next?.models));
    }
    setVendorKey(key);
  };
  const suggestedKeyRef = preset?.keyRefSuggestion ?? '';
  // A row may carry a preset key this build no longer lists. The picker still has to show what
  // is stored rather than silently rendering the first option and inviting an accidental change.
  const vendorOptions = [
    ...presets.map((entry) => ({
      value: String(entry.key),
      label: `${entry.displayName}${entry.local ? ` · ${t('governance.vendorLocal')}` : ''}`,
    })),
    ...(vendorKey !== CUSTOM_VENDOR && !presets.some((entry) => String(entry.key) === vendorKey)
      ? [{ value: vendorKey, label: vendorKey }]
      : []),
    { value: CUSTOM_VENDOR, label: t('governance.vendorCustom') },
  ];

  // S3 rejects a call only when the address is empty, and never inspects its scheme — a model
  // served over plain http on a private network is legitimate, and the data policy governs it.
  // So the form asks for an absolute http(s) URL and leaves the rest to the server.
  // The Key reference is only required to *be* a reference, and only when one was typed: an address
  // that authenticates with nothing — every `local` catalogue entry, and the reason 契约 1.7.1 made
  // the field optional — has no key to name, and inventing one registers fine and then fails the
  // first call. An emptied box on an edit is not a removal either: PATCH takes `min_length=1` and
  // reads an absent field as "leave it", so the cleared value simply does not travel.
  const valid =
    name.trim() !== '' &&
    /^https?:\/\//.test(endpoint.trim()) &&
    (apiKeySecretRef.trim() === '' || isSecretReference(apiKeySecretRef)) &&
    (!provider || Object.keys(patchBody()).length > 0);

  /**
   * An edit sends only what changed. S1's PATCH reads an absent field as "leave it", and S3's
   * register path — which a whole-record edit would have to avoid — overwrites every column, so
   * resending the row is not the same request as sending the two fields that moved.
   */
  function patchBody(): T.PatchProviderRequest {
    const out: Record<string, unknown> = {};
    if (providerClass !== String(provider?.providerClass ?? '')) out.providerClass = providerClass;
    if (endpoint.trim() !== (provider?.endpoint ?? '')) out.endpoint = endpoint.trim();
    // an empty box is not a value PATCH can carry (`min_length=1`), and absence is "leave it"
    if (apiKeySecretRef.trim() !== '' && apiKeySecretRef.trim() !== (provider?.apiKeySecretRef ?? ''))
      out.apiKeySecretRef = apiKeySecretRef.trim();
    if (enabled !== (provider?.enabled ?? true)) out.enabled = enabled;
    if (vendorKey !== (provider?.vendor || CUSTOM_VENDOR)) out.vendor = vendorKey;
    const models = parseModels(suggestedModels);
    if (!sameModels(models, provider?.suggestedModels ?? [])) out.suggestedModels = models;
    return out as T.PatchProviderRequest;
  }

  return (
    <FormShell
      embedded={embedded}
      title={provider ? t('governance.editProvider') : t('governance.registerProvider')}
      onClose={onClose}
      footer={
        <>
          <Button secondary onClick={onClose}>{t('common.cancel')}</Button>
          <AllowedActionButton
            workflowId={undefined}
            scope="platform"
            action={provider ? ACTION.governanceProviderEnable : ACTION.governanceProviderRegister}
            loading={pending}
            disabled={!valid}
            onExecute={async () => {
              if (provider) {
                await edit.mutateAsync({ name: provider.name ?? '', body: patchBody() });
              } else {
                await create.mutateAsync({
                  name: name.trim(),
                  providerClass: providerClass as T.RegisterProviderRequest['providerClass'],
                  endpoint: endpoint.trim(),
                  apiKeySecretRef: apiKeySecretRef.trim(),
                  adapter,
                  enabled,
                  vendor: vendorKey,
                  suggestedModels: parseModels(suggestedModels),
                });
              }
              onSaved();
            }}
          >
            {t('common.save')}
          </AllowedActionButton>
        </>
      }
    >
      <NoticeBar tone="info" title={t('environments.secretsOnlyTitle')}>
        {t('governance.keyRefHint')}
      </NoticeBar>
          {failure && isApiError(failure) ? (
            <NoticeBar tone="error" title={t('error.title')}>
              <span>{translateErrorCode(failure.errorCode, failure.message)}</span>
            </NoticeBar>
          ) : null}
          <SelectInput
            label={t('governance.vendor')}
            value={vendorKey}
            onChange={chooseVendor}
            options={vendorOptions}
          />
          {preset ? (
            <div className="muted" style={{ fontSize: 13 }}>
              {preset.note ? <p>{preset.note}</p> : null}
              {preset.local ? <p>{t('governance.vendorLocalHelp')}</p> : null}
              {preset.docsUrl ? (
                <p>
                  <a href={preset.docsUrl} target="_blank" rel="noreferrer">
                    {t('governance.vendorDocs')}
                  </a>
                </p>
              ) : null}
            </div>
          ) : null}
          <TextInput label={t('governance.providerName')} value={name} onChange={setName} required disabled={!!provider} />
          <SelectInput
            label={t('governance.providerClass')}
            value={providerClass}
            onChange={setProviderClass}
            options={PROVIDER_CLASSES.map((value) => ({
              value,
              label: t(`governance.class${value}`, { defaultValue: value }),
            }))}
          />
          <HelpTip label={t('common.whyThis')} text={t('governance.helpProviderClass')} />
          <TextInput
            label={t('governance.endpoint')}
            value={endpoint}
            onChange={setEndpoint}
            type="url"
            required
            placeholder="https://llm.internal/v1"
            helpText={t('governance.endpointHelp')}
          />
          <CredentialField
            label={t('governance.apiKeyRef')}
            value={apiKeySecretRef}
            onChange={setApiKeySecretRef}
            suggestedName={`llm-${(name.trim() || 'provider').toLowerCase().replace(/[^a-z0-9._-]+/g, '-')}`}
            helpText={t('governance.keyOptionalHelp')}
            error={apiKeySecretRef.trim() !== '' && !isSecretReference(apiKeySecretRef) ? t('environments.referenceRequired') : undefined}
          />
          {/* The catalogue's suggestion is offered, never stamped in: an env:// reference that
              the deployment never set registers cleanly and fails the first call. */}
          {suggestedKeyRef && apiKeySecretRef.trim() === '' ? (
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <Button secondary small type="button" onClick={() => setApiKeySecretRef(suggestedKeyRef)}>
                {t('governance.useSuggestedKey', { ref: suggestedKeyRef })}
              </Button>
            </div>
          ) : null}
          <TextInput
            label={t('governance.suggestedModels')}
            value={suggestedModels}
            onChange={setSuggestedModels}
            placeholder={t('governance.suggestedModelsPlaceholder')}
            helpText={t('governance.suggestedModelsHelp')}
          />
          <p className="muted" style={{ fontSize: 13 }}>
            {t('governance.adapterFixed', { adapter })}
          </p>
          <CheckboxInput label={t('governance.enabled')} checked={enabled} onChange={setEnabled} />
    </FormShell>
  );
}

/**
 * `POST /governance/models` (S5IF-GOV05, UI-SET-005). The provider is chosen from what is already
 * registered because the address and the key reference are read from that row — a model that could
 * type its own endpoint would keep the old one after the provider moved.
 *
 * Every box the runtime reads arrives filled, and carries the consequence of its value one click
 * away. Leaving them empty was not a neutral default: an unnamed agent never sees the model at all,
 * and a context window nobody typed reached the server as `0`, which fits no request — so the form
 * states the numbers the runtime itself falls back to, and says what each one decides.
 */
export function ModelForm({
  providers,
  target,
  onClose,
  onSaved,
  embedded,
}: {
  providers: T.ProviderView[];
  /** the row being corrected, or nothing when registering a new model (S5IF-GOV06) */
  target?: T.ModelView | null;
  onClose: () => void;
  onSaved: () => void;
  /** Rendered inside the getting-started step rather than as a modal (UI-SET-006). */
  embedded?: boolean;
}) {
  const { t } = useTranslation();
  // An agent type this build has no words for still reads as itself, not as a missing-key marker.
  const agentLabel = (type: string) => String(t(`agents.${type}`, { defaultValue: type }));
  const stored = target ?? null;
  const caps = stored?.capabilities;
  const [provider, setProvider] = useState(stored?.provider ?? providers[0]?.name ?? '');
  const [modelId, setModelId] = useState(stored?.model ?? '');
  const [structuredOutput, setStructuredOutput] = useState(caps ? caps.structuredOutput !== false : true);
  const [vision, setVision] = useState(caps?.vision === true);
  const [maxContextTokens, setMaxContextTokens] = useState(
    caps?.maxContextTokens ? String(caps.maxContextTokens) : DEFAULT_CONTEXT,
  );
  const [priceIn, setPriceIn] = useState(String(stored?.unitPriceInputPer1k ?? 0));
  const [priceOut, setPriceOut] = useState(String(stored?.unitPriceOutputPer1k ?? 0));
  const [enabled, setEnabled] = useState(stored?.enabled !== false);
  /** the agents this model is named for; one never named never sees it. */
  const [picked, setPicked] = useState<string[]>(() => Object.keys(stored?.applicableAgents ?? {}));
  /** agent type → position in that agent's list; lower is tried first. */
  const [priorities, setPriorities] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(stored?.applicableAgents ?? {}).map(([type, value]) => [type, String(value)])),
  );
  const [showPriorities, setShowPriorities] = useState(false);
  /** the catalogue answers after the form opens: seed once, then leave the operator's choice alone */
  const seeded = useRef(!!stored);

  const catalogue = useQuery(providerCatalogueOptions());
  const agents = useQuery(agentsOptions());
  // The provider row's own suggestions come first: they are what the operator saw and saved when
  // registering it, and they survive an edited address.  A legacy row (stored before the field
  // existed) falls back to the catalogue entry its vendor or endpoint names, and free text stays
  // the primary input — a private gateway, fine-tune or renamed deployment is normal.
  const providerRow = providers.find((row) => row.name === provider);
  const preset = catalogue.data?.items?.find(
    (row) =>
      (providerRow?.vendor &&
        providerRow.vendor !== CUSTOM_VENDOR &&
        row.key === providerRow.vendor) ||
      (row.endpoint && row.endpoint === providerRow?.endpoint),
  );
  const suggestions = providerRow?.suggestedModels?.length
    ? providerRow.suggestedModels
    : preset?.models ?? [];
  const agentTypes = (agents.data?.items ?? []).map((row) => String(row.agentType ?? '')).filter((type) => type !== '');

  useEffect(() => {
    const types = (agents.data?.items ?? []).map((row) => String(row.agentType ?? '')).filter((type) => type !== '');
    if (seeded.current || types.length === 0) return;
    seeded.current = true;
    setPicked(types);
    setPriorities(namedForAll(types));
  }, [agents.data]);

  const create = useRegisterModel();
  const edit = useUpdateModel();
  const failure = create.error ?? edit.error;
  // an empty box is the stated default, not a zero: `0` is what makes a model refuse every request
  const contextTokens = maxContextTokens.trim() === '' ? Number(DEFAULT_CONTEXT) : Number(maxContextTokens);
  const windowOk = Number.isFinite(contextTokens) && contextTokens > 0;
  const numbersOk = [priceIn, priceOut, ...picked.map((type) => priorities[type] ?? '')].every(
    (value) => value === '' || Number.isFinite(Number(value)),
  );
  const applicableAgents = Object.fromEntries(
    picked.map((type) => {
      const entered = priorities[type]?.trim() ?? '';
      const value = entered === '' ? Number(DEFAULT_PRIORITY) : Number(entered);
      return [type, Number.isFinite(value) ? value : Number(DEFAULT_PRIORITY)];
    }),
  );
  const toggleAgent = (type: string, on: boolean) =>
    setPicked((current) => (on ? [...current.filter((x) => x !== type), type] : current.filter((x) => x !== type)));
  const pickAll = () => {
    setPicked([...agentTypes]);
    setPriorities(namedForAll(agentTypes));
  };

  /**
   * Only what the operator moved; an unchanged field is left off the wire entirely.
   *
   * `endpoint` and `apiKeySecretRef` are never sent, which is the point of this body: registration
   * writes NULL over a column it omits, so an edit that did not re-type the model's own address
   * would have moved the model onto the provider's — quietly, and past the point where this form
   * could take it back.
   */
  const patchBody = (): T.PatchModelRequest => {
    if (!stored) return {};
    const out: Record<string, unknown> = {};
    const capabilityPatch: Record<string, unknown> = {};
    if (structuredOutput !== (caps?.structuredOutput ?? false)) capabilityPatch.structuredOutput = structuredOutput;
    if (vision !== (caps?.vision ?? false)) capabilityPatch.vision = vision;
    if (contextTokens !== Number(caps?.maxContextTokens)) capabilityPatch.maxContextTokens = contextTokens;
    if (Object.keys(capabilityPatch).length) out.capabilities = capabilityPatch;
    if ((Number(priceIn) || 0) !== Number(stored.unitPriceInputPer1k ?? 0)) out.unitPriceInputPer1k = Number(priceIn) || 0;
    if ((Number(priceOut) || 0) !== Number(stored.unitPriceOutputPer1k ?? 0)) out.unitPriceOutputPer1k = Number(priceOut) || 0;
    if (!sameMap(stored.applicableAgents ?? {}, applicableAgents)) out.applicableAgents = applicableAgents;
    if (enabled !== (stored.enabled !== false)) out.enabled = enabled;
    return out as T.PatchModelRequest;
  };
  const unchanged = !!stored && Object.keys(patchBody()).length === 0;
  const pending = create.isPending || edit.isPending;

  return (
    <FormShell
      embedded={embedded}
      title={stored ? t('governance.editModel') : t('governance.registerModel')}
      onClose={onClose}
      footer={
        <>
          <Button secondary onClick={onClose}>{t('common.cancel')}</Button>
          <AllowedActionButton
            workflowId={undefined}
            scope="platform"
            action={stored ? ACTION.governanceModelEnable : ACTION.governanceModelRegister}
            loading={pending}
            disabled={!provider || modelId.trim() === '' || !numbersOk || !windowOk || unchanged}
            onExecute={async () => {
              if (stored) {
                await edit.mutateAsync({ ref: String(stored.ref ?? stored.id), body: patchBody() });
              } else {
                await create.mutateAsync({
                  provider,
                  model: modelId.trim(),
                  enabled,
                  capabilities: {
                    structuredOutput,
                    vision,
                    // always sent: an absent key is what the server reads as a zero window
                    maxContextTokens: contextTokens,
                  },
                  unitPriceInputPer1k: Number(priceIn) || 0,
                  unitPriceOutputPer1k: Number(priceOut) || 0,
                  applicableAgents,
                });
              }
              onSaved();
            }}
          >
            {t('common.save')}
          </AllowedActionButton>
        </>
      }
    >
          {providers.length === 0 ? <NoticeBar tone="warning" title={t('governance.noProvidersYet')} /> : null}
          {failure && isApiError(failure) ? (
            <NoticeBar tone="error" title={t('error.title')}>
              <span>{translateErrorCode(failure.errorCode, failure.message)}</span>
            </NoticeBar>
          ) : null}
          <SelectInput
            label={t('governance.provider')}
            value={provider}
            onChange={setProvider}
            // a model's provider is half of its reference; changing one means registering the other
            disabled={!!stored}
            options={providers.map((row) => ({ value: String(row.name), label: String(row.name) }))}
          />
          <HelpTip label={t('common.whyThis')} text={t('governance.helpProvider')} />
          <TextInput label={t('governance.modelId')} value={modelId} onChange={setModelId} disabled={!!stored} required placeholder="gpt-x-large" />
          <HelpTip label={t('common.whyThis')} text={t('governance.helpModelId')} />
          {/* a shortcut into a box the operator cannot edit is not a shortcut */}
          {suggestions.length > 0 && !stored ? (
            <div className="row" style={{ gap: 6, flexWrap: 'wrap' }}>
              <span className="muted" style={{ fontSize: 13 }}>{t('governance.modelSuggestions')}</span>
              {suggestions.map((name) => (
                <Button
                  key={name}
                  secondary
                  small
                  type="button"
                  // a suggestion is a shortcut into the same text box, never a separate value
                  onClick={() => setModelId(String(name))}
                >
                  {name}
                </Button>
              ))}
            </div>
          ) : null}
          <CheckboxInput label={t('governance.structuredOutput')} checked={structuredOutput} onChange={setStructuredOutput} />
          <HelpTip label={t('common.whyThis')} text={t('governance.helpStructured')} />
          <CheckboxInput label={t('governance.vision')} checked={vision} onChange={setVision} />
          <HelpTip label={t('common.whyThis')} text={t('governance.helpVision')} />
          <TextInput
            label={t('governance.maxContextTokens')}
            value={maxContextTokens}
            onChange={setMaxContextTokens}
            type="number"
            error={!windowOk ? t('governance.contextMustBePositive') : numbersOk ? undefined : t('governance.numbersOnly')}
          />
          <HelpTip label={t('common.whyThis')} text={t('governance.helpMaxContext')} />
          <TextInput label={t('governance.priceIn')} value={priceIn} onChange={setPriceIn} type="number" />
          <TextInput label={t('governance.priceOut')} value={priceOut} onChange={setPriceOut} type="number" />
          <HelpTip label={t('common.whyThis')} text={t('governance.helpPrices')} />
          {agentTypes.length > 0 ? (
            <div className="field">
              <span className="field__label">{t('governance.applicableAgents')}</span>
              {/* a group of checkboxes says so: the visible heading above them is not an association
                  a screen reader can use, and the list of eight reads as nothing without one */}
              <div className="row" style={{ gap: 12, flexWrap: 'wrap' }} role="group" aria-label={t('governance.applicableAgents')}>
                {agentTypes.map((type) => (
                  <CheckboxInput
                    key={type}
                    label={agentLabel(type)}
                    checked={picked.includes(type)}
                    onChange={(on) => toggleAgent(type, on)}
                  />
                ))}
              </div>
              <div className="row" style={{ gap: 8 }}>
                <Button secondary small type="button" disabled={picked.length === agentTypes.length} onClick={pickAll}>
                  {t('governance.selectAllAgents')}
                </Button>
                <Button secondary small type="button" aria-expanded={showPriorities} onClick={() => setShowPriorities((current) => !current)}>
                  {t('governance.editPriorities')}
                </Button>
              </div>
            </div>
          ) : null}
          <HelpTip label={t('common.whyThis')} text={t('governance.helpAgents')} />
          {/* Outside the collapsible panel on purpose: every agent goes out with the fallback
              number whether or not the panel is ever opened, so an operator who never expands it is
              still setting priorities — and a main model left at 100 behind a stub at 1 is never
              tried, because S3's select() walks applicableAgents ascending. */}
          <HelpTip label={t('common.whyThis')} text={t('governance.helpPriority')} />
          {showPriorities ? (
            <>
              <div
                className="row"
                style={{ gap: 12, flexWrap: 'wrap' }}
                role="group"
                aria-label={t('governance.priority')}
              >
                {picked.map((type) => (
                  <TextInput
                    key={type}
                    label={agentLabel(type)}
                    value={priorities[type] ?? DEFAULT_PRIORITY}
                    onChange={(value) => setPriorities((current) => ({ ...current, [type]: value }))}
                    type="number"
                    inputWidth="90"
                  />
                ))}
              </div>
            </>
          ) : null}
          {picked.length === 0 ? <NoticeBar tone="warning" title={t('governance.noAgentsSelected')} /> : null}
          <CheckboxInput label={t('governance.enabled')} checked={enabled} onChange={setEnabled} />
          <HelpTip label={t('common.whyThis')} text={t('governance.helpEnabled')} />
    </FormShell>
  );
}
