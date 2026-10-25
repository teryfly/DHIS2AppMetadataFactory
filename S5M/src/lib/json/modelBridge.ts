/**
 * Form ↔ JSON two-way sync (UM-CAP-001: "与表单双向同步且不丢失未知字段").
 *
 * The rule that makes this safe is that there is exactly one master. `model` is the truth; the
 * editor text is a projection of it. Unknown fields are preserved *by construction*: the text is
 * rendered from the complete model — including keys the form cannot render — so parsing the text
 * back cannot lose anything. The loss vector is the other direction (something else replaces the
 * model while the reader is mid-edit), and that is what the `origin` bookkeeping exists to catch.
 */

export type ModelOrigin = 'editor' | 'form' | 'server' | 'localDraft';

export interface BridgeState {
  origin: ModelOrigin;
  /** Bumped every time the editor commits a parse; compared to decide whether to overwrite. */
  textRevision: number;
  /** Bumped every time the model changes from anything other than the editor. */
  modelRevision: number;
  /** The reader has typed since the last commit. */
  textDirty: boolean;
}

export function createBridge(): BridgeState {
  return { origin: 'form', textRevision: 0, modelRevision: 0, textDirty: false };
}

/**
 * Should the current editor text be replaced by `model`?
 *
 * No when the change came from the editor itself (that would reformat under the caret), and no
 * while the reader has unsaved edits — in that case the caller must surface a choice instead of
 * silently clobbering, which is the one place this feature can lose data.
 */
export function shouldProjectModelIntoText(state: BridgeState, pendingOrigin: ModelOrigin): boolean {
  if (pendingOrigin === 'editor') return false;
  return !state.textDirty;
}

/** Should a newly arriving model be offered to the reader as a replace-your-work prompt? */
export function shouldAskBeforeReplacing(state: BridgeState, pendingOrigin: ModelOrigin): boolean {
  return pendingOrigin !== 'editor' && state.textDirty;
}

export function noteTextEdit(state: BridgeState, revision: number): void {
  state.origin = 'editor';
  state.textRevision = revision;
  state.textDirty = true;
}

export function noteTextCommitted(state: BridgeState): void {
  state.textDirty = false;
}

export function noteModelChange(state: BridgeState, origin: ModelOrigin): void {
  state.origin = origin;
  state.modelRevision++;
  if (origin !== 'editor') state.textDirty = false;
}

/**
 * The switch that loses data today: leaving the JSON tab with a document that does not parse must
 * not quietly keep the stale model. Returns true when the caller has to ask first.
 */
export function mustConfirmLeavingJson(textDirty: boolean, hasSyntaxError: boolean): boolean {
  return textDirty && hasSyntaxError;
}

/**
 * Merge an edited section document into the surrounding Blueprint draft without dropping sibling
 * keys. `PATCH /stages/{id}/run` style writes replace one section, so the section itself is
 * authoritative — but the model must still carry keys the form never showed.
 */
export function mergePreservingUnknown<T extends Record<string, unknown>>(base: T | null | undefined, edited: Record<string, unknown>): Record<string, unknown> {
  return { ...(base ?? {}), ...edited };
}
