/*
 * S5IF-SEC01 / UI-ADM-002 as amended. What this component promises is narrow and testable: the
 * credential value is a pass-through. It is typed into a masked field, sent once, and then nothing
 * on the page or in the browser holds it — no read-back, no mask, no length, no localStorage.
 *
 * The failure mode these tests guard is the quiet one: a component that "works" while keeping the
 * token in a query cache, a form default, or a field that still shows the saved value after a
 * reload. Any of those would make the amendment to FR-SEC-007 false.
 */
import { useState } from 'react';
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { render, screen, waitFor, cleanup } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import '@/i18n';
import { CredentialField } from './CredentialField';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';

const SECRET = 'tok-SecRet-9f3a-值';

/**
 * The field is controlled — the reference lives in the *form*, not in this component — so the test
 * host keeps it in state the way `EnvironmentForm` does. Without that, a successful save would look
 * like nothing happened and the assertion below would be testing the wrong thing.
 */
function renderField(overrides: Partial<React.ComponentProps<typeof CredentialField>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const onChange = overrides.onChange ?? vi.fn();
  function Host() {
    const [value, setValue] = useState('');
    return (
      <CredentialField
        label="Secret reference"
        value={value}
        onChange={(next) => {
          setValue(next);
          onChange(next);
        }}
        suggestedName="dhis2-clinic-discovery"
        {...overrides}
      />
    );
  }
  render(
    <QueryClientProvider client={client}>
      <Host />
    </QueryClientProvider>,
  );
  return { onChange };
}

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

describe('CredentialField (S5IF-SEC01)', () => {
  it('masks the value while it is being typed, and never offers to remember it', async () => {
    renderField();
    await userEvent.click(screen.getByRole('button', { name: /Enter a new credential/i }));
    const token = await screen.findByLabelText(/Token or API key/i);
    expect(token).toHaveAttribute('type', 'password');
    expect(token).toHaveAttribute('autocomplete', 'new-password');
  });

  it('sends the value once and fills in the reference it got back', async () => {
    const captured: Record<string, unknown>[] = [];
    server.use(
      http.put('/api/v1/secrets/:name', async ({ request }) => {
        captured.push(await request.json() as Record<string, unknown>);
        return HttpResponse.json({
          name: 'dhis2-clinic-discovery',
          ref: 'db://dhis2-clinic-discovery',
          algo: 'aes-256-gcm+hkdf-sha256',
          createdAt: '2026-10-04T00:00:00Z',
          updatedAt: '2026-10-04T00:00:00Z',
          rotatedAt: null,
          createdBy: 'Ana',
        });
      }),
    );
    const { onChange } = renderField();
    await userEvent.click(screen.getByRole('button', { name: /Enter a new credential/i }));
    await userEvent.type(screen.getByLabelText(/Token or API key/i), SECRET);
    await userEvent.click(screen.getByRole('button', { name: /Encrypt and fill the reference/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0]).toEqual({ material: SECRET });
    expect(onChange).toHaveBeenCalledWith('db://dhis2-clinic-discovery');
    // and the field that holds it now holds the *reference*, not the value
    expect((screen.getByLabelText(/Secret reference/i) as HTMLInputElement).value).toBe(
      'db://dhis2-clinic-discovery',
    );
  });

  it('forgets the value after the request, whatever the answer was', async () => {
    server.use(
      http.put('/api/v1/secrets/:name', () =>
        HttpResponse.json(
          { errorCode: 'SECRET_UNAVAILABLE', message: 'no master key', details: { reason: 'storeDisabled' } },
          { status: 503 },
        ),
      ),
    );
    renderField();
    await userEvent.click(screen.getByRole('button', { name: /Enter a new credential/i }));
    const token = screen.getByLabelText(/Token or API key/i) as HTMLInputElement;
    await userEvent.type(token, SECRET);
    expect(token.value).toBe(SECRET);

    await userEvent.click(screen.getByRole('button', { name: /Encrypt and fill the reference/i }));
    await waitFor(() => expect(token.value).toBe(''));
    // the refusal is explained without repeating what was refused
    expect(screen.getByText(/credential store is off/i)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain(SECRET);
  });

  it('composes user:password for a Basic environment, because that is the shape S4 needs', async () => {
    const captured: Record<string, unknown>[] = [];
    server.use(
      http.put('/api/v1/secrets/:name', async ({ request }) => {
        captured.push(await request.json() as Record<string, unknown>);
        return HttpResponse.json({ name: 'x', ref: 'db://x', algo: 'a', createdAt: null, updatedAt: null, rotatedAt: null, createdBy: 'Ana' });
      }),
    );
    renderField({ shape: 'basic', suggestedName: 'dhis2-clinic-import' });
    await userEvent.click(screen.getByRole('button', { name: /Enter a new credential/i }));
    await userEvent.type(await screen.findByLabelText(/DHIS2 username/i), 'admin');
    const password = screen.getByLabelText(/Password/i) as HTMLInputElement;
    expect(password).toHaveAttribute('type', 'password');
    await userEvent.type(password, 'p4ss');
    await userEvent.click(screen.getByRole('button', { name: /Encrypt and fill the reference/i }));

    await waitFor(() => expect(captured).toHaveLength(1));
    expect(captured[0]).toEqual({ material: 'admin:p4ss' });
  });

  it('keeps nothing in browser storage', async () => {
    const before = { ...localStorage };
    renderField();
    await userEvent.click(screen.getByRole('button', { name: /Enter a new credential/i }));
    await userEvent.type(screen.getByLabelText(/Token or API key/i), SECRET);
    expect(Object.keys({ ...localStorage })).toEqual(Object.keys(before));
    expect(JSON.stringify(localStorage)).not.toContain(SECRET);
  });

  it('offers an already-stored reference instead of asking for the value again', async () => {
    // one credential already in the store: the point of the picker is that a second environment
    // reuses a token rather than making the operator type it out again
    server.use(
      http.get('/api/v1/secrets', () =>
        HttpResponse.json({
          items: [{ name: 'shared', ref: 'db://shared', algo: 'a', createdAt: null, updatedAt: null, rotatedAt: null, createdBy: 'Ana' }],
          total: 1,
          generatedAt: null,
        }),
      ),
    );
    const { onChange } = renderField();
    const picker = await screen.findByLabelText(/Use a stored credential/i);
    await userEvent.selectOptions(picker, 'db://shared');
    expect(onChange).toHaveBeenCalledWith('db://shared');
  });
});
