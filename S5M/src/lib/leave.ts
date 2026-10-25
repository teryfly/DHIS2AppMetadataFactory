/** Leave this document for a full page load elsewhere. See `@/lib/origin` for who builds the URL. */
export function leaveTo(url: string): void {
  if (typeof window === 'undefined') return;
  window.location.assign(url);
}
