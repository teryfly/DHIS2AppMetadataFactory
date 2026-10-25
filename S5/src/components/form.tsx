/*
 * Form primitives over @dhis2/ui (UI-APP-001), normalised to React Hook Form's value-based
 * `onChange` so RHF + Zod validation reads cleanly. Only UI state flows through these; the
 * submitted body is validated by a Zod model generated from the OpenAPI (UI-MAN-002, UI-APP-007).
 */
import { useId, useState } from 'react';
import type { ReactNode } from 'react';
import { CheckboxField } from '@dhis2/ui';

type InputType = 'text' | 'number' | 'email' | 'password' | 'tel' | 'url' | 'search' | 'date' | 'datetime-local';

interface BaseProps {
  label: string;
  helpText?: string;
  error?: string;
  required?: boolean;
  disabled?: boolean;
}

/**
 * A text field with a real label association.
 *
 * @dhis2/ui 10.19 renders InputField's label with no `for` and forwards no `id` to the inner
 * `<input>` — `htmlFor` does not appear anywhere in the built bundle — so the control it produces
 * has no accessible name. That is not cosmetic: axe's `label` rule and `getByLabel` in the e2e
 * suite both fail on it, and a screen reader announces an empty box. The markup is therefore ours,
 * with dhis2's own layout classes kept where they exist.
 */
export function TextInput({
  label,
  value,
  onChange,
  helpText,
  error,
  required,
  disabled,
  type = 'text',
  placeholder,
  inputWidth,
  autoComplete,
}: BaseProps & {
  value: string;
  onChange: (v: string) => void;
  type?: InputType;
  placeholder?: string;
  inputWidth?: string;
  /** `new-password` on a credential field: a browser that offers to remember one has just been told the value is worth keeping. */
  autoComplete?: string;
}) {
  const id = useId();
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
        {required ? <span className="field__required"> *</span> : null}
      </label>
      <input
        id={id}
        className={`field__input${error ? ' field__input--error' : ''}`}
        type={type}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        required={required}
        autoComplete={autoComplete}
        style={inputWidth ? { width: `${Number(inputWidth) || 0}px` } : undefined}
        onChange={(event) => onChange(event.target.value)}
      />
      {helpText ? <p className="field__help">{helpText}</p> : null}
      {error ? <p className="field__error" role="alert">{error}</p> : null}
    </div>
  );
}

/** The same label-association rule as TextInput (UI-NFR-03). */
export function TextAreaInput({
  label,
  value,
  onChange,
  helpText,
  error,
  required,
  rows = 4,
}: BaseProps & { value: string; onChange: (v: string) => void; rows?: number }) {
  const id = useId();
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
        {required ? <span className="field__required"> *</span> : null}
      </label>
      <textarea
        id={id}
        className={`field__input field__textarea${error ? ' field__input--error' : ''}`}
        rows={rows}
        value={value}
        required={required}
        onChange={(event) => onChange(event.target.value)}
      />
      {helpText ? <p className="field__help">{helpText}</p> : null}
      {error ? <p className="field__error" role="alert">{error}</p> : null}
    </div>
  );
}

export interface SelectOption {
  value: string;
  label: string;
}

/**
 * A select with a real label association.
 *
 * @dhis2/ui 10.19's SingleSelect is a `div[tabindex]` that forwards neither `id`, `role` nor
 * `aria-label`, so the value it holds has no accessible name at all — axe fails it on
 * `aria-labelledby`/`aria-label` and a screen reader announces nothing. A native select is the
 * smallest correct control: it is labelled, keyboard-operable and read out by every platform.
 */
export function SelectInput({
  label,
  value,
  onChange,
  options,
  helpText,
  error,
  required,
  disabled,
}: BaseProps & { value: string; onChange: (v: string) => void; options: SelectOption[] }) {
  const id = useId();
  return (
    <div className="field">
      <label className="field__label" htmlFor={id}>
        {label}
        {required ? <span className="field__required"> *</span> : null}
      </label>
      <select
        id={id}
        className={`field__input${error ? ' field__input--error' : ''}`}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
      >
        {value === '' ? <option value=""> </option> : null}
        {options.map((option) => (
          <option key={option.value || 'none'} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
      {helpText ? <p className="field__help">{helpText}</p> : null}
      {error ? <p className="field__error" role="alert">{error}</p> : null}
    </div>
  );
}

export function CheckboxInput({
  label,
  checked,
  onChange,
  helpText,
  disabled,
}: {
  label: ReactNode;
  checked: boolean;
  onChange: (v: boolean) => void;
  helpText?: string;
  disabled?: boolean;
}) {
  return (
    <CheckboxField
      label={label}
      checked={checked}
      disabled={disabled}
      helpText={helpText}
      onChange={(payload) => onChange(payload.checked)}
    />
  );
}

/**
 * One field's consequence, behind a question mark.
 *
 * `helpText` says what a box is; this says what the number in it does to a run. A form can state the
 * first and still leave the operator guessing at the second, which is how a model registration with
 * an empty context window ends up refused by every stage.
 *
 * It reveals on a click rather than on hover because a tooltip has no finger equivalent, and the same
 * control should read the same way as the one on the phone. `aria-controls` is given only while the
 * text exists, so a closed toggle never points at nothing.
 */
export function HelpTip({ label, text }: { label: string; text: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="field__tip">
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((current) => !current)}
      >
        <span aria-hidden="true">?</span>
        {label}
      </button>
      {open ? <p id={id} className="field__help">{text}</p> : null}
    </div>
  );
}
