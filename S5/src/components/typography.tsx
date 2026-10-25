import type { ReactNode } from 'react';

/*
 * @dhis2/ui v10 ships no Heading/Text components (typography is plain markup + CSS). These thin,
 * semantic wrappers keep call sites readable while preserving WCAG heading order (UI-APP-003).
 */
type Level = 1 | 2 | 3 | 4 | 5 | 6;

export function Heading({ level = 2, size, children, className }: { level?: Level | string; size?: 'small' | 'medium' | 'large'; children: ReactNode; className?: string }) {
  const n = Math.min(6, Math.max(1, Number(level) || 2)) as Level;
  const Tag = `h${n}` as const;
  const fontSize = size === 'large' ? 24 : size === 'medium' ? 18 : 15;
  return (
    <Tag className={className} style={{ fontSize, margin: 0, fontWeight: 700 }}>
      {children}
    </Tag>
  );
}
