/* Relative-time formatting used by dashboards, stage nav and audit (UI-PRJ-001, UI-WB-002). */
import type { TFunction } from 'i18next';

const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

export function formatRelativeTime(iso: string | null | undefined, t: TFunction): string {
  if (!iso) return t('common.unknown');
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return t('common.unknown');
  const diff = Date.now() - then;
  if (diff < MIN) return t('common.justNow');
  if (diff < HOUR) return t('common.minutesAgo', { count: Math.round(diff / MIN) });
  if (diff < DAY) return t('common.hoursAgo', { count: Math.round(diff / HOUR) });
  return t('common.daysAgo', { count: Math.round(diff / DAY) });
}

export function formatDate(iso: string | null | undefined, locale: string): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(locale, { dateStyle: 'medium', timeStyle: 'short' });
}
