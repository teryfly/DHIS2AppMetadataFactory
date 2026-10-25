import type { ReactNode } from 'react';

/** The icons the application shell needs; keeping them separate keeps the page icon set lazy. */
export type ShellIconName =
  | 'workflow'
  | 'help'
  | 'bell'
  | 'users'
  | 'lock'
  | 'home'
  | 'inbox'
  | 'rocket'
  | 'folder'
  | 'server'
  | 'shield'
  | 'settings'
  | 'close';

const PATHS: Record<ShellIconName, ReactNode> = {
  workflow: (
    <>
      <rect x="3" y="4" width="6" height="4" rx="1" />
      <rect x="15" y="16" width="6" height="4" rx="1" />
      <path d="M9 6h4a2 2 0 0 1 2 2v8h-4" />
    </>
  ),
  help: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9a2.7 2.7 0 0 1 5.3.8c0 2-2.8 2.2-2.8 4.2" />
      <path d="M12 17.5h.01" />
    </>
  ),
  bell: (
    <>
      <path d="M6 9a6 6 0 0 1 12 0c0 7 3 7 3 9H3c0-2 3-2 3-9Z" />
      <path d="M10 21h4" />
    </>
  ),
  users: (
    <>
      <circle cx="9" cy="8" r="3" />
      <path d="M3 20c0-3 2.5-5 6-5s6 2 6 5" />
      <path d="M16 5a3 3 0 0 1 0 6M17 15c2.5.5 4 2.3 4 5" />
    </>
  ),
  lock: (
    <>
      <rect x="4" y="10" width="16" height="11" rx="2" />
      <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    </>
  ),
  home: <path d="M3 10.5 12 3l9 7.5V21h-6v-6H9v6H3V10.5Z" />,
  inbox: (
    <>
      <path d="M4 5h16l2 10v4H2v-4L4 5Z" />
      <path d="M2 15h6l1.5 2h5L16 15h6" />
    </>
  ),
  rocket: (
    <>
      <path d="M14 4c3 0 6 3 6 6-2 5-6 8-9 9l-2-2c1-3 4-7 9-9" />
      <path d="M9 17l-2 2M7 15l-2-2" />
      <circle cx="15" cy="9" r="1.5" />
    </>
  ),
  folder: <path d="M3 6h7l2 2h9v11H3V6Z" />,
  server: (
    <>
      <rect x="3" y="4" width="18" height="6" rx="1.5" />
      <rect x="3" y="14" width="18" height="6" rx="1.5" />
      <path d="M7 7h.01M7 17h.01" />
    </>
  ),
  shield: <path d="M12 3 5 6v5c0 5 3 8 7 10 4-2 7-5 7-10V6l-7-3Z" />,
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2" />
    </>
  ),
  close: <path d="m6 6 12 12M18 6 6 18" />,
};

export function ShellIcon({ name, size = 18 }: { name: ShellIconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name]}
    </svg>
  );
}
