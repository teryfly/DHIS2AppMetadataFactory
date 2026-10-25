/*
 * `js-yaml` ships no type declarations (it has no bundled types and `@types/js-yaml` is not
 * installed here). The narrow surface the console actually uses is declared locally so the parse
 * stays type-checked rather than `any`: one function, one documented return.
 *
 * If `@types/js-yaml` is ever added to devDependencies, this file can be deleted — the two
 * declarations of `load` merge as overloads, so nothing breaks in the meantime.
 */
declare module 'js-yaml' {
  /** Parse the first document of a YAML string. Returns the plain JS value, or `undefined` for an empty document. */
  export function load(input: string, options?: { filename?: string; schema?: unknown }): unknown;
}
