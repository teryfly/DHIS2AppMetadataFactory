import type { ReactNode } from 'react';
import { NoticeBox } from '@dhis2/ui';

type Tone = 'info' | 'warning' | 'error' | 'valid';

/** Thin, always-inline status box on top of @dhis2 NoticeBox (UI-APP-001). */
export function NoticeBar({ tone = 'info', title, children }: { tone?: Tone; title?: string; children?: ReactNode }) {
  return (
    <div style={{ margin: '8px 0' }}>
      <NoticeBox
        error={tone === 'error'}
        warning={tone === 'warning'}
        valid={tone === 'valid'}
        title={title}
      >
        {children}
      </NoticeBox>
    </div>
  );
}
