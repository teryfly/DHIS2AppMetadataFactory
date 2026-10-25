/**
 * The page's own origin, empty where there is no window.
 *
 * The single sign-on redirect URI is built from it rather than configured (UM-COMP-004): S5-M is
 * deployed on the same origin as S1, and a URI registered per-environment is one more value a
 * deployment can get wrong than the browser cannot already answer for free.
 */
export function pageOrigin(): string {
  return typeof window === 'undefined' ? '' : window.location.origin;
}

/** Where a single sign-on round trip comes back to. One value, both ends of it. */
export function oidcRedirectUri(): string {
  return `${pageOrigin()}/auth/callback`;
}

/**
 * The only return path a finished sign-in will honour: a path inside this app.
 *
 * `//host` and anything with a scheme are how a login page becomes an open redirect that hands the
 * organisation's new session to somebody else's site, so they collapse to the app root.
 */
export function safeReturnTo(path: string): string {
  return path.startsWith('/') && !path.startsWith('//') ? path : '/';
}
