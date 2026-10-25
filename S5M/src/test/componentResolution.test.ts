/*
 * A `<script setup>` template resolves its components from the script block's bindings at runtime.
 * Get that wrong and nothing fails to build: `vue-tsc` stays quiet, the bundle ships, and Vue logs
 * "Failed to resolve component" in a console nobody is watching while the control renders as a dead
 * custom element. This file found four of them in the environment sheet — add/remove credential and
 * add/remove commit window — after `FuiButton` was dropped from an import line.
 *
 * So the rule is checked, not remembered: every PascalCase tag in a template has to have a binding
 * of that name in the same file, or be a component the app genuinely registers everywhere.
 */
import { basename } from 'node:path';
import { describe, expect, it } from 'vitest';
import { filesUnder, read } from './walk';

// Installed by main.ts (`app.use(router)`) or by Vue itself; a file may use these without importing.
const GLOBALS = new Set(['RouterView', 'RouterLink', 'Transition', 'TransitionGroup', 'KeepAlive', 'Teleport', 'Suspense']);

const VUE_FILES = filesUnder('src', (p) => p.endsWith('.vue') && !p.includes('/__fixtures__/'));

/** `<script>…</script>` and `<style>…</style>` removed, HTML comments removed: what is left is markup. */
function templateOf(body: string): string {
  return body
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, '')
    .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, '');
}

function scriptOf(body: string): string {
  return [...body.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/gi)].map((m) => m[1]).join('\n');
}

/** Every name the file's own script makes available to its template. */
function bindingsOf(script: string): Set<string> {
  const names = new Set<string>();
  for (const m of script.matchAll(/\bimport\s+(?:([\w$]+)\s*,\s*)?(?:\{([^}]*)\}\s*)?([\w$]+)?/g)) {
    if (m[1]) names.add(m[1]);
    if (m[3]) names.add(m[3]);
    for (const entry of (m[2] ?? '').split(',')) {
      const parts = entry.trim().split(/\s+as\s+/);
      if (parts.length > 1) names.add(parts[parts.length - 1]);
      else if (parts[0]) names.add(parts[0]);
    }
  }
  // `const Foo = …`, including destructured bindings — the shapes a template can legitimately use
  for (const m of script.matchAll(/\b(?:const|let|var|class|function)\s+([\w$]+)/g)) names.add(m[1]);
  for (const m of script.matchAll(/\bconst\s*\{([^}]*)\}\s*=/g)) {
    for (const entry of m[1].split(',')) {
      const parts = entry.split(/\s*:\s*|\s+as\s+/);
      const last = parts[parts.length - 1].trim();
      if (/^[\w$]+$/.test(last)) names.add(last);
    }
  }
  return names;
}

function unresolved(path: string): string[] {
  const body = read(path);
  const bindings = bindingsOf(scriptOf(body));
  // `<script setup>` gives a component its own filename as an implicit binding, which is how
  // OpenApiSchemaTree nests an arbitrary-depth schema without a circular import
  bindings.add(basename(path).replace(/\.vue$/, ''));
  const used = new Set<string>();
  for (const m of templateOf(body).matchAll(/<\s*([A-Z][\w]*(?:[.-][\w]+)*)/g)) {
    const tag = m[1];
    // `<Foo.Bar>` resolves through the default import of a namespace, which no flat name can match;
    // checking the root segment is enough to catch a forgotten import of `Foo`
    used.add(tag.split('.')[0]);
  }
  return [...used].filter((tag) => !bindings.has(tag) && !GLOBALS.has(tag));
}

describe('UM-TECH · 模板里的每个组件都在本文件中有绑定', () => {
  it('every PascalCase tag in a template resolves to an import or a local binding', () => {
    const offenders = VUE_FILES.flatMap((p) => unresolved(p).map((tag) => `${p} uses <${tag}> with no binding in its script block`));
    expect(offenders).toEqual([]);
  });

  it('the scan still covers every single-file component', () => {
    // a filter that silently matched nothing would make the check above vacuously true
    expect(VUE_FILES.length).toBeGreaterThan(40);
    expect(VUE_FILES).toContain('src/features/environments/EnvironmentFormSheet.vue');
  });
});
