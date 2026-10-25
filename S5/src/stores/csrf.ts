/*
 * The CSRF value a state-changing call must name (UI-AUTH-001, CP-IAM-004).
 *
 * This store holds that one transient value and nothing else. Who the caller is, their roles and
 * the session's deadline are server state and live in TanStack Query (UI-APP-006); the session
 * itself rides the HttpOnly cookie S1 set at sign-in, which the browser sends and this app cannot
 * read. The CSRF value is the exception because the request middleware runs outside React and
 * needs it synchronously — the same reason the operator name is read the way `operator.ts` reads
 * it (UI-OP-003). It is never persisted: a reload re-reads it from the readable same-origin cookie
 * S1 mirrors it into, which is what keeps a refreshed page able to write at all.
 */
import { create } from 'zustand';

/** S1's `csrf_cookie_name` default; the contract's `securitySchemes.csrfCookie.name` is the authority. */
export const CSRF_COOKIE = 'factory_csrf';
/** S1's `csrf_header` default, declared as `components.parameters.XCsrfToken`. */
export const CSRF_HEADER = 'X-CSRF-Token';

interface CsrfState {
  token: string | null;
  /** Set when a request answered 401: the page has to say so once, on the way back to sign-in. */
  lapsed: boolean;
  setToken: (token: string | null) => void;
  markLapsed: () => void;
  clearLapsed: () => void;
}

export const useCsrfStore = create<CsrfState>()((set) => ({
  token: null,
  lapsed: false,
  setToken: (token) => set({ token }),
  markLapsed: () => set({ lapsed: true }),
  clearLapsed: () => set({ lapsed: false }),
}));

function fromCookie(): string | null {
  if (typeof document === 'undefined') return null;
  const prefix = `${CSRF_COOKIE}=`;
  const entry = document.cookie
    .split(';')
    .map((item) => item.trim())
    .find((item) => item.startsWith(prefix));
  if (!entry) return null;
  try {
    return decodeURIComponent(entry.slice(prefix.length)) || null;
  } catch {
    return null;
  }
}

/** Synchronous read for the API client middleware (outside React render). */
export function getCsrfToken(): string | null {
  return useCsrfStore.getState().token ?? fromCookie();
}

export function clearCsrfToken(): void {
  useCsrfStore.getState().setToken(null);
}
