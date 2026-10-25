import { describe, it, expect } from 'vitest';
import { formatRelativeTime, formatDate } from './time';

// A minimal t that echoes the key + count so we assert the right bucket is chosen.
const t = ((key: string, opts?: { count?: number }) => (opts?.count != null ? `${key}:${opts.count}` : key)) as never;

describe('formatRelativeTime', () => {
  const now = Date.now();
  const ago = (ms: number) => new Date(now - ms).toISOString();
  it('buckets: now / minutes / hours / days', () => {
    expect(formatRelativeTime(ago(1000), t)).toBe('common.justNow');
    expect(formatRelativeTime(ago(3 * 60_000), t)).toBe('common.minutesAgo:3');
    expect(formatRelativeTime(ago(2 * 3600_000), t)).toBe('common.hoursAgo:2');
    expect(formatRelativeTime(ago(5 * 86400_000), t)).toBe('common.daysAgo:5');
  });
  it('handles null and malformed input', () => {
    expect(formatRelativeTime(null, t)).toBe('common.unknown');
    expect(formatRelativeTime('not-a-date', t)).toBe('common.unknown');
  });
});

describe('formatDate', () => {
  it('returns an em dash for empty, otherwise a locale string', () => {
    expect(formatDate(null, 'en')).toBe('—');
    expect(formatDate('garbage', 'en')).toBe('garbage');
    expect(formatDate('2026-01-02T03:04:05Z', 'en')).toMatch(/2026/);
  });
});
