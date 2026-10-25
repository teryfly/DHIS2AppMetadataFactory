/** Human-readable byte sizes (UI-REQS-001 shows the same limits the server enforces). */
export function formatBytes(value: number): string {
  if (!value) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  const exponent = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const size = value / 1024 ** exponent;
  return `${size >= 10 || exponent === 0 ? Math.round(size) : size.toFixed(1)} ${units[exponent]}`;
}

/**
 * One free-form JSON value as a line of text: a string stays a string, an object becomes
 * `key: value` pairs, an array joins with a dot. S2 stores the shape the Blueprint actually said
 * (an architecture decision and a mapping one differ), and two screens show it — so the reading
 * belongs in one place rather than beside either of them.
 */
export function decisionText(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value);
  if (Array.isArray(value)) return value.map(decisionText).filter((line) => line !== '').join(' · ');
  if (typeof value === 'object') {
    return Object.entries(value as Record<string, unknown>)
      .map(([key, inner]) => `${key}: ${decisionText(inner)}`)
      .filter((line) => !line.endsWith(': '))
      .join(' · ');
  }
  return String(value);
}
