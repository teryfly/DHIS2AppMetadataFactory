/*
 * The shared form primitives (UI-APP-001). These tests exist because a select that throws on the
 * first click passed every screen test: nothing in the suite ever picked an option.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { CheckboxInput, SelectInput, TextInput } from './form';

describe('SelectInput (UI-APP-001)', () => {
  function Demo() {
    const [value, setValue] = useState('');
    return (
      <div>
        <SelectInput
          label="Classification"
          value={value}
          onChange={setValue}
          options={[
            { value: 'PUBLIC', label: 'Public' },
            { value: 'INTERNAL', label: 'Internal' },
          ]}
        />
        <span data-testid="picked">{value || 'none'}</span>
      </div>
    );
  }

  it('is reachable by its label and reports the picked option value', async () => {
    render(<Demo />);
    // The control is now a labelled <select>: the accessible name is how a screen reader (and
    // getByLabelText) reaches it, which the dhis2 widget it replaced could not do at all.
    const select = (await screen.findByLabelText('Classification')) as HTMLSelectElement;
    await userEvent.selectOptions(select, 'INTERNAL');
    expect(screen.getByTestId('picked')).toHaveTextContent('INTERNAL');
  });

  it('keeps an empty selection selectable so the caller can require a choice', async () => {
    render(<Demo />);
    const select = (await screen.findByLabelText('Classification')) as HTMLSelectElement;
    expect(select.value).toBe('');
  });
});

describe('TextInput and CheckboxInput', () => {
  it('reports every keystroke as a value, not an event', async () => {
    let latest = '';
    function Demo() {
      const [value, setValue] = useState('');
      latest = value;
      return <TextInput label="Name" value={value} onChange={setValue} />;
    }
    const { container } = render(<Demo />);
    // @dhis2/ui 10.19's InputField renders its label without a `for` and forwards no `id` to the
    // inner input, so the control cannot be reached by accessible name here. That gap is a
    // library-version limitation (label association arrives with a wired `id` in a later release),
    // not something this primitive can fix by passing props the component ignores.
    const input = container.querySelector('input') as HTMLInputElement;
    await userEvent.type(input, 'abc');
    expect(latest).toBe('abc');
  });

  it('gives the checkbox an accessible name', async () => {
    const seen: boolean[] = [];
    function Demo() {
      const [checked, setChecked] = useState(false);
      return (
        <CheckboxInput
          label="Confirm"
          checked={checked}
          onChange={(value) => {
            seen.push(value);
            setChecked(value);
          }}
        />
      );
    }
    render(<Demo />);
    // CheckboxField nests the input in its label, so this one *is* associated (WCAG 3.3.2).
    await userEvent.click(await screen.findByLabelText('Confirm'));
    expect(seen).toEqual([true]);
  });
});
