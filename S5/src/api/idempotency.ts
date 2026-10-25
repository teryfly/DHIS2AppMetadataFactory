/*
 * UI-REQ-001 / UI-BR-04: every create-class and execution-class user action carries an
 * Idempotency-Key. A single logical action keeps ONE key so automatic or manual retries reuse
 * it (S1 replays the stored response). COMMIT (UI-REQ-004) is never auto-resubmitted on a
 * network failure — the pending key is kept for the user to retry explicitly.
 *
 * The key lives in sessionStorage, scoped to a stable intent signature, so a page reload that
 * retries the same logical action still reuses the key.
 */
import { useEffect, useState } from 'react';

const NS = 'damf:idem:';

export function newUuid(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c?.randomUUID) return c.randomUUID();
  // RFC4122 v4 fallback for environments without crypto.randomUUID.
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (ch) => {
    const r = (Math.random() * 16) | 0;
    const v = ch === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function storage(): Pick<Storage, 'getItem' | 'setItem' | 'removeItem'> | null {
  try {
    return globalThis.sessionStorage ?? null;
  } catch {
    return null;
  }
}

/** Read or mint a stable key for the given action intent. */
export function idempotencyKeyFor(intent: string): string {
  const s = storage();
  const idKey = NS + intent;
  const existing = s?.getItem(idKey);
  if (existing) return existing;
  const fresh = newUuid();
  s?.setItem(idKey, fresh);
  return fresh;
}

/** Drop the key once an action is confirmed, so the next action of the same intent gets a new key. */
export function releaseIdempotencyKey(intent: string): void {
  storage()?.removeItem(NS + intent);
}

/**
 * React hook: returns a stable idempotency key for `intent`. The intent string should encode the
 * action + target (e.g. `commit:run:RUN-123`) and change when the user starts a *new* action.
 */
export function useIdempotencyKey(intent: string | null): string | undefined {
  const [key, setKey] = useState<string | undefined>(() => (intent ? idempotencyKeyFor(intent) : undefined));
  useEffect(() => {
    setKey(intent ? idempotencyKeyFor(intent) : undefined);
  }, [intent]);
  return key;
}

/**
 * Build an intent signature from method + resolved route template + a short payload hash so the
 * same button on the same object yields the same key, but a genuinely new action yields a new one.
 */
export function actionIntent(method: string, routeTemplate: string, targetId?: string, extra?: string): string {
  return [method.toUpperCase(), routeTemplate, targetId ?? '-', extra ?? '-'].join('|');
}

/**
 * A short stable digest of a text, for the `extra` of `actionIntent`: two identical chat turns
 * must replay as one request while a different message is a new action.  Not a security hash —
 * it only distinguishes intents, so it is computed inline rather than through async crypto.
 */
export function payloadDigest(text: string): string {
  let hash = 0x811c9dc5;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash.toString(16).padStart(8, '0');
}
