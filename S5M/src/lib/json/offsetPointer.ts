import { findNodeAtOffset, format as jsoncFormat, getNodePath, parse as parseJsonc, parseTree, printParseErrorCode, type Edit, type Node as JsoncNode, type ParseError } from 'jsonc-parser';

/**
 * Text↔structure bridge for the JSON editor (UM-CAP-001).
 *
 * Two directions are needed and neither is free:
 *  - error → position: Ajv reports `instancePath` (`/entities/0/buzz`); the editor must draw the
 *    squiggle on the *key*, which is not what the pointer names.
 *  - position → path: completion needs "which object am I inside, and which keys does it already
 *    have", which needs the parse tree, not the parsed value.
 *
 * `jsonc-parser` is chosen over walking the `@lezer/json` tree because it also gives error
 * recovery: a half-typed document still yields a partial tree, so the editor can validate what is
 * there instead of refusing to say anything until the braces balance.
 */

export interface Range {
  from: number;
  to: number;
}

/** JSON Pointer (RFC 6901) for a path array. `''` is the root. */
export function toPointer(path: (string | number)[]): string {
  return path.map((p) => `/${String(p).replace(/~/g, '~0').replace(/\//g, '~1')}`).join('');
}

export function fromPointer(pointer: string): string[] {
  if (!pointer) return [];
  return pointer.split('/').slice(1).map((p) => p.replace(/~1/g, '/').replace(/~0/g, '~'));
}

function tree(text: string, report: ParseError[] = []): JsoncNode | null {
  return parseTree(text, report, { allowTrailingComma: true, disallowComments: false }) ?? null;
}

function keyText(propertyNode: JsoncNode): string {
  const key = propertyNode.children?.[0];
  if (!key) return '';
  return typeof key.value === 'string' ? key.value : String(key.value ?? '');
}

/**
 * The range to mark for a validation error at `pointer`.
 *
 * `name` is Ajv's `params.additionalProperty`: for "must NOT have additional properties" the
 * pointer is the *parent object*, so the offending key is located by name — otherwise the squiggle
 * would land on the whole object instead of on the typo.
 */
export function errorRange(text: string, pointer: string, name?: string): Range | null {
  const root = tree(text);
  if (!root) return null;
  const tokens = fromPointer(pointer);

  if (name) {
    const owner = descend(root, tokens);
    const keyNode = owner ? owner.children?.find((p) => p.type === 'property' && keyText(p) === name)?.children?.[0] : undefined;
    if (keyNode) return { from: keyNode.offset, to: keyNode.offset + keyNode.length };
  }

  const node = descend(root, tokens);
  if (!node) return null;
  const target = node.type === 'property' ? (node.children?.[0] ?? node) : node;
  return { from: target.offset, to: target.offset + target.length };
}

function descend(root: JsoncNode, tokens: string[]): JsoncNode | null {
  let cur: JsoncNode | null = root;
  for (const token of tokens) {
    if (!cur) return null;
    const children: JsoncNode[] = cur.children ?? [];
    if (cur.type === 'object') {
      const prop: JsoncNode | undefined = children.find((p) => p.type === 'property' && keyText(p) === token);
      cur = prop?.children?.[1] ?? null;
    } else if (cur.type === 'array') {
      const idx = Number(token);
      cur = Number.isInteger(idx) ? (children[idx] ?? null) : null;
    } else {
      cur = null;
    }
  }
  return cur;
}

/** Keys already written inside the object that contains `offset`. */
export function presentKeys(text: string, offset: number): { pointer: string; keys: string[] } {
  const root = tree(text);
  if (!root) return { pointer: '', keys: [] };
  let node: JsoncNode = findNodeAtOffset(root, offset, true) ?? root;
  // Climb to the enclosing object: on a key the anchor is the Property, on a value the String.
  while (node.type !== 'object' && node.parent) node = node.parent;
  if (node.type !== 'object') return { pointer: '', keys: [] };
  return {
    pointer: toPointer(getNodePath(node)),
    keys: (node.children ?? []).filter((p) => p.type === 'property').map(keyText),
  };
}

export type CursorContext =
  | { kind: 'key'; pointer: string; presentKeys: string[]; partial: string }
  | { kind: 'value'; pointer: string; isArrayItem: boolean; partial: string }
  | { kind: 'root'; pointer: string; partial: string };

/** What the caret is doing: writing a key name, or writing a value. */
export function cursorContext(text: string, offset: number): CursorContext {
  const root = tree(text);
  if (!root) return { kind: 'root', pointer: '', partial: '' };
  const anchor = findNodeAtOffset(root, offset, true) ?? root;
  const partial = partialToken(text, offset);

  const onKey = anchor.type === 'string' && anchor.parent?.type === 'property' && anchor.parent.children?.[0] === anchor;
  if (onKey && anchor.parent) {
    const objectNode = anchor.parent.parent ?? root;
    return {
      kind: 'key',
      pointer: toPointer(getNodePath(objectNode)),
      presentKeys: (objectNode.children ?? []).filter((p) => p.type === 'property').map(keyText),
      partial,
    };
  }

  if (anchor.type === 'object' || anchor.type === 'array') {
    const path = getNodePath(anchor);
    // Inside `{`…`}` the next thing typed is a key; inside `[`…`]` it is an item value.
    if (anchor.type === 'array') return { kind: 'value', pointer: toPointer(path), isArrayItem: true, partial };
    return { kind: 'key', pointer: toPointer(path), presentKeys: (anchor.children ?? []).filter((p) => p.type === 'property').map(keyText), partial };
  }

  return { kind: 'value', pointer: toPointer(getNodePath(anchor)), isArrayItem: false, partial };
}

/** The word fragment under the caret, used to rank completion candidates. */
function partialToken(text: string, offset: number): string {
  const before = text.slice(Math.max(0, offset - 64), offset);
  const m = before.match(/["']?([A-Za-z_$][\w$-]*)$/);
  return m ? m[1] : '';
}

/** Syntax diagnostics straight from the tolerant parser, already positioned. */
export function syntaxIssues(text: string): { range: Range; message: string }[] {
  const errors: ParseError[] = [];
  tree(text, errors);
  return errors.map((e) => ({
    range: { from: e.offset, to: e.offset + Math.max(1, e.length) },
    message: printParseErrorCode(e.error),
  }));
}

/** Parse with recovery; returns undefined when the document is too broken to validate. */
export function tryParse(text: string): unknown {
  const errors: ParseError[] = [];
  const value = parseJsonc(text, errors, { allowTrailingComma: true, disallowComments: false });
  return errors.length ? undefined : value;
}

/** Format edits for the 格式化 command — applied as one ChangeSet, so it undoes as a single step. */
export function formatEdits(text: string, tabSize = 2): Edit[] {
  return jsoncFormat(text, undefined, { insertSpaces: true, tabSize });
}
