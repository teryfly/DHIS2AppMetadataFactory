/*
 * UI-REV-006 (总体 SRS NFR-CAP-001/002): a 5,000-object Registry and a 100-page API review are not
 * 5,000 DOM rows. The window that is scrolled renders only what fits plus a margin, and the two
 * spacer rows carry the height of what is above and below it, so the scrollbar and the keyboard
 * still describe the whole list.
 *
 * It stays a real `<table>` rather than a list of positioned divs: the row/column relationship is
 * the information (a UID beside a business key is only meaningful as a row), and re-creating that
 * with ARIA roles is how a big list quietly stops being navigable.
 */
import { useCallback, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';

export interface VirtualColumn<Row> {
  key: string;
  header: string;
  cell: (row: Row) => React.ReactNode;
  /** header cell is a row header, for the column that identifies the row */
  isRowHeader?: boolean;
  width?: number;
}

interface Props<Row> {
  rows: Row[];
  columns: VirtualColumn<Row>[];
  /** what the scroll region is, for a screen reader entering it without the caption */
  ariaLabel: string;
  rowHeight?: number;
  viewportHeight?: number;
  /** extra label after the caption, e.g. the version the rows belong to */
  captionNote?: string;
}

/** rows outside the window that stay mounted, so a fast wheel does not flash empty space */
const OVERSCAN = 6;

export function VirtualTable<Row>({
  rows,
  columns,
  ariaLabel,
  rowHeight = 40,
  viewportHeight = 440,
  captionNote,
}: Props<Row>) {
  const { t } = useTranslation();
  const [scrollTop, setScrollTop] = useState(0);
  const scroller = useRef<HTMLDivElement>(null);

  const onScroll = useCallback(() => {
    const node = scroller.current;
    if (node) setScrollTop(node.scrollTop);
  }, []);

  const first = Math.max(0, Math.floor(scrollTop / rowHeight) - OVERSCAN);
  const visibleCount = Math.ceil(viewportHeight / rowHeight) + OVERSCAN * 2;
  const last = Math.min(rows.length, first + visibleCount);
  const above = first * rowHeight;
  const below = Math.max(0, (rows.length - last) * rowHeight);
  const slice = rows.slice(first, last);

  return (
    <div
      className="virtual-scroll"
      ref={scroller}
      onScroll={onScroll}
      style={{ maxHeight: viewportHeight, overflow: 'auto' }}
      tabIndex={0}
      role="region"
      aria-label={ariaLabel}
    >
      <table className="virtual-table">
        <caption className="visually-hidden">
          {ariaLabel} — {t('common.rowCount', { count: rows.length })}
          {captionNote ? ` · ${captionNote}` : ''}
        </caption>
        <colgroup>
          {columns.map((column) => (
            <col key={column.key} style={column.width ? { width: column.width } : undefined} />
          ))}
        </colgroup>
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column.key} scope="col">
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {above > 0 ? (
            <tr aria-hidden="true" style={{ height: above }}>
              <td colSpan={columns.length} />
            </tr>
          ) : null}
          {slice.map((row, index) => (
            <tr key={`${first + index}`} className="virtual-row">
              {columns.map((column) =>
                column.isRowHeader ? (
                  <th key={column.key} scope="row">
                    {column.cell(row)}
                  </th>
                ) : (
                  <td key={column.key}>{column.cell(row)}</td>
                ),
              )}
            </tr>
          ))}
          {below > 0 ? (
            <tr aria-hidden="true" style={{ height: below }}>
              <td colSpan={columns.length} />
            </tr>
          ) : null}
        </tbody>
      </table>
    </div>
  );
}
