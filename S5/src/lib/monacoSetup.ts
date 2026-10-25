/*
 * Monaco JSON editor bootstrap for manual authoring (UI-MAN-002).
 *
 * @monaco-editor/react loads the Monaco AMD bundle on demand (default CDN loader) instead of
 * pulling the ~1k-module `monaco-editor` ESM graph into the build — that graph alone exhausts the
 * memory of constrained CI boxes during `vite build`. Set VITE_MONACO_BUNDLED=1 and wire the
 * bundler path (see git history) on a build host with ≥4 GB free RAM to ship an offline image; the
 * editor API surface used here is identical either way.
 */

import type { Monaco } from '@monaco-editor/react';

// No side-effect imports needed with the on-demand loader; kept as a module so JsonEditor can
// `import '@/lib/monacoSetup'` for a single, explicit place to switch loading strategy.

/** The Monaco model path a section's document is edited at; it is also the fileMatch key. */
export function sectionModelPath(section: string): string {
  return `factory://sections/${section}.json`;
}

/**
 * UI-MAN-002: point Monaco at S2's section Schema (delivered unchanged through S1) so the manual
 * JSON editor gets the same validation and completion the agent output is checked against
 * (BP-SCH-006). A schema-less editor only knows JSON syntax, which is what it validated before.
 */
export function applySectionSchema(monaco: Monaco, section: string, schema: Record<string, unknown>): void {
  monaco.languages.json.jsonDefaults.setDiagnosticsOptions({
    validate: true,
    allowComments: false,
    trailingCommas: 'error',
    schemas: [
      {
        uri: `factory://schemas/${section}.json`,
        fileMatch: [sectionModelPath(section)],
        schema: schema as never,
      },
    ],
  });
}

/** JSON syntax validation for the authoring editor; returns null when the text parses. */
export function validateJson(text: string): string | null {
  try {
    JSON.parse(text);
    return null;
  } catch (e) {
    return e instanceof Error ? e.message : String(e);
  }
}
