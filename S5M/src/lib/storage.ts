// localStorage keys shared by the Pinia stores and the network layer. Kept in one place so the
// client (which must not import stores, to avoid cycles) and the stores agree. All values are UI
// facts only — never business state (UM-P-01).
export const LS = {
  operator: 's5m:operator',
  language: 's5m:language',
  font: 's5m:font',
  theme: 's5m:theme',
  ui: 's5m:ui',
  dataClassByProject: 's5m:dataclass',
  draftPrefix: 's5m:draft:',
  idemPrefix: 's5m:idem:',
} as const;

/**
 * Session-scoped keys: dropped when the tab closes, which is what a value carried across a
 * redirect to the organisation and back has to be. Only ever a path inside this app.
 */
export const SS = {
  oidcReturn: 's5m:oidc-return',
} as const;

export type Lang = 'zh-CN' | 'en';

export function readJSON<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

export function writeJSON(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* quota or unavailable — UI state only, safe to drop */
  }
}

export function removeKey(key: string): void {
  try {
    localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

/** Session-scoped plain text. `''` when unavailable, which is the same as "nothing stored". */
export function readSessionText(key: string): string {
  try {
    return sessionStorage.getItem(key) ?? '';
  } catch {
    return '';
  }
}

export function writeSessionText(key: string, value: string): void {
  try {
    sessionStorage.setItem(key, value);
  } catch {
    /* the value is a convenience, not a correctness requirement */
  }
}

export function clearSessionText(key: string): void {
  try {
    sessionStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}
