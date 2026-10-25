import { describe, it, expect } from 'vitest';
import { fmtRelTime } from './time';

const NOW = Date.parse('2026-09-30T14:05:00+08:00');
const iso = (minAgo: number) => new Date(NOW - minAgo * 60000).toISOString();

describe('relative time (UM-UX-004)', () => {
  it('within an hour uses 分钟前', () => {
    expect(fmtRelTime(iso(0), 'zh', NOW)).toBe('刚刚');
    expect(fmtRelTime(iso(12), 'zh', NOW)).toBe('12 分钟前');
    expect(fmtRelTime(iso(12), 'en', NOW)).toBe('12 min ago');
  });

  it('same day shows 今天 HH:mm', () => {
    expect(fmtRelTime(iso(120), 'zh', NOW)).toBe('今天 12:05');
  });

  it('earlier this year shows MM-DD', () => {
    expect(fmtRelTime(iso(60 * 24 * 5), 'zh', NOW)).toBe('09-25 14:05');
  });

  it('cross-year shows full date', () => {
    expect(fmtRelTime(iso(60 * 24 * 400), 'zh', NOW)).toContain('2025-');
  });

  it('handles null/empty', () => {
    expect(fmtRelTime(null, 'zh', NOW)).toBe('');
  });
});
