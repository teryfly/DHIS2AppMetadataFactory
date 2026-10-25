/*
 * UI-REQ-001 / UI-BR-04 / UI-AC-07: one logical action keeps a single idempotency key across
 * retries; releasing on confirmation gives the next action a fresh key.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { idempotencyKeyFor, releaseIdempotencyKey, actionIntent, newUuid } from './idempotency';

beforeEach(() => {
  globalThis.sessionStorage?.clear();
});

describe('idempotency keys', () => {
  it('reuses the same key for the same intent across calls', () => {
    const a = idempotencyKeyFor('commit:run:RUN-1');
    const b = idempotencyKeyFor('commit:run:RUN-1');
    expect(a).toBe(b);
    expect(a).toMatch(/^[0-9a-f-]{36}$/);
  });

  it('mints different keys for different intents', () => {
    expect(idempotencyKeyFor('run:ST-1')).not.toBe(idempotencyKeyFor('run:ST-2'));
  });

  it('releases a key so the next action of the same intent is fresh (UI-REQ-004)', () => {
    const first = idempotencyKeyFor('commit:run:RUN-9');
    releaseIdempotencyKey('commit:run:RUN-9');
    const second = idempotencyKeyFor('commit:run:RUN-9');
    expect(second).not.toBe(first);
  });

  it('actionIntent is method+route+target scoped', () => {
    const k = actionIntent('post', '/projects', 'PRJ-1');
    expect(k).toBe('POST|/projects|PRJ-1|-');
  });

  it('newUuid returns a v4 uuid', () => {
    expect(newUuid()).toMatch(/^[\da-f]{8}-[\da-f]{4}-4[\da-f]{3}-[89ab][\da-f]{3}-[\da-f]{12}$/i);
  });
});
