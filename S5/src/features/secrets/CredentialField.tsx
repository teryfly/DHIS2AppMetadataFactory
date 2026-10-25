/*
 * S5IF-SEC01 / UI-ADM-002 (as amended): the one control in this console that accepts a credential
 * *value*, and the reason it can do so is that nothing else ever sees it again.
 *
 * The value lives in this component's state between the keystroke and the POST, and nowhere else:
 * not in localStorage, not in the URL, not in a query cache, not in a toast. After a successful
 * save the fields are cleared and what remains on screen is the reference S1 returned. That is the
 * whole bargain FR-SEC-007 strikes — the browser sends a credential once and never receives it.
 *
 * It renders beside a reference field rather than replacing it, because `env://` is still a normal
 * answer: a deployment that keeps its tokens in the environment file must not be pushed through
 * this control.
 */
import { useEffect, useId, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery } from '@tanstack/react-query';
import { Button } from '@dhis2/ui';
import { TextInput, SelectInput } from '@/components/form';
import { AllowedActionButton } from '@/components/AllowedActionButton';
import { ACTION } from '@/api/actions';
import { secretsOptions } from '@/api/queries';
import { useSaveSecret } from '@/api/mutations';
import { isApiError } from '@/api/errors';

interface Props {
  label: string;
  /** The reference this field currently holds (`env://…`, `db://…` or empty). */
  value: string;
  onChange: (reference: string) => void;
  /** Slug proposed for a new credential, e.g. `dhis2-clinic-dev-discovery`. */
  suggestedName: string;
  /**
   * `basic` composes `user:password` before storing it, because that is the shape S4's Basic
   * authentication requires (`factory_dhis2/client/auth.py`) — the two inputs are a convenience,
   * the stored secret is one value.
   */
  shape?: 'single' | 'basic';
  error?: string;
  helpText?: string;
  disabled?: boolean;
}

export function CredentialField({
  label,
  value,
  onChange,
  suggestedName,
  shape = 'single',
  error,
  helpText,
  disabled,
}: Props) {
  const { t } = useTranslation();
  const stored = useQuery(secretsOptions());
  const save = useSaveSecret();
  const [open, setOpen] = useState(false);
  // A disclosure needs to say it is one, and needs to put the caret where the work is: without the
  // focus move, a keyboard operator presses "Enter a new credential" and is still sitting on the
  // button when the panel appears below.
  const panelId = useId();
  const panelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (!open) return;
    panelRef.current?.querySelector<HTMLElement>('input, textarea, select')?.focus();
  }, [open]);
  const [name, setName] = useState(suggestedName);
  const [token, setToken] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [savedRef, setSavedRef] = useState('');
  const [failure, setFailure] = useState('');

  const known = stored.data?.items ?? [];
  const material = shape === 'basic' ? `${username}:${password}` : token;
  const canSave = name.trim().length >= 2 && material.trim().length > 0 && !save.isPending;

  /** Drop every trace of the value, whatever the outcome of the request was. */
  const clearSecrets = () => {
    setToken('');
    setUsername('');
    setPassword('');
  };

  const submit = async () => {
    setFailure('');
    try {
      const view = await save.mutateAsync({ name: name.trim(), material });
      onChange(view.ref);
      setSavedRef(view.ref);
      clearSecrets();
      setOpen(false);
    } catch (cause) {
      clearSecrets();
      // 503 means the store is not configured here at all; say so in terms of what to do instead
      setFailure(isApiError(cause) && cause.errorCode === 'SECRET_UNAVAILABLE' ? t('secrets.unavailable') : t('secrets.failed'));
    }
  };

  return (
    <div className="stack" data-testid="credential-field">
      <TextInput
        label={label}
        value={value}
        onChange={onChange}
        error={error}
        helpText={helpText ?? t('secrets.refHelp')}
        placeholder={shape === 'basic' ? 'env://CLINIC_DISCOVERY' : 'env://MODEL_API_KEY'}
        disabled={disabled}
        autoComplete="off"
      />

      <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
        <Button secondary small type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen((v) => !v)} disabled={disabled}>
          {open ? t('secrets.cancel') : t('secrets.enter')}
        </Button>
        {known.length > 0 ? (
          <SelectInput
            // an empty value is not a choice, so the picker sits on the placeholder until picked
            label={t('secrets.useExisting')}
            value=""
            onChange={onChange}
            helpText={t('secrets.useExistingPlaceholder')}
            disabled={disabled}
            options={known.map((item) => ({ label: String(item.name), value: String(item.ref) }))}
          />
        ) : null}
      </div>

      {savedRef ? <p className="muted">{t('secrets.saved', { ref: savedRef })}</p> : null}
      {failure ? <p className="field__error" role="alert">{failure}</p> : null}

      {open ? (
        <div id={panelId} ref={panelRef} className="panel" style={{ padding: 12 }}>
          <p className="muted" style={{ fontSize: 13 }}>{t('secrets.whyBody')}</p>
          <TextInput
            label={t('secrets.name')}
            value={name}
            onChange={setName}
            helpText={t('secrets.nameHint')}
            required
            disabled={disabled}
            autoComplete="off"
          />
          {shape === 'basic' ? (
            <>
              <TextInput
                label={t('secrets.username')}
                value={username}
                onChange={setUsername}
                helpText={t('secrets.basicHint')}
                required
                disabled={disabled}
                autoComplete="off"
              />
              <TextInput
                label={t('secrets.password')}
                value={password}
                onChange={setPassword}
                type="password"
                required
                disabled={disabled}
                autoComplete="new-password"
              />
            </>
          ) : (
            <TextInput
              label={t('secrets.token')}
              value={token}
              onChange={setToken}
              type="password"
              required
              disabled={disabled}
              autoComplete="new-password"
            />
          )}
          <AllowedActionButton
            workflowId={undefined}
            scope="platform"
            action={ACTION.secretManage}
            onExecute={submit}
            loading={save.isPending}
            disabled={!canSave}
          >
            {t('secrets.save')}
          </AllowedActionButton>
        </div>
      ) : null}
    </div>
  );
}
