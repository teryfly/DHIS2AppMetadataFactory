/** The page's own origin, empty under a server render. Same-origin is what makes the cookie
 * session viable (UI-APP-008), so the redirect URI is built from it rather than configured. */
export function pageOrigin(): string {
  return typeof window === 'undefined' ? '' : window.location.origin;
}
