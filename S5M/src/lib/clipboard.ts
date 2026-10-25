export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* fall through to legacy path */
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

export function currentUrl(): string {
  return typeof window !== 'undefined' ? window.location.href : '';
}

/** Native share (used for "发送到文件传输助手" on built-in browsers) — falls back to clipboard. */
export async function share(title: string, url: string): Promise<'shared' | 'copied' | 'failed'> {
  const nav = navigator as Navigator & { share?: (d: ShareData) => Promise<void> };
  if (nav.share) {
    try {
      await nav.share({ title, url });
      return 'shared';
    } catch {
      /* user cancelled or unsupported */
    }
  }
  return (await copyText(url)) ? 'copied' : 'failed';
}
