/*
 * S5IF-BP13 on the screen that had been linking at it. The Registry row names one decision; until
 * this page existed that link landed on the router's catch-all, which is a very expensive way of
 * telling an operator "the reason this object is shaped like this is not available to you".
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { http, HttpResponse } from 'msw';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { AppRoutes } from '@/routes';
import { useOperatorStore } from '@/stores/operator';
import { server } from '@/mocks/server';
import * as db from '@/mocks/db';

const PATH = `/projects/${db.DESIGN_PROJECT_ID}/decisions`;

function renderAt(path: string) {
  return render(
    <AppProviders>
      <MemoryRouter initialEntries={[path]}>
        <AppRoutes />
      </MemoryRouter>
    </AppProviders>,
  );
}

beforeEach(() => {
  cleanup();
  useOperatorStore.getState().setName('Ana');
});
afterEach(() => server.resetHandlers());

describe('the design decisions of one blueprint version (S5IF-BP13)', () => {
  it('reaches the page from the Registry row that names the decision', async () => {
    renderAt(`/projects/${db.DESIGN_PROJECT_ID}/metadata`);
    const link = await screen.findByRole('link', { name: 'MAP-017' });
    expect(link).toHaveAttribute('href', `${PATH}?ids=MAP-017`);
  });

  it('shows the reason, the chosen option and what was left on the table', async () => {
    renderAt(PATH);
    expect(await screen.findByText(/maps to the org-unit tree/)).toBeInTheDocument();
    expect(screen.getByText('MAP-017')).toBeInTheDocument();
    expect(screen.getByText(/a flat list loses the hierarchy/)).toBeInTheDocument();
    expect(screen.getByText(/renamed units break the parent lookup/)).toBeInTheDocument();
    // the ratio arrives as 0–1 and reads as a percentage, the way every other confidence does
    expect(screen.getByText(/78%/)).toBeInTheDocument();
    expect(screen.queryByText(/MAP-017\.|0\.78/)).not.toBeInTheDocument();
  });

  it('asks the server for the one decision the link named, and offers the way back to all', async () => {
    // No handler override here on purpose: the fixture's own endpoint honours `decisionIds`, so the
    // row that did not come back is proof the page asked for one instead of reading the whole list
    // and slicing it in the browser.
    renderAt(`${PATH}?ids=ARC-002`);
    expect(await screen.findByText('ARC-002')).toBeInTheDocument();
    expect(screen.queryByText('MAP-017')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: /show every decision/i })).toHaveAttribute('href', PATH);
  });

  it('links the decision back to the object and the artifacts it reaches', async () => {
    renderAt(PATH);
    const subject = await screen.findByRole('link', { name: /entity:facility/ });
    expect(subject).toHaveAttribute('href', `/projects/${db.DESIGN_PROJECT_ID}/graph?businessKey=entity%3Afacility`);
    const artifact = screen.getByRole('link', { name: 'ART-PKG-1' });
    expect(artifact).toHaveAttribute('href', `/projects/${db.DESIGN_PROJECT_ID}/artifacts/ART-PKG-1`);
  });

  it('says the version recorded nothing, rather than showing an empty frame', async () => {
    server.use(http.get('/api/v1/projects/:id/decisions', () => HttpResponse.json({ items: [], total: 0 })));
    renderAt(PATH);
    expect(await screen.findByText(/recorded no decisions/i)).toBeInTheDocument();
  });

  it('shows a refused read as a refusal', async () => {
    server.use(http.get('/api/v1/projects/:id/decisions', () => HttpResponse.json({ errorCode: 'FORBIDDEN', message: 'no' }, { status: 403 })));
    renderAt(PATH);
    expect(await screen.findByText(/no$|forbidden|无权/i)).toBeInTheDocument();
    expect(screen.queryByText(/recorded no decisions/i)).not.toBeInTheDocument();
  });

  it('answers the approval page’s third question with the same card', async () => {
    renderAt(`/projects/${db.DESIGN_PROJECT_ID}/gate/GT-1`);
    expect(await screen.findByRole('heading', { name: /Why \(decisions & rationale\)/i })).toBeInTheDocument();
    expect(screen.getByText(/maps to the org-unit tree/)).toBeInTheDocument();
  });
});
