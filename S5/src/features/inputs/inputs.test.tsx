/*
 * UI-REQS-001 / 002 / 003 / 004: the input chain in the browser — declare a classification and
 * upload, see how far parsing got, review the proposed column mapping, and read what S3
 * understood. The mock handlers mirror S1's rules, so a test that passes here would also pass
 * against S1 for the same reason.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { OperatorGate } from '@/components/OperatorGate';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { useCsrfStore } from '@/stores/csrf';
import { server } from '@/mocks/server';
import { DESIGN_PROJECT_ID, inputs as mockInputs, nrr } from '@/mocks/db';
import { buildUploadForm } from '@/api/upload';

function renderAt(path: string) {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[path]}>
        <OperatorGate>
          <AppRoutes />
        </OperatorGate>
      </MemoryRouter>
    </AppProviders>,
  );
}

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

/** The classification picker is a labelled native select (see components/form.tsx). */
async function openClassificationSelect() {
  await userEvent.selectOptions(screen.getByLabelText(/Data classification/i) as HTMLSelectElement, 'PHI_PII');
}

const uploadButton = () => screen.getByRole('button', { name: /^Upload$/ }) as HTMLButtonElement;

describe('The upload body (FR-REQ-001)', () => {
  it('carries the declared classification and the PHI confirmation as form fields', () => {
    const form = buildUploadForm({
      file: new File(['a,b\n1,2\n'], 'clients.csv', { type: 'text/csv' }),
      dataClassification: 'PHI_PII',
      phiDeclared: true,
    });
    // S1 reads these as multipart fields, not headers: an undeclared upload is a 422 there, and
    // PHI without the confirmation is a 422 — this is the only place the UI says it.
    expect(form.get('dataClassification')).toBe('PHI_PII');
    expect(form.get('phiDeclared')).toBe('true');
    expect(form.get('mediaType')).toBe('text/csv');
    expect((form.get('file') as File).name).toBe('clients.csv');
  });
});

describe('Input list and parse status (UI-REQS-002)', () => {
  it('lists each input with its declared classification and parse status', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs`);
    const row = await screen.findByText('spec.xlsx');
    expect(within(row.closest('tr') as HTMLElement).getByText('Internal')).toBeInTheDocument();
    expect(await screen.findByText('indicators.csv')).toBeInTheDocument();
  });

  it('opens the uploaded bytes from the name the operator recognises', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs`);
    const name = await screen.findByText('spec.xlsx');
    // The artifact record has no filename, so the review page is addressed by id and carried the
    // name as a query — but the row the operator knows is the one they uploaded, and clicking it has
    // to read the bytes back under that name.
    expect(name.closest('a')).toHaveAttribute(
      'href',
      `/projects/${DESIGN_PROJECT_ID}/artifacts/INP-1?name=${encodeURIComponent('spec.xlsx')}`,
    );
  });

  it('offers the column-mapping review only for the input that needs it', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs`);
    await screen.findByText('indicators.csv');
    const review = screen.getAllByRole('link', { name: /Review column mapping/i });
    expect(review).toHaveLength(1);
    expect(review[0]).toHaveAttribute('href', `/projects/${DESIGN_PROJECT_ID}/inputs/INP-2/column-mapping`);
  });

  it('offers the parsed-result review instead of column mapping for a text input', async () => {
    server.use(
      http.get('/api/v1/projects/:id/inputs', () =>
        HttpResponse.json({
          total: 1,
          items: [
            {
              ...mockInputs.items![1],
              artifactId: 'INP-3',
              filename: '出院随访.txt',
              mediaType: 'text/plain',
              parserType: 'MARKDOWN',
              usedModel: true,
              parseStatus: 'NEEDS_REVIEW',
              parseRunId: 'ipr-3',
            },
          ],
        }),
      ),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs`);
    await screen.findByText('出院随访.txt');
    // A text document has no columns; the mapping page would be empty (AI-INP-006).
    expect(screen.queryByRole('link', { name: /Review column mapping/i })).not.toBeInTheDocument();
    const review = screen.getByRole('link', { name: /Review parsed result/i });
    expect(review).toHaveAttribute('href', `/projects/${DESIGN_PROJECT_ID}/stages/ST-req`);
  });

  it('shows the parsed requirements on the inputs page, with the source artifact', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs`);
    expect(await screen.findByText(/Understood requirements/i)).toBeInTheDocument();
    expect(await screen.findByText('A monthly summary per facility is required.')).toBeInTheDocument();
    const sources = screen.getAllByRole('link', { name: 'INP-1' });
    expect(sources[0]).toHaveAttribute('href', `/projects/${DESIGN_PROJECT_ID}/artifacts/INP-1`);
  });

  it('links a parsing input to its LLM conversation log', async () => {
    server.use(
      http.get('/api/v1/projects/:id/inputs', () =>
        HttpResponse.json({
          total: 1,
          items: [{ ...mockInputs.items![0], parseStatus: 'PARSING', parseRunId: 'ipr-1' }],
        }),
      ),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs`);
    await screen.findByText('spec.xlsx');
    const status = await screen.findByText('Parsing…');
    expect(status.closest('a')).toHaveAttribute(
      'href',
      `/projects/${DESIGN_PROJECT_ID}/inputs/INP-1/llm-log`,
    );
  });
});

describe('Upload with a declared classification (UI-REQS-001)', () => {
  it('keeps upload closed until the classification is declared, then sends it', async () => {
    let sent = 0;
    server.use(
      http.post(`/api/v1/projects/:id/inputs`, () => {
        sent += 1;
        return HttpResponse.json({ artifactId: 'INP-9', filename: 'clients.csv', dataClassification: 'PHI_PII', sizeBytes: 12 }, { status: 201 });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs`);
    const file = await screen.findByLabelText(/File/);
    // No classification chosen yet: S1 would refuse this (FR-REQ-001), so the button stays closed
    // rather than sending a declaration the operator never made.
    await waitFor(() => expect(uploadButton()).toBeDisabled());
    await userEvent.upload(file, new File(['a,b\n1,2\n'], 'clients.csv', { type: 'text/csv' }));
    await openClassificationSelect();
    // PHI is refused until it is confirmed by name: it is the one classification that must never
    // reach a model, and the checkbox is where the uploader takes responsibility for that.
    expect(uploadButton()).toBeDisabled();
    await userEvent.click(await screen.findByRole('checkbox'));
    // re-queried every time: React replaces the button node as the form state changes, so a
    // reference held from earlier points at a detached element that stays disabled forever
    await waitFor(() => expect(uploadButton()).toBeEnabled());
    await userEvent.click(uploadButton());
    await waitFor(() => expect(sent).toBe(1));
  });

  it('sends the headers a cookie-authenticated change is checked for', async () => {
    // The upload leaves the typed client for XMLHttpRequest, so the CSRF echo and the encoded
    // operator name are re-attached by hand here. Without the token S1 answers 403 CSRF_FAILED —
    // and the progress bar still runs to 100%, because it reports the body, not the answer.
    let csrf: string | null = null;
    let operator: string | null = null;
    useCsrfStore.getState().setToken('tok-9');
    useOperatorStore.getState().setName('测试员');
    server.use(
      http.post(`/api/v1/projects/:id/inputs`, ({ request }) => {
        csrf = request.headers.get('x-csrf-token');
        operator = request.headers.get('x-operator');
        return HttpResponse.json({ artifactId: 'INP-9', filename: 'spec.csv', dataClassification: 'INTERNAL', sizeBytes: 4 }, { status: 201 });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs`);
    await userEvent.upload(await screen.findByLabelText(/File/), new File(['a,b\n'], 'spec.csv', { type: 'text/csv' }));
    await userEvent.selectOptions(screen.getByLabelText(/Data classification/i) as HTMLSelectElement, 'INTERNAL');
    await waitFor(() => expect(uploadButton()).toBeEnabled());
    await userEvent.click(uploadButton());
    await waitFor(() => expect(csrf).toBe('tok-9'));
    // A header value must stay within ISO-8859-1; Chromium rejects the request outright otherwise.
    expect(operator).toBe(encodeURIComponent('测试员'));
    expect(await screen.findByRole('status')).toHaveTextContent(/Uploaded spec\.csv/i);
  }, 15000);

  it('refuses a file larger than the limit S1 published, before sending it', async () => {
    let sent = false;
    server.use(
      http.get(`/api/v1/upload-limits`, () => HttpResponse.json({ maxUploadBytes: 8, allowedUploadTypes: ['text/csv'] })),
      http.post(`/api/v1/projects/:id/inputs`, () => {
        sent = true;
        return HttpResponse.json({}, { status: 201 });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs`);
    const file = await screen.findByLabelText(/File/);
    await userEvent.upload(file, new File(['0123456789'], 'big.csv', { type: 'text/csv' }));
    expect(await screen.findByText(/larger than the 8 B limit/i)).toBeInTheDocument();
    await userEvent.selectOptions(screen.getByLabelText(/Data classification/i) as HTMLSelectElement, 'INTERNAL');
    expect(await screen.findByRole('button', { name: /^Upload$/ })).toBeDisabled();
    expect(sent).toBe(false);
  });
});

describe('Column mapping review (UI-REQS-003)', () => {
  it('edits a target and re-parses with the confirmed mapping under a fresh key', async () => {
    let body: Record<string, unknown> = {};
    let key = '';
    server.use(
      http.post(`/api/v1/projects/:id/inputs/:artifactId/parse`, async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>;
        key = request.headers.get('Idempotency-Key') ?? '';
        return HttpResponse.json({ stageId: 'ST-req', stepRunId: 'STEP-2', accepted: true }, { status: 202 });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/inputs/INP-2/column-mapping`);
    await screen.findByText('col1');
    const targets = screen.getAllByRole('textbox');
    const unsolved = targets[targets.length - 1];
    if (!unsolved) throw new Error('the third source column has no editable target');
    await userEvent.clear(unsolved);
    await userEvent.type(unsolved, 'code');
    await userEvent.click(await screen.findByRole('button', { name: /Confirm and re-parse/i }));
    await waitFor(() => expect(key).not.toBe(''));
    expect((body.options as { columnMapping: Record<string, string> }).columnMapping.col3).toBe('code');
  });
});

describe('NRR grouped view (UI-REQS-004)', () => {
  it('groups the parsed requirements by category and keeps a low-confidence item visible', async () => {
    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-req`);
    const title = await screen.findByText(/Understood requirements/i);
    const card = title.closest('div[class="stack"]') as HTMLElement;
    expect(within(card).getByText('A monthly summary per facility is required.')).toBeInTheDocument();
    // A doubtful item is not hidden behind its score: the question it raised is shown instead.
    expect(within(card).getByText(/Is the SMS export weekly or monthly\?/)).toBeInTheDocument();
  });

  it('narrows the list through the server-side category filter', async () => {
    const queried: (string | null)[] = [];
    server.use(
      http.get(`/api/v1/workflows/:id/nrr`, ({ request }) => {
        const category = new URL(request.url).searchParams.get('category');
        queried.push(category);
        const items = category ? nrr.items.filter((item) => item.category === category) : nrr.items;
        return HttpResponse.json({ total: items.length, items });
      }),
    );
    renderAt(`/projects/${DESIGN_PROJECT_ID}/stages/ST-req`);
    await screen.findByText(/Understood requirements/i);
    const segment = await screen.findByRole('button', { name: 'validation' });
    await userEvent.click(segment);
    await waitFor(() => expect(queried).toContain('validation'));
    expect(await screen.findByText('Age must be between 0 and 120.')).toBeInTheDocument();
    expect(screen.queryByText('A monthly summary per facility is required.')).not.toBeInTheDocument();
  });
});
