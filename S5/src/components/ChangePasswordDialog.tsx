/*
 * UI-AUTH-001 / FR-SEC-001: change your own password.
 *
 * The value goes out once and stays nowhere: no store, no cache, no toast, no draft kept for the
 * next attempt. A successful change ends this session because S1 revokes every live session when
 * the digest changes, so the dialog clears the local session and marks the exit as a lapse.
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { sessionOptions } from '@/api/queries';
import { useSetUserPassword, dropSession } from '@/api/mutations';
import { isApiError } from '@/api/errors';
import { useCsrfStore } from '@/stores/csrf';
import { useUiStore } from '@/stores/ui';
import { Dialog } from './Dialog';

/** S1 enforces this floor; stating it here means a refusal is not the first you hear of it. */
const MIN_PASSWORD_CHARS = 8;

export function ChangePasswordDialog() {
  const { t } = useTranslation();
  const open = useUiStore((s) => s.passwordDialogOpen);
  const close = useUiStore((s) => s.closePasswordDialog);
  const qc = useQueryClient();
  const { data: who } = useQuery(sessionOptions());
  const userId = who?.userId ?? '';
  const change = useSetUserPassword(userId);
  const [value, setValue] = useState('');
  const [again, setAgain] = useState('');
  const [failure, setFailure] = useState('');

  if (!open || !userId) return null;

  const tooShort = value.trim().length < MIN_PASSWORD_CHARS;
  const mismatch = again.trim() !== value.trim();
  const canSubmit = value.trim() !== '' && !tooShort && !mismatch && !change.isPending;

  const forget = () => {
    setValue('');
    setAgain('');
  };

  const submit = async () => {
    setFailure('');
    try {
      await change.mutateAsync({ password: value, mustChangePassword: false });
      forget();
      close();
      dropSession(qc);
      useCsrfStore.getState().markLapsed();
    } catch (cause) {
      forget();
      setFailure(isApiError(cause) ? cause.message : t('auth.changeFailed'));
    }
  };

  return (
    <Dialog
      title={t('auth.changeTitle')}
      closeLabel={t('common.close')}
      onClose={close}
      footer={
        <>
          <button type="button" className="primary-link" disabled={!canSubmit} onClick={submit}>
            {t('auth.changeSubmit')}
          </button>
          <button type="button" className="quiet-button" onClick={close}>
            {t('common.cancel')}
          </button>
        </>
      }
    >
      <div className="stack">
        {who?.mustChangePassword ? <p className="field__error">{t('auth.changeRequired')}</p> : null}
        <p className="muted">{t('auth.changeBody')}</p>
        <PasswordField
          id="change-password-new"
          label={t('auth.newPassword')}
          value={value}
          onChange={setValue}
          error={value !== '' && tooShort ? t('auth.passwordMin', { min: MIN_PASSWORD_CHARS }) : undefined}
          help={t('auth.passwordMin', { min: MIN_PASSWORD_CHARS })}
        />
        <PasswordField
          id="change-password-again"
          label={t('auth.confirmPassword')}
          value={again}
          onChange={setAgain}
          error={again !== '' && mismatch ? t('auth.passwordMismatch') : undefined}
        />
        {failure ? (
          <p className="field__error" role="alert">
            {failure}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}

function PasswordField({
  id,
  label,
  value,
  onChange,
  error,
  help,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  help?: string;
}) {
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label} <span className="field__required">*</span>
      </label>
      <input
        id={id}
        className={`field__input${error ? ' field__input--error' : ''}`}
        type="password"
        autoComplete="new-password"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      />
      {help ? <p className="field__help">{help}</p> : null}
      {error ? <p className="field__error">{error}</p> : null}
    </div>
  );
}
