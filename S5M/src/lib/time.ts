import dayjs from 'dayjs';

// UM-UX-004 time display. Browser local time, 24-hour clock. Within an hour → "刚刚 / N 分钟前";
// same day → "今天 HH:mm"; earlier this year → "MM-DD HH:mm"; cross-year → "YYYY-MM-DD".
export type TimeLang = 'zh' | 'en';

const pad = (n: number) => String(n).padStart(2, '0');

export function fmtRelTime(iso: string | number | Date | null | undefined, lang: TimeLang = 'zh', now: number = Date.now()): string {
  if (!iso) return '';
  const d = dayjs(iso);
  if (!d.isValid()) return '';
  const diffMin = (now - d.valueOf()) / 60000;
  if (diffMin < 0) return lang === 'zh' ? '刚刚' : 'just now';
  if (diffMin < 1) return lang === 'zh' ? '刚刚' : 'just now';
  if (diffMin < 60) return lang === 'zh' ? `${Math.floor(diffMin)} 分钟前` : `${Math.floor(diffMin)} min ago`;

  const today = dayjs(now);
  const hm = `${pad(d.hour())}:${pad(d.minute())}`;
  if (d.format('YYYY-MM-DD') === today.format('YYYY-MM-DD')) return lang === 'zh' ? `今天 ${hm}` : `today ${hm}`;
  if (d.year() === today.year()) return lang === 'zh' ? `${pad(d.month() + 1)}-${pad(d.date())} ${hm}` : `${pad(d.month() + 1)}-${pad(d.date())} ${hm}`;
  return d.format('YYYY-MM-DD');
}

/** Full timestamp (24h, local) for tooltips. */
export function fmtAbs(iso: string | number | Date): string {
  return dayjs(iso).format('YYYY-MM-DD HH:mm:ss');
}

/** "hh:mm" used by the offline banner ("显示的是 xx:xx 的数据"). */
export function fmtClock(ts: number | Date = Date.now()): string {
  return dayjs(ts).format('HH:mm');
}
