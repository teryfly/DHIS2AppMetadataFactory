/**
 * Whether an artifact's bytes can be shown as text on this device.
 *
 * `/artifacts/{id}/content` answers with the stored media type and the raw bytes, and this console
 * reads it as text — so a .docx or .pdf arrived as replacement characters in a monospace box that
 * looked like a broken document rather than an unrenderable one. The rule: a media type that says
 * text (or a JSON/XML/YAML/SQL family), and when the server only ever heard
 * `application/octet-stream` — which is what a browser sends for many named documents — the
 * filename's extension is the remaining evidence.
 *
 * The mobile console carries the same rule; the two share no code by design, so this file is the
 * desktop copy and `S5M/src/lib/media.ts` the mobile one.
 */
const TEXTUAL_TYPE = /^text\/|^application\/(json|xml|xhtml\+json|xhtml\+xml|yaml|x-yaml|ndjson|x-ndjson|sql|x-sh|x-python|javascript)\b|^image\/svg\+xml\b/;

const TEXTUAL_EXT = /\.(md|markdown|txt|text|csv|tsv|json|jsonc|jsonl|yaml|yml|xml|sql|sh|py|js|ts|html|htm|svg)$/i;

export function looksTextual(mediaType?: string | null, filename?: string | null): boolean {
  const type = (mediaType ?? '').trim().toLowerCase();
  if (type && !type.includes('octet-stream')) return TEXTUAL_TYPE.test(type);
  return TEXTUAL_EXT.test((filename ?? '').trim());
}
