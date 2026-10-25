/**
 * Copy-to-clipboard with a fallback, because the modern API is not available everywhere this
 * console is opened: `navigator.clipboard` needs a secure context, and a factory intranet served
 * over plain http has none. A silently failing button is worse than the legacy path.
 */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to the legacy path */
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}

/** The address of what is on screen right now — the thing a reviewer pastes into a ticket. */
export function currentUrl(): string {
  return typeof window === 'undefined' ? '' : window.location.href;
}
