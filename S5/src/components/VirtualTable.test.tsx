/*
 * UI-REV-006 (NFR-CAP-001/002): a 5,000-object Registry must not put 5,000 rows in the document.
 * What is asserted is the promise the requirement makes: the DOM holds a window, the scrollbar and
 * the caption still describe the whole list, and the last row is reachable.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup, fireEvent } from '@testing-library/react';
import '@/i18n';
import { AppProviders } from '@/components/AppProviders';
import { VirtualTable } from './VirtualTable';

const ROWS = Array.from({ length: 5000 }, (_, i) => ({ key: `object:${i}`, type: i % 2 === 0 ? 'dataElement' : 'indicator' }));

function renderTable() {
  return render(
    <AppProviders>
      <VirtualTable
        rows={ROWS}
        ariaLabel="Registry objects"
        rowHeight={40}
        viewportHeight={400}
        columns={[
          { key: 'key', header: 'Business key', isRowHeader: true, cell: (row) => row.key },
          { key: 'type', header: 'Type', cell: (row) => row.type },
        ]}
      />
    </AppProviders>,
  );
}

beforeEach(() => cleanup());
afterEach(() => cleanup());

describe('VirtualTable (UI-REV-006)', () => {
  it('renders a window of the list, not the list', () => {
    renderTable();
    const rows = screen.getAllByRole('row');
    // header + spacer + a window: far short of 5,000, which is the whole point
    expect(rows.length).toBeLessThan(60);
    expect(screen.getByRole('rowheader', { name: 'object:0' })).toBeInTheDocument();
  });

  it('says how many items there are even though few are rendered', () => {
    renderTable();
    expect(screen.getByText(/5,000 items|5000 items/)).toBeInTheDocument();
  });

  it('keeps the whole height so the scrollbar still describes the list', () => {
    const { container } = renderTable();
    const spacers = container.querySelectorAll('tr[aria-hidden="true"]');
    const below = spacers[spacers.length - 1];
    expect(below).toBeInTheDocument();
    // the rows that are not rendered are still accounted for in pixels
    expect(Number((below as HTMLElement).style.height.replace('px', ''))).toBeGreaterThan(190000);
  });

  it('reaches the last object by scrolling', () => {
    renderTable();
    const scroller = screen.getByRole('region', { name: 'Registry objects' });
    fireEvent.scroll(Object.assign(scroller, { scrollTop: 5000 * 40 - 400 }));

    expect(screen.getByRole('rowheader', { name: 'object:4999' })).toBeInTheDocument();
  });
});
