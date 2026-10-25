import type { JsonSchema } from '@/fui/schemaTypes';
import { enumCandidates, itemsSchema, propertySchema } from '@/lib/json/resolveSchema';
import {
  BUSINESS_KEY_MAX,
  BUSINESS_KEY_MIN,
  BUSINESS_KEY_PATTERN,
  OPEN_QUESTION_SEVERITIES,
  REQUIREMENT_PRIORITIES,
  REQUIREMENT_STATUSES,
  SOURCE_NOTE_MAX,
  STATEMENT_MAX,
  isBusinessKey,
} from '@/lib/enums';

/**
 * The entry↔operation layer of the requirement screen, with no Vue and no fetch in it.
 *
 * Everything the screen writes is one RFC 6902 `replace` of the whole `requirements` item array,
 * because that is the only address S2's patch engine accepts for a set change: the read model sorts
 * entries by `businessKey` (`S2/src/factory_blueprint/readmodel.py`) and prints a *key* address in
 * `_path`, while `patch/rfc6902.py` only understands an array index or `-`. Nothing on the wire
 * tells a client where an item sits in the stored array, so an index targeted at the read order
 * would edit a different entry than the one the operator tapped.
 */

/** S2's read-model locators: they address an entry, they are not entry fields, and they must not travel back. */
const LOCATORS = new Set(['_section', '_path']);

/** The array the screen owns. Nothing outside it is ever written. */
export const REQUIREMENTS_CONTAINER = '/requirements/items';

export interface OpenQuestion {
  question: string;
  severity: string;
}

export interface RequirementItem {
  businessKey: string;
  statement: string;
  source: Record<string, unknown>;
  rationale?: string;
  priority?: string;
  status?: string;
  confidence?: number;
  actors?: string[];
  openQuestions?: OpenQuestion[];
  [key: string]: unknown;
}

export type RequirementEdit =
  | { kind: 'create'; item: RequirementItem }
  | { kind: 'update'; businessKey: string; item: RequirementItem }
  | { kind: 'delete'; businessKey: string };

export type PatchOperation = { op: 'add' | 'replace' | 'remove'; path: string; value?: unknown };

function stripLocators(entry: Record<string, unknown>): RequirementItem {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(entry)) if (!LOCATORS.has(key)) out[key] = value;
  return out as unknown as RequirementItem;
}

/**
 * The entries S1 answered, as items.
 *
 * Sorted by `businessKey` to mirror the read model, so writing the array back is a re-order the
 * stored index already expects (`readmodel.py` addresses array rows by entry key precisely so they
 * survive a re-order) and a read → write round trip changes nothing on its own.
 */
export function itemsFromEntries(entries: readonly Record<string, unknown>[]): RequirementItem[] {
  return entries.map(stripLocators).sort((a, b) => (String(a.businessKey ?? '') < String(b.businessKey ?? '') ? -1 : 1));
}

/**
 * The rules S1 serves for this section (`GET /blueprint/section-schemas/requirements`, S5IF-BP02),
 * with `lib/enums.ts` behind them. The served document wins: when S2 changes an enum or a length,
 * the form follows without a console release, and the constants are only what this console believed
 * the last time it looked.
 */
export interface RequirementRules {
  keyPattern: RegExp;
  keyMin: number;
  keyMax: number;
  statementMax: number;
  noteMax: number;
  priorities: string[];
  statuses: string[];
  severities: string[];
  confidenceMin: number;
  confidenceMax: number;
  /** The `source` alternatives S2 accepts (`anyOf`), e.g. `["artifactId","decisionId","note"]`. */
  sourceKeys: string[];
}

function int(schema: JsonSchema | null | undefined, key: 'minLength' | 'maxLength' | 'minimum' | 'maximum', fallback: number): number {
  const value = schema?.[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : fallback;
}

export function requirementRules(schema?: JsonSchema | null): RequirementRules {
  // Navigated as a schema document, not as a data pointer: `schemaAtPointer` walks *values*
  // (`/entities/0/name`), so asking it for `/properties/…` looks for a property named `properties`.
  const container = (schema?.properties?.items ?? null) as JsonSchema | null;
  const item = container?.items && !Array.isArray(container.items) ? (container.items as JsonSchema) : null;
  const field = (name: string) => (item ? propertySchema(schema as JsonSchema, item, name).schema : null);
  const key = field('businessKey');
  const statement = field('statement');
  const confidence = field('confidence');
  const priority = field('priority');
  const status = field('status');
  const source = field('source');
  const questions = itemsSchema(schema as JsonSchema, field('openQuestions')).schema;
  const severity = questions ? propertySchema(schema as JsonSchema, questions, 'severity').schema : null;

  const pattern = typeof key?.pattern === 'string' ? key.pattern : '';
  const anyOf = Array.isArray(source?.anyOf) ? (source?.anyOf as Record<string, unknown>[]) : [];
  const offered = anyOf.map((arm) => String((arm?.required as string[] | undefined)?.[0] ?? '')).filter(Boolean);

  return {
    keyPattern: pattern ? safePattern(pattern, BUSINESS_KEY_PATTERN) : BUSINESS_KEY_PATTERN,
    keyMin: int(key, 'minLength', BUSINESS_KEY_MIN),
    keyMax: int(key, 'maxLength', BUSINESS_KEY_MAX),
    statementMax: int(statement, 'maxLength', STATEMENT_MAX),
    noteMax: int(propertySchema(schema as JsonSchema, source, 'note').schema, 'maxLength', SOURCE_NOTE_MAX),
    priorities: enumCandidates(priority).length ? enumCandidates(priority) : [...REQUIREMENT_PRIORITIES],
    statuses: enumCandidates(status).length ? enumCandidates(status) : [...REQUIREMENT_STATUSES],
    severities: enumCandidates(severity).length ? enumCandidates(severity) : [...OPEN_QUESTION_SEVERITIES],
    confidenceMin: int(confidence, 'minimum', 0),
    confidenceMax: int(confidence, 'maximum', 1),
    sourceKeys: offered.length ? offered : ['artifactId', 'decisionId', 'note'],
  };
}

/** A pattern that will not compile is a contract problem, not a reason to refuse every keystroke. */
function safePattern(source: string, fallback: RegExp): RegExp {
  try {
    return new RegExp(source);
  } catch {
    return fallback;
  }
}

/**
 * The item's own violations, as translated sentences.
 *
 * S2 validates the same rules after the write (`patch/validate.py`, BPV-PCH-002/003); running them
 * here is what keeps a tap on 提交 from ending in a rejection the operator has to decode, and it is
 * the only place a duplicate key can be caught before it is sent.
 *
 * `problems` is a `t()`d string list on purpose (the `EnvironmentFormSheet.vue` idiom) — this module
 * takes `t` instead of importing the i18n global, so it stays testable without a plugin.
 */
export function requirementProblems(
  item: RequirementItem,
  read: readonly RequirementItem[],
  rules: RequirementRules,
  /** The key this edit started from; `null` while creating. */
  editing: string | null,
  t: (key: string, named?: Record<string, unknown>) => string,
): string[] {
  const out = new Set<string>();
  const key = String(item.businessKey ?? '').trim();
  const statement = String(item.statement ?? '').trim();

  if (!key) out.add(t('reqItems.keyRequired'));
  else if (key.length < rules.keyMin || key.length > rules.keyMax || !rules.keyPattern.test(key)) {
    out.add(t('reqItems.keyShape', { min: rules.keyMin, example: 'req.delivery.call' }));
  }
  if (key && read.some((i) => String(i.businessKey ?? '') === key && i.businessKey !== editing)) {
    out.add(t('reqItems.keyTaken', { key }));
  }
  if (!statement) out.add(t('reqItems.statementRequired'));
  else if (statement.length > rules.statementMax) out.add(t('reqItems.statementTooLong', { max: rules.statementMax }));

  const note = String(item.source?.note ?? '').trim();
  if (note.length > rules.noteMax) out.add(t('reqItems.noteTooLong', { max: rules.noteMax }));
  if (!rules.sourceKeys.some((field) => String(item.source?.[field] ?? '').trim())) {
    out.add(t('reqItems.sourceRequired'));
  }

  if (item.confidence !== undefined && item.confidence !== null) {
    const value = Number(item.confidence);
    if (!Number.isFinite(value) || value < rules.confidenceMin || value > rules.confidenceMax) {
      out.add(t('reqItems.confidenceRange', { min: rules.confidenceMin, max: rules.confidenceMax }));
    }
  }
  if (item.priority && !rules.priorities.includes(item.priority)) out.add(t('reqItems.priorityUnknown', { value: item.priority }));
  if (item.status && !rules.statuses.includes(item.status)) out.add(t('reqItems.statusUnknown', { value: item.status }));
  for (const q of item.openQuestions ?? []) {
    if (!String(q.question ?? '').trim()) out.add(t('reqItems.questionTextRequired'));
    else if (q.severity && !rules.severities.includes(q.severity)) out.add(t('reqItems.questionSeverityUnknown', { value: q.severity }));
  }
  return [...out];
}

/** True for a key the served pattern accepts — the screen uses it to freeze the field on an edit. */
export const validBusinessKey = (value: string): boolean => isBusinessKey(value);

/**
 * The array this edit produces.
 *
 * An update merges into the item that was read rather than replacing it: `actors`, a
 * `source.artifactId` the parse pipeline recorded, or a field this sheet does not show at all must
 * survive a save that only meant to change the statement.
 */
export function applyEdit(read: readonly RequirementItem[], edit: RequirementEdit): RequirementItem[] {
  if (edit.kind === 'delete') {
    return itemsFromEntries(read.filter((i) => String(i.businessKey ?? '') !== edit.businessKey) as unknown as Record<string, unknown>[]);
  }
  const next = edit.item;
  const exists = read.some((i) => String(i.businessKey ?? '') === String(next.businessKey ?? ''));
  const merged = exists
    ? read.map((i) => (String(i.businessKey ?? '') === String(next.businessKey ?? '') ? mergeItem(i, next) : i))
    : [...read, next];
  return itemsFromEntries(merged as unknown as Record<string, unknown>[]);
}

/**
 * `source` is merged key by key while every other field is replaced.
 *
 * The sheet edits one member of it — the note — and S2 requires the object to keep at least one of
 * artifactId / decisionId / note. A shallow replace would drop the artifact the parse pipeline
 * recorded, and BPV-PCH-002 would refuse the whole patch for an edit that only reworded a sentence.
 */
function mergeItem(stored: RequirementItem, next: RequirementItem): RequirementItem {
  const out: RequirementItem = { ...stored, ...next };
  out.source = { ...(stored.source as Record<string, unknown> | undefined), ...(next.source as Record<string, unknown> | undefined) };
  return out;
}

/**
 * The operations one edit produces.
 *
 * A create *appends* (`/requirements/items/-`), which is the only shape that cannot lose an entry
 * somebody else added. An update or a delete has to rewrite the container — see the file header —
 * so the caller must hand it a read taken immediately before submitting (`RequirementsView`
 * re-fetches for exactly that reason).
 *
 * No `test` operation is emitted, deliberately: S2's patch engine addresses an array member by
 * index, and the entries read is sorted by `businessKey` (`readmodel.py`) with no position
 * reported, so a guard on "the element at index n-1" asserts something about an order this side
 * cannot observe. Verified against a live draft: with the document in insertion order and the read
 * in key order, the guard failed and a legitimate deletion came back 422 `SCHEMA_ERROR`
 * (`BP_PATCH_MALFORMED`) — it blocked the edit it was meant to protect.
 */
export function buildRequirementPatch(
  read: readonly RequirementItem[],
  edit: RequirementEdit,
): { operations: PatchOperation[]; next: RequirementItem[]; removed: RequirementItem | null } {
  const next = applyEdit(read, edit);
  const removed = edit.kind === 'delete' ? read.find((i) => String(i.businessKey ?? '') === edit.businessKey) ?? null : null;
  if (edit.kind === 'create') {
    return { operations: [{ op: 'add', path: `${REQUIREMENTS_CONTAINER}/-`, value: edit.item }], next, removed };
  }
  return { operations: [{ op: 'replace', path: REQUIREMENTS_CONTAINER, value: next }], next, removed };
}

/**
 * The review screen's status write: one array replacement for one key or every key.
 *
 * S2 addresses an entry by array position, and the read is sorted by businessKey, so a status
 * change for one row still rewrites the container. `keys === null` means "all of them" (the
 * 全部通过 action); an explicit list is the per-row verdict.
 */
export function buildStatusPatch(
  read: readonly RequirementItem[],
  keys: readonly string[] | null,
  status: 'CONFIRMED' | 'REJECTED',
): { operations: PatchOperation[]; next: RequirementItem[]; changed: number } {
  const wanted = keys === null ? null : new Set(keys.map(String));
  let changed = 0;
  const next = read.map((item) => {
    if (wanted !== null && !wanted.has(String(item.businessKey ?? ''))) return item;
    if (item.status === status) return item;
    changed += 1;
    return { ...item, status };
  });
  return {
    operations: changed ? [{ op: 'replace', path: REQUIREMENTS_CONTAINER, value: next }] : [],
    next,
    changed,
  };
}

/** What the draft was changed for, in the operator's own terms — S2 keeps it as patch metadata. */
export function editRationale(edit: RequirementEdit, t: (key: string, named?: Record<string, unknown>) => string): string {
  if (edit.kind === 'create') return t('reqItems.rationaleCreate', { key: edit.item.businessKey });
  if (edit.kind === 'delete') return t('reqItems.rationaleDelete', { key: edit.businessKey });
  return t('reqItems.rationaleUpdate', { key: edit.item.businessKey });
}

/** The provenance the sheet does not edit but must not lose. */
export function provenanceOf(item: RequirementItem | null): { artifactId?: string; decisionId?: string; location?: string } {
  const source = (item?.source ?? {}) as Record<string, unknown>;
  return {
    artifactId: typeof source.artifactId === 'string' ? source.artifactId : undefined,
    decisionId: typeof source.decisionId === 'string' ? source.decisionId : undefined,
    location: typeof source.location === 'string' ? source.location : undefined,
  };
}
