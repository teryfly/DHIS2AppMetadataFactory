/*
 * UI-OP-001: first use requires an operator name (stored locally, shown in the top bar, editable).
 * Until it is set, the app is blocked and every state-changing control is disabled (UI-OP-002).
 */
import { useState } from 'react';
import { useTranslation } from 'react-i18next';
import { useOperatorStore } from '@/stores/operator';
import { useUiStore } from '@/stores/ui';
import { Dialog } from './Dialog';

export function OperatorDialog() {
  const { t } = useTranslation();
  const open = useUiStore((s) => s.operatorDialogOpen);
  const close = useUiStore((s) => s.closeOperatorDialog);
  const current = useOperatorStore((s) => s.name);
  const setName = useOperatorStore((s) => s.setName);
  const [value, setValue] = useState(current ?? '');

  if (!open) return null;

  const save = () => {
    const trimmed = value.trim();
    if (!trimmed) return;
    setName(trimmed);
    close();
  };

  return (
    <Dialog
      title={t('operator.setPromptTitle')}
      closeLabel={t('common.close')}
      onClose={current ? close : () => undefined}
      footer={
        <>
          <button type="button" className="primary-link" disabled={!value.trim()} onClick={save}>
            {current ? t('common.save') : t('operator.save')}
          </button>
          {current ? (
            <button type="button" className="quiet-button" onClick={close}>
              {t('common.cancel')}
            </button>
          ) : null}
        </>
      }
    >
      <div className="stack">
        <p className="muted">{t('operator.setPromptBody')}</p>
        <div className="field">
          <label className="field__label" htmlFor="operator-name">
            {t('operator.label')} <span className="field__required">*</span>
          </label>
          <input
            id="operator-name"
            className="field__input"
            value={value}
            required
            placeholder={t('operator.placeholder')}
            onChange={(event) => setValue(event.target.value)}
          />
        </div>
      </div>
    </Dialog>
  );
}
