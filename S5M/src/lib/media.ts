/**
 * Whether an artifact's bytes can be shown as text on this device.
 *
 * `/artifacts/{id}/content` answers with the stored media type and the raw bytes, and the console
 * reads it as text — so a .docx or .pdf used to arrive as replacement characters in a monospace
 * box that looked like a broken document rather than an unrenderable one. This is the gate the two
 * content screens share: text goes to the viewer, anything else gets S1's own download link.
 *
 * Deliberately no word list of "known text types" that grows forever: the rule is a media type that
 * says text (or a JSON/XML/YAML/SQL family), and when the server told us nothing but
 * `application/octet-stream` — which is what a browser sends for a lot of named documents — the
 * filename's extension is the only remaining evidence.
 */
const TEXTUAL_TYPE = /^text\/|^application\/(json|xml|xhtml\+json|xhtml\+xml|yaml|x-yaml|ndjson|x-ndjson|sql|x-sh|x-python|javascript)\b|^image\/svg\+xml\b/;

/** Extensions that only ever hold text, for the times the media type says nothing. */
const TEXTUAL_EXT = /\.(md|markdown|txt|text|csv|tsv|json|jsonc|jsonl|yaml|yml|xml|sql|txt|sh|py|js|ts|html|htm|svg)$/i;

export function looksTextual(mediaType?: string | null, filename?: string | null): boolean {
  const type = (mediaType ?? '').trim().toLowerCase();
  // A type the server actually knew is the answer; only a missing or generic one defers to the name.
  if (type && !type.includes('octet-stream')) return TEXTUAL_TYPE.test(type);
  return TEXTUAL_EXT.test((filename ?? '').trim());
}

/** Bytes, in the units an operator compares documents in. */
export function formatBytes(n?: number | null): string {
  if (n === undefined || n === null || !Number.isFinite(n)) return '';
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${Math.round(n / 1024)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}
