import { LS } from '@/lib/storage';

export function newUuid(): string {
  const c = globalThis.crypto;
  if (c && 'randomUUID' in c) return c.randomUUID();
  // RFC4122 v4 fallback.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    const v = ch === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

/**
 * Stable signature for a logical action. Retries of the same action reuse the same intent, and
 * therefore the same idempotency key (UM-NET-001). The route template — not the concrete path —
 * keeps a retry of the same resource stable across reloads.
 */
export function actionIntent(
  method: string,
  routeTemplate: string,
  targetId?: string | null,
  extra?: string | null,
): string {
  return `${method}|${routeTemplate}|${targetId ?? '-'}|${extra ?? '-'}`;
}

/**
 * Short stable hash of a request body, folded into an action intent so that retrying the SAME
 * body reuses the idempotency key, while editing and resubmitting a REJECTED action (e.g. a manual
 * patch, or a gate approval whose warning dispositions changed) mints a fresh key instead of
 * tripping S1's "same key, different body" IDEMPOTENCY_CONFLICT.
 */
export function hashJson(value: unknown): string {
  const s = typeof value === 'string' ? value : JSON.stringify(value ?? null);
  let h = 5381;
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) >>> 0;
  return h.toString(36);
}

/**
 * The same job as `hashJson`, in the desktop client's exact spelling and algorithm (S5
 * `src/api/idempotency.ts`, UI-REQ-001): an 8-character FNV-1a digest of a serialized body, folded
 * into the intent of a set-replacement PUT or a create POST.
 *
 * Kept separate rather than folded into `hashJson` because the two consoles must mint the same
 * digest for the same body: an intent is only a *retry* handle while both ends agree on what makes
 * two requests the same logical action, and this is the one place a body digest is observable in
 * the header S1 compares.
 */
export function payloadDigest(text: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}

function store(): Storage | undefined {
  try {
    return sessionStorage;
  } catch {
    return undefined;
  }
}

export function idempotencyKeyFor(intent: string): string {
  const s = store();
  const key = `${LS.idemPrefix}${intent}`;
  if (!s) return newUuid();
  const existing = s.getItem(key);
  if (existing) return existing;
  const fresh = newUuid();
  s.setItem(key, fresh);
  return fresh;
}

export function releaseIdempotencyKey(intent: string): void {
  store()?.removeItem(`${LS.idemPrefix}${intent}`);
}
