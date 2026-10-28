/*
 * UI-NFR-05: MSW handlers shaped exactly like S1 (camelCase envelopes, the error envelope, the
 * header rules). Mutating handlers enforce X-Operator (CP-API-002 → 400 OPERATOR_REQUIRED) and an
 * Idempotency-Key on create/execute actions, so the operator-gating and idempotency behaviours of
 * the real UI are exercised against the mock too.
 */
import { http, HttpResponse } from 'msw';
import * as db from './db';
import type { ProjectView } from '@/api/types';

const P = '/api/v1';

function envelope(code: string, status: number, extra: Record<string, unknown> = {}): Response {
  return HttpResponse.json(
    {
      errorCode: code,
      message: code,
      stage: null,
      artifactId: null,
      retryable: status >= 500,
      correlationId: 'mock-' + Math.random().toString(36).slice(2, 10),
      details: {},
      ...extra,
    },
    { status },
  );
}

/**
 * The stage context S1 attaches to every dialogue answer (FR-UI-002, AI-CHAT-003).  Built from the
 * same mock stage the screens already read, so the panel's header cannot disagree with the timeline.
 */
function stageChatContext(stageId: string) {
  const stages = (db.workflow1 as unknown as { stages?: { id: string; stageType: string; status: string }[] }).stages ?? [];
  const found = stages.find((item) => item.id === stageId);
  return {
    projectId: db.DESIGN_PROJECT_ID,
    workflowId: (db.workflow1 as unknown as { id: string }).id,
    project: db.DESIGN_PROJECT_ID,
    stage: stageId,
    stageType: found?.stageType ?? 'REQUIREMENTS',
    stageStatus: found?.status ?? 'READY',
    activeDraftId: `draft-${db.DESIGN_PROJECT_ID}`,
    targetEnvironments: ['env-dev'],
  };
}

/** Enforce CP-API-002 (operator) and CP-API-003 (idempotency on create/execute). */
function requireOperator(request: Request, requireIdempotency = true): Response | null {
  if (!request.headers.get('X-Operator')) return envelope('OPERATOR_REQUIRED', 400);
  if (requireIdempotency && !request.headers.get('Idempotency-Key')) return envelope('IDEMPOTENCY_KEY_REQUIRED', 400);
  return null;
}

const json = (body: unknown, status?: number) =>
  status ? HttpResponse.json(body as never, { status }) : HttpResponse.json(body as never);

/** The roles one mock account holds; `users` is a loose row, so the list is read defensively. */
function heldRoles(row: Record<string, unknown>): Record<string, unknown>[] {
  return Array.isArray(row.roles) ? (row.roles as Record<string, unknown>[]) : [];
}

/**
 * S1 refuses an instance-level operation on a DISABLED environment (services/environments.py) --
 * the same 409 under either provider. A mock that answered 200 here would let the console pass
 * every test and then meet a refusal it had never rendered.
 */
function outOfService(key: unknown): Response | null {
  const row = db.state.environments.find((e) => e.environmentKey === key);
  if (row?.status !== 'DISABLED') return null;
  return envelope('INVALID_STATE', 409, {
    details: { environmentKey: String(key), status: 'DISABLED', reasonCode: 'ENVIRONMENT_DISABLED' },
  });
}

/**
 * UI-OPS-002: S1 filters the query and then pages the filtered result, so the mock does both in the
 * same order. Answering the whole fixture no matter what `limit` says would let a screen look
 * complete while the real one shows a page.
 */
function auditPage(request: Request, items: { eventType?: string | null; operatorName?: string | null; objectId?: string | null; occurredAt?: string | null }[]) {
  const params = new URL(request.url).searchParams;
  const after = params.get('occurredAfter');
  const before = params.get('occurredBefore');
  const filtered = items.filter((row) => {
    if (params.get('eventType') && row.eventType !== params.get('eventType')) return false;
    if (params.get('operatorName') && !(row.operatorName ?? '').includes(String(params.get('operatorName')))) return false;
    if (params.get('objectId') && row.objectId !== params.get('objectId')) return false;
    const at = row.occurredAt ?? '';
    if (after && at && at < after) return false;
    if (before && at && at > before) return false;
    return true;
  });
  // The cursor is opaque in S1 and an offset here; neither end treats it as a number, and the
  // contract only asks that it come back on the next request.
  const limit = Number(params.get('limit')) || filtered.length || 1;
  const start = Number(params.get('cursor')) || 0;
  const page = filtered.slice(start, start + limit);
  const next = start + page.length < filtered.length ? String(start + page.length) : null;
  return { items: page, total: filtered.length, limit: page.length, offset: start, nextCursor: next };
}

const DEFAULT_RUN = { id: 'RUN-1', status: 'VALIDATED', environmentKey: 'env-dev', environmentClass: 'TEST', workflowId: db.WORKFLOW_ID, outdated: false };

export const handlers = [
  http.get(`${P}/healthz`, () => json({ status: 'ok' })),

  // ---- projects ----
  http.get(`${P}/projects`, ({ request }) => {
    const archived = new URL(request.url).searchParams.get('includeArchived') === 'true';
    const items = db.state.projects.filter((p) => (archived ? true : !p.archived));
    return json({ items, total: items.length });
  }),
  http.post(`${P}/projects`, async ({ request }) => {
    const r = requireOperator(request, false); // create project is not idempotency-keyed per re-synced S1
    if (r) return r;
    const body = (await request.json()) as Record<string, unknown>;
    const created = {
      id: 'PRJ-' + Math.random().toString(36).slice(2, 7),
      name: String(body.name ?? 'Untitled'),
      description: (body.description as string) ?? null,
      deliveryMode: (body.deliveryMode as string) ?? 'A',
      projectType: (body.projectType as string) ?? 'GREENFIELD',
      ownerName: (body.ownerName as string) ?? null,
      defaultLanguage: (body.defaultLanguage as string) ?? 'en',
      translationLanguages: (body.translationLanguages as string[]) ?? [],
      workflowId: 'WF-' + Math.random().toString(36).slice(2, 6),
      workflowStatus: 'CREATED',
      archived: false,
      stages: [
        { id: 'ST-r', stageType: 'REQUIREMENTS', status: 'PENDING', attempt: 0 },
        { id: 'ST-d', stageType: 'DOMAIN', status: 'PENDING', attempt: 0 },
        { id: 'ST-p', stageType: 'PAGE_DESIGN', status: body.deliveryMode === 'A' ? 'SKIPPED' : 'PENDING', attempt: 0 },
      ],
      targetEnvironments: (body.targetEnvironments as unknown[]) ?? [],
    };
    db.state.projects = [...db.state.projects, created as unknown as ProjectView];
    return json(created, 201);
  }),
  http.get(`${P}/projects/:id`, ({ params }) => {
    const found = db.state.projects.find((p) => p.id === params.id);
    return found ? json(found) : envelope('NOT_FOUND', 404);
  }),
  http.patch(`${P}/projects/:id`, async ({ request, params }) => {
    const guard = requireOperator(request);
    if (guard) return guard;
    const found = db.state.projects.find((p) => p.id === params.id);
    if (!found) return envelope('NOT_FOUND', 404);
    const body = (await request.json()) as Record<string, unknown>;
    // UpdateProjectBody: an omitted field means "leave it alone".
    Object.assign(found, body);
    return json(found);
  }),

  // ---- workflow / stages ----
  http.get(`${P}/workflows/:id`, ({ params }) =>
    params.id === db.WORKFLOW_ID ? json({ ...db.workflow1, deploymentRuns: db.state.runs }) : json({ id: String(params.id), projectId: 'PRJ-?', status: 'IN_PROGRESS', stages: [] }),
  ),
  http.get(`${P}/workflows/:id/stages`, () => json({ items: db.workflow1.stages ?? [], total: (db.workflow1.stages ?? []).length })),
  http.get(`${P}/workflows/:id/allowed-actions`, ({ params }) => json(db.workflowActionsView(String(params.id)))),
  // FR-UI-009 for the Administration pages: same entry shape, `scope: 'platform'`, no workflow.
  http.get(`${P}/allowed-actions`, () => json(db.platformActionsView())),
  http.get(`${P}/setup`, () => json(db.setupView())),
  http.get(`${P}/workflows/:id/questions`, () => json({ items: db.state.questions, total: db.state.questions.length })),
  http.get(`${P}/workflows/:id/gates`, () => json({ items: [db.state.gate1], total: 1 })),

  // The three workflow flags are the state the console reads back, so the mock writes them onto
  // the row rather than returning a value nothing else ever sees -- otherwise "pause" appears to
  // succeed and the screen keeps offering Pause.
  http.post(`${P}/workflows/:id/pause`, ({ request }) => {
    const denied = requireOperator(request, false);
    if (denied) return denied;
    db.workflow1.paused = true;
    return json({ workflowId: db.WORKFLOW_ID, paused: true });
  }),
  http.post(`${P}/workflows/:id/resume`, ({ request }) => {
    const denied = requireOperator(request, false);
    if (denied) return denied;
    db.workflow1.paused = false;
    return json({ workflowId: db.WORKFLOW_ID, paused: false });
  }),
  http.post(`${P}/workflows/:id/cancel`, ({ request }) => {
    const denied = requireOperator(request, false);
    if (denied) return denied;
    db.workflow1.cancelled = true;
    return json({ workflowId: db.WORKFLOW_ID, cancelled: true });
  }),

  // ---- stage actions ----
  http.post(`${P}/stages/:id/run`, ({ request }) => requireOperator(request, false) ?? json({ stageId: 'ST-req', accepted: true, stepRunId: 'SR-1' }, 202)),
  http.post(`${P}/stages/:id/rerun`, ({ request }) => requireOperator(request, false) ?? json({ stageId: 'ST-req', accepted: true, stepRunId: 'SR-2' }, 202)),
  http.post(`${P}/stages/:id/patches`, ({ request }) =>
    requireOperator(request) ?? json({ stageId: 'ST-req', patchStatus: 'ACCEPTED', assignedUids: { 'req:new': 'aa11bb22cc33dd44' }, draftHash: 'sha256:cd34', issues: [], questions: 0 }),
  ),
  http.post(`${P}/stages/:id/submit-for-approval`, ({ request }) =>
    requireOperator(request, false) ?? json({ stageId: 'ST-req', status: 'WAITING_APPROVAL', gateId: 'GT-1', draftHash: 'sha256:cd34', blocking: 0, issues: [] }),
  ),
  http.post(`${P}/stages/:id/resolve-manual-check`, ({ request }) =>
    requireOperator(request, false) ?? json({ executionId: 'EX-1', resolved: true, status: 'PASSED', releasesLock: false }),
  ),

  // ---- gate ----
  // The cross-project queue (S5IF-FLD03) answers with S1's own tokens and its paging envelope, and
  // honours `projectId`/`mine` rather than ignoring them: an inbox whose filter changes nothing on
  // screen is a filter test can never tell apart from a working one.
  http.get(`${P}/gates`, ({ request }) => {
    const url = new URL(request.url);
    const mine = url.searchParams.get('mine') === 'true';
    const project = url.searchParams.get('projectId');
    const status = url.searchParams.get('status');
    const rows = [
      { id: 'GT-1', type: 'REQUIREMENT_GATE', status: 'WAITING', projectId: db.DESIGN_PROJECT_ID, projectName: db.project1.name, workflowId: db.WORKFLOW_ID, environmentKey: 'env-dev', createdAt: '2026-10-03T08:00:00Z', stageId: 'ST-req', warningCount: db.gate1.warnings?.length ?? 0, unresolvedWarningCount: db.gate1.warnings?.length ?? 0, requiredApprovals: 1, approvalCount: 0, highOpenQuestionCount: 1 },
      { id: 'GT-2', type: 'DEPLOY_GATE', status: 'WAITING', projectId: db.HANDOFF_PROJECT_ID, projectName: db.project2.name, workflowId: db.WORKFLOW_ID, environmentKey: 'env-test', createdAt: '2026-10-04T09:30:00Z', warningCount: 0, unresolvedWarningCount: 2, requiredApprovals: 2, approvalCount: 1, highOpenQuestionCount: 0 },
    ];
    // Honour the status filter like S1 does: a mock that ignores it hides the gate-vs-stage
    // enum mismatch that made the real inbox empty.
    const statusRows = status ? rows.filter((r) => r.status === status) : rows;
    const mineRows = mine ? statusRows.filter((r) => r.id === 'GT-1') : statusRows;
    const filtered = project ? mineRows.filter((r) => r.projectId === project) : mineRows;
    const limit = Number(url.searchParams.get('limit') ?? '50');
    const offset = Number(url.searchParams.get('offset') ?? '0');
    // `total` stays the whole filtered count while `items` is the page: that difference is the only
    // thing telling the screen "there is more", and a mock that equates them tests nothing.
    return json({ items: filtered.slice(offset, offset + limit), total: filtered.length });
  }),
  http.get(`${P}/gates/:id`, ({ params }) => (params.id === 'GT-1' || params.id === 'GT-2' ? json(db.state.gate1) : envelope('NOT_FOUND', 404))),
  // UI-GATE-001: the page's found/uncertain answers. The mock answers with the same bound objects
  // and warnings the gate row carries, plus one open question, so both sections render real shapes.
  http.get(`${P}/gates/:id/explanation`, ({ params }) =>
    params.id === 'GT-1'
      ? json({
          gateId: 'GT-1',
          gateType: 'REQUIREMENTS_GATE',
          found: { boundObjects: db.state.gate1.boundObjects },
          uncertain: { openQuestions: [{ text: 'Low confidence (0.55) on entity “Facility” — confirm the mapping.' }], warnings: db.state.gate1.warnings },
          willDo: { environmentKey: null },
          why: { design: db.decisions.items ?? [] },
          canChange: [],
          afterApproval: {},
        })
      : envelope('NOT_FOUND', 404),
  ),
  http.post(`${P}/gates/:id/approve`, async ({ request }) => {
    const r = requireOperator(request, false);
    if (r) return r;
    const body = (await request.json().catch(() => ({}))) as { warningDispositions?: unknown[]; seenBoundHashes?: unknown[] };
    if ((db.state.gate1.warnings?.length ?? 0) > (body.warningDispositions?.length ?? 0)) {
      return envelope('WARNING_UNRESOLVED', 409, { details: { gateId: 'GT-1' } });
    }
    return json({ gateId: 'GT-1', status: 'APPROVED', decision: 'APPROVED', operator: request.headers.get('X-Operator'), at: db.state.lastTouched(), seenHashesAccepted: (body.seenBoundHashes?.length ?? 0) });
  }),
  http.post(`${P}/gates/:id/reject`, async ({ request }) => {
    const r = requireOperator(request, false);
    if (r) return r;
    const body = (await request.json().catch(() => ({}))) as { reason?: string };
    if (!body.reason) return envelope('VALIDATION_ERROR', 422, { details: { field: 'reason' } });
    return json({ gateId: 'GT-1', status: 'REJECTED', decision: 'REJECTED', reason: body.reason });
  }),

  // ---- questions ----
  http.get(`${P}/questions/:id`, ({ params }) => {
    const found = db.state.questions.find((q) => q.id === params.id);
    return found ? json(found) : envelope('NOT_FOUND', 404);
  }),
  http.post(`${P}/questions/:id/answer`, async ({ request }) => {
    const r = requireOperator(request, false);
    if (r) return r;
    const b = (await request.json()) as { suggestionId?: string; customText?: string };
    const text = b.customText ?? b.suggestionId ?? '—';
    const updated = { ...db.question1, status: 'ANSWERED', blocking: false, answeredBy: request.headers.get('X-Operator'), answeredAt: db.state.lastTouched(), answer: { text, suggestionId: b.suggestionId ?? null } };
    db.state.questions = db.state.questions.map((q) => (q.id === db.question1.id ? updated : q));
    return json(updated);
  }),
  http.post(`${P}/questions/answers`, async ({ request }) => {
    const r = requireOperator(request, false);
    if (r) return r;
    const body = (await request.json()) as {
      answers: Array<{ questionId: string; suggestionIds?: string[]; customText?: string }>;
    };
    const views = body.answers.map((answer) => {
      const current = db.state.questions.find((q) => q.id === answer.questionId) ?? db.question1;
      const chosen = (current.suggestions ?? []).filter((s) => (answer.suggestionIds ?? []).includes(s.id));
      const text = [...chosen.map((s) => String(s.text ?? '')), answer.customText ?? ''].filter(Boolean).join('; ');
      return {
        ...current,
        status: 'ANSWERED',
        blocking: false,
        answeredBy: request.headers.get('X-Operator'),
        answeredAt: db.state.lastTouched(),
        answer: {
          text,
          suggestionId: chosen[0]?.id ?? null,
          suggestionIds: chosen.map((s) => s.id),
          suggestionTexts: chosen.map((s) => String(s.text ?? '')),
          customText: answer.customText ?? null,
          answeredAt: db.state.lastTouched(),
        },
      };
    });
    db.state.questions = db.state.questions.map((q) => views.find((v) => v.id === q.id) ?? q);
    const open = db.state.questions.filter((q) => q.status === 'OPEN');
    return json({
      answers: views.map((v) => ({ questionId: v.id, status: v.status })),
      resumed: !open.some((q) => q.blocking),
      runId: 'AR-1',
      status: 'WAITING_INPUT',
      questions: open,
    });
  }),

  // ---- notifications ----
  http.get(`${P}/notifications`, ({ request }) => {
    const params = new URL(request.url).searchParams;
    const projectId = params.get('projectId');
    let items = (db.state.notifications.items ?? []).filter((n) => (projectId ? n.projectId === projectId : true));
    // CP-NTF-001 with M4's personal half: `unreadOnly` is a filter, and `mine` keeps the
    // project-level rows plus the ones addressed to this account. The fixture has no recipient
    // field, so the mock keeps every row when `mine` is asked — which is what S1 answers when
    // nothing is addressed to anyone in particular.
    if (params.get('unreadOnly') === 'true') items = items.filter((n) => !n.read);
    const limit = Number(params.get('limit')) || items.length || 1;
    const offset = Number(params.get('offset')) || 0;
    const page = items.slice(offset, offset + limit);
    return json({ ...db.state.notifications, items: page, total: items.length, limit: page.length, offset });
  }),
  http.post(`${P}/notifications/:id/read`, ({ request, params }) => {
    const r = requireOperator(request, false);
    if (r) return r;
    db.state.notifications.items = (db.state.notifications.items ?? []).map((n) => (n.id === params.id ? { ...n, read: true } : n));
    return json({ notificationId: String(params.id), read: true });
  }),

  // ---- environments ----
  http.get(`${P}/environments`, () => json({ items: db.state.environments, total: db.state.environments.length })),
  http.get(`${P}/environments/expiring-credentials`, () => json({ items: [{ environmentKey: 'env-prod', scope: 'import', secretRef: 'vault://dhis2/prod/import', expiresAt: '2026-09-30T00:00:00Z', daysRemaining: 1 }] })),
  http.get(`${P}/environments/:key`, ({ params }) => {
    const found = db.state.environments.find((e) => e.environmentKey === params.key);
    return found ? json(found) : envelope('NOT_FOUND', 404);
  }),
  http.get(`${P}/environments/:key/capability-profile`, ({ params }) => json({ environmentKey: String(params.key), profileVersion: '1', dhis2Version: '2.40.7', stale: false, freshness: 'VALID', content: {} })),
  http.get(`${P}/environments/:key/protection`, ({ params }) => {
    const key = String(params.key);
    const environment = db.state.environments.find((e) => e.environmentKey === key);
    const protection = db.state.protections[key];
    if (!environment || !protection) return envelope('NOT_FOUND', 404);
    return json({
      environmentKey: key,
      environmentClass: environment.environmentClass,
      timezone: protection.timezone,
      commitWindows: protection.commitWindows,
      withinCommitWindow: true,
      emergencyLocked: protection.emergencyLocked,
      lockReason: protection.lockReason,
      lockChangedAt: protection.lockChangedAt,
      lockChangedBy: protection.lockChangedBy,
      deletionPolicy: protection.deletionPolicy,
      requiredApprovals: null,
      operatorRole: null,
      allowsFixtureImport: environment.environmentClass !== 'PRODUCTION',
      dataExportPolicy: {},
      llmDataPolicy: environment.llmDataPolicy,
    });
  }),
  http.post(`${P}/environments/:key/test-connection`, ({ request, params }) => {
    // S1 authorises before it reads the row, so the operator check comes first.
    const refused = requireOperator(request, false) ?? outOfService(params.key);
    if (refused) return refused;
    // The eight DX-CON-001 checks with the names S1 sends, and `state` rather than `passed`: the
    // page prints `check.name` verbatim, so an invented name tests the screen against a shape no
    // real answer has. `SKIPPED` and `message` are what S1 sends when a probe did not run at all,
    // and the page has a third label for them — so the fixture has to reach that branch too.
    const key = String(params.key);
    db.state.connections[key] = db.state.lastTouched();
    return json({
      environmentKey: key,
      ok: true,
      result: 'DEGRADED',
      message: '导入范围缺少 ALL 权限，其余检查通过',
      dhis2Version: '2.40.7',
      requiredScopes: ['discovery', 'import'],
      missingAuthorities: { import: ['ALL'] },
      capabilityProfileArtifactId: 'art-cap-1',
      checks: [
        { name: 'addressReachable', state: 'PASSED' },
        { name: 'tlsValid', state: 'PASSED' },
        { name: 'authentication', state: 'PASSED' },
        { name: 'versionIdentified', state: 'PASSED', detail: '2.40.7 via /api/system/info' },
        { name: 'versionConstraint', state: 'PASSED', detail: 'matches 2.40.x' },
        { name: 'metadataRead', state: 'PASSED', detail: 'scope=discovery' },
        { name: 'metadataImport', state: 'FAILED', detail: '403 on authority M:metadata:add' },
        { name: 'serviceAccountRole', state: 'SKIPPED', detail: '未通过权限检查，跳过' },
      ],
      testedAt: db.state.connections[key],
    });
  }),
  http.post(`${P}/environments/:key/emergency-lock`, async ({ request, params }) => {
    const body = (await request.json().catch(() => ({}))) as { locked?: boolean; reason?: string };
    // S1's EmergencyLockRequest: `locked` has no default, and a lock needs a reason.
    if (typeof body.locked !== 'boolean') return envelope('VALIDATION_ERROR', 422);
    if (body.locked && !(body.reason ?? '').trim()) return envelope('VALIDATION_ERROR', 422);
    const key = String(params.key);
    const protection = db.state.protections[key];
    if (!protection) return envelope('NOT_FOUND', 404);
    // The lock is a column of the protection row. Writing it onto `status` invented a value S1
    // never sends (EnvironmentStatus is ACTIVE or DISABLED) and tested the page against a lie.
    protection.emergencyLocked = body.locked;
    protection.lockReason = body.locked ? (body.reason ?? null) : null;
    protection.lockChangedAt = db.state.lastTouched();
    protection.lockChangedBy = 'Ana';
    return json(
      { environmentKey: key, emergencyLocked: protection.emergencyLocked, lockReason: protection.lockReason, lockChangedAt: protection.lockChangedAt },
      200,
    );
  }),
  http.post(`${P}/environments/:key/disable`, async ({ request, params }) => {
    const denied = requireOperator(request, true);
    if (denied) return denied;
    const found = db.state.environments.find((e) => e.environmentKey === params.key);
    if (!found) return envelope('NOT_FOUND', 404);
    // S4 refuses to disable an already-disabled environment (`rules.can_disable`), and S1 answers
    // the same for both providers -- the shared guard is what keeps these three handlers honest.
    const already = outOfService(params.key);
    if (already) return already;
    found.status = 'DISABLED';
    return json({ environmentKey: String(params.key), status: 'DISABLED' });
  }),
  http.post(`${P}/environments/:key/probe-capabilities`, ({ request, params }) =>
    requireOperator(request, true) ?? outOfService(params.key) ??
      json({ capabilityProfileArtifactId: `art-cap-${String(params.key)}`, version: '2.41.4', profileVersion: '1' }),
  ),
  http.post(`${P}/environments`, async ({ request }) => {
    const denied = requireOperator(request, true);
    if (denied) return denied;
    const body = (await request.json()) as Record<string, unknown>;
    const credentials = (body.credentials as Array<Record<string, unknown>> | undefined) ?? [];
    const scopes = credentials.map((c) => String(c.scope));
    const windows = ((body.protection as Record<string, unknown> | undefined)?.commitWindows as unknown[] | undefined) ?? [];
    // S1's own door checks (S5IF-ENV03): a mock that accepts what the server refuses lets a form
    // bug pass every test and fail the first real save.
    if (!body.environmentKey || !body.baseUrl) return envelope('VALIDATION_ERROR', 422);
    if (/\s/.test(String(body.environmentKey))) return envelope('VALIDATION_ERROR', 422);
    if (!/^https?:\/\//.test(String(body.baseUrl))) return envelope('VALIDATION_ERROR', 422);
    if (!(body.versionConstraint as string | undefined)?.trim()) return envelope('VALIDATION_ERROR', 422);
    if (new Set(scopes).size !== scopes.length) return envelope('VALIDATION_ERROR', 422);
    if (!scopes.includes('discovery') || !scopes.includes('import')) return envelope('VALIDATION_ERROR', 422);
    if (body.environmentClass === 'PRODUCTION') {
      const byScope = Object.fromEntries(credentials.map((c) => [String(c.scope), String(c.secretRef)]));
      if (byScope.discovery === byScope.import) return envelope('VALIDATION_ERROR', 422);
      if (!windows.length) return envelope('VALIDATION_ERROR', 422);
    }
    const created = {
      environmentKey: String(body.environmentKey),
      name: (body.name as string | undefined) ?? String(body.environmentKey),
      baseUrl: String(body.baseUrl),
      environmentClass: (body.environmentClass as string | undefined) ?? 'TEST',
      versionConstraint: (body.versionConstraint as string | undefined) ?? null,
      dhis2Version: null,
      authMode: (body.authMode as string | undefined) ?? 'PAT',
      tlsRequired: Boolean(body.tlsRequired),
      llmDataPolicy: (body.llmDataPolicy as string | undefined) ?? 'PRIVATE_NETWORK',
      timeoutSeconds: Number(body.timeoutSeconds ?? 60),
      status: 'ACTIVE',
      notes: (body.notes as string | undefined) ?? null,
      secretRefs: Object.fromEntries(credentials.map((c) => [String(c.scope), String(c.secretRef)])),
      credentials: credentials.map((c) => ({
        scope: String(c.scope),
        secretRef: String(c.secretRef),
        expiresAt: null,
        label: null,
      })),
      createdAt: db.state.lastTouched(),
      updatedAt: db.state.lastTouched(),
    };
    db.state.environments.push(created as never);
    const protection = (body.protection as Record<string, unknown> | undefined) ?? {};
    db.state.protections[created.environmentKey] = {
      timezone: String(protection.timezone ?? 'UTC'),
      commitWindows: ((protection.commitWindows as db.MockProtection['commitWindows'] | undefined) ?? []).map((w) => ({
        days: w.days,
        start: w.start,
        end: w.end,
      })),
      emergencyLocked: false,
      lockReason: null,
      lockChangedAt: null,
      lockChangedBy: null,
      deletionPolicy: (protection.deletionPolicy as string | null | undefined) ?? null,
    };
    return json(created, 201);
  }),
  http.patch(`${P}/environments/:key`, async ({ request, params }) => {
    const denied = requireOperator(request, true);
    if (denied) return denied;
    const body = (await request.json()) as Record<string, unknown>;
    const found = db.state.environments.find((e) => e.environmentKey === params.key);
    if (!found) return envelope('NOT_FOUND', 404);
    if ('environmentClass' in body) return envelope('VALIDATION_ERROR', 422);
    if ('name' in body && !String(body.name ?? '').trim()) return envelope('VALIDATION_ERROR', 422);
    const protection = body.protection as Record<string, unknown> | undefined;
    if (protection) {
      const stored = db.state.protections[String(params.key)];
      if (stored) {
        stored.timezone = String(protection.timezone ?? stored.timezone);
        stored.commitWindows = ((protection.commitWindows as db.MockProtection['commitWindows'] | undefined) ?? []).map((w) => ({
          days: w.days,
          start: w.start,
          end: w.end,
        }));
        if (protection.deletionPolicy) stored.deletionPolicy = String(protection.deletionPolicy);
      }
    }
    // An absent field means unchanged — the same rule S1's UpdateEnvironmentRequest follows.
    const { protection: _dropped, ...rest } = body;
    Object.assign(found, { ...rest, updatedAt: db.state.lastTouched() } as never);
    return json(found);
  }),

  // ---- governance ----
  // The agent catalogue is what the model form offers a priority for; without this route the whole
  // applicability block renders as nothing, in the suite as well as on screen.
  http.get(`${P}/governance/agents`, () => json(db.agents)),
  http.get(`${P}/governance/models`, () => json(db.models)),
  http.post(`${P}/governance/models`, async ({ request }) => {
    const denied = requireOperator(request, true);
    if (denied) return denied;
    const body = (await request.json()) as Record<string, unknown>;
    // S1's RegisterModelRequest requires `provider` and `model`; the address and key reference are
    // not accepted here because the model row borrows them from its provider.
    if (!body.provider || !body.model) return envelope('VALIDATION_ERROR', 422);
    const provider = db.providers.items.find((p) => p.name === String(body.provider));
    const row = {
      id: `${String(body.provider)}/${String(body.model)}`,
      ref: `${String(body.provider)}/${String(body.model)}`,
      provider: String(body.provider),
      model: String(body.model),
      providerClass: provider?.providerClass ?? 'PRIVATE_NETWORK',
      endpoint: provider?.endpoint ?? null,
      apiKeySecretRef: provider?.apiKeySecretRef ?? null,
      // S1's ModelCapabilities declares both flags with a default and the token count as nullable,
      // so the fixture has to answer that shape rather than echo whatever the body carried — and the
      // two defaults are S3's own: a registration that says nothing about capabilities is a model
      // that answers in a checkable shape over an 8000-token window, not one that fits nothing.
      capabilities: {
        structuredOutput: (body.capabilities as Record<string, unknown> | undefined)?.structuredOutput !== false,
        vision: (body.capabilities as Record<string, unknown> | undefined)?.vision === true,
        maxContextTokens: ((body.capabilities as Record<string, unknown> | undefined)?.maxContextTokens as number | undefined) ?? 8000,
      },
      applicableAgents: (body.applicableAgents ?? {}) as Record<string, number | string>,
      unitPriceInputPer1k: Number(body.unitPriceInputPer1k ?? 0),
      unitPriceOutputPer1k: Number(body.unitPriceOutputPer1k ?? 0),
      enabled: body.enabled !== false,
    };
    const index = db.models.items.findIndex((m) => m.ref === row.ref);
    if (index >= 0) db.models.items.splice(index, 1, row);
    else db.models.items.push(row);
    return json(row, 201);
  }),
  http.patch(`${P}/governance/models/*`, async ({ request }) => {
    const denied = requireOperator(request, true);
    if (denied) return denied;
    const body = (await request.json()) as Record<string, unknown>;
    // S1's PatchModelRequest forbids unknown keys and drops nulls, so a body that names nothing is
    // a 422 rather than a successful no-op. The mock answers the same way, or a test that sends
    // `{}` believes the edit reached the server.
    const named = ['capabilities', 'unitPriceInputPer1k', 'unitPriceOutputPer1k', 'applicableAgents', 'enabled', 'endpoint', 'apiKeySecretRef'];
    const changed = Object.fromEntries(Object.entries(body).filter(([key, value]) => named.includes(key) && value !== null));
    if (!Object.keys(changed).length) return envelope('VALIDATION_ERROR', 422);
    // S1 declares this path with `{ref:path}` because the reference is `provider/model` and carries a
    // slash. The client percent-encodes it and the server decodes it, so the mock reads the tail of
    // the URL rather than a single path segment.
    const path = new URL(request.url).pathname;
    const ref = decodeURIComponent(path.slice(`${P}/governance/models/`.length));
    const found = db.models.items.find((m) => m.ref === ref || m.id === ref);
    if (!found) return envelope('NOT_FOUND', 404);
    // Only the named columns move; `capabilities` merges key by key, which is what lets a price
    // fix leave the stored context window and vision flag exactly as they were.
    const { capabilities, ...rest } = changed;
    Object.assign(found, rest);
    if (capabilities) {
      found.capabilities = { ...found.capabilities, ...(capabilities as Record<string, unknown>) } as typeof found.capabilities;
    }
    return json(found);
  }),
  // S5IF-SEC01…03: the mock accepts a credential value and keeps only its name, so a test cannot
  // accidentally prove something the real server forbids (a value read back).
  http.put(`${P}/secrets/:name`, async ({ request, params }) => {
    const denied = requireOperator(request, true);
    if (denied) return denied;
    const body = (await request.json()) as Record<string, unknown>;
    const name = String(params.name ?? '');
    const material = typeof body.material === 'string' ? body.material : '';
    if (!/^[a-z0-9][a-z0-9._-]{1,63}$/.test(name)) return envelope('VALIDATION_ERROR', 422, { details: { field: 'name' } });
    if (!material.trim()) return envelope('VALIDATION_ERROR', 422, { details: { field: 'material' } });
    return json(db.putSecret(name, request.headers.get('X-Operator') ?? 'anonymous'));
  }),
  http.get(`${P}/secrets`, () => json({ items: db.listSecrets(), total: db.listSecrets().length, generatedAt: new Date().toISOString() })),
  http.delete(`${P}/secrets/:name`, ({ params }) => {
    const name = String(params.name ?? '');
    const referenced = (db.state.environments ?? []).some((row) =>
      JSON.stringify(row).includes(`db://${name}`),
    );
    if (referenced) {
      return envelope('SECRET_IN_USE', 409, { details: { referencedBy: ['environment:referenced'] } });
    }
    return db.dropSecret(name) ? json({ name, deleted: true }) : envelope('NOT_FOUND', 404);
  }),
  http.get(`${P}/governance/provider-catalogue`, () =>
    json({ items: db.providerCatalogue, total: db.providerCatalogue.length }),
  ),
  http.get(`${P}/governance/providers`, () => json({ items: db.providers.items, total: db.providers.items.length })),
  http.post(`${P}/governance/providers`, async ({ request }) => {
    const denied = requireOperator(request, true);
    if (denied) return denied;
    const body = (await request.json()) as Record<string, unknown>;
    // a local server needs no key, so the reference is allowed to be empty
    if (!body.name || !body.providerClass || !body.endpoint) return envelope('VALIDATION_ERROR', 422);
    const row = {
      id: String(body.name),
      name: String(body.name),
      providerClass: String(body.providerClass),
      endpoint: String(body.endpoint),
      adapter: String(body.adapter ?? 'openai_compat'),
      enabled: body.enabled !== false,
      apiKeySecretRef: String(body.apiKeySecretRef ?? ''),
      vendor: String(body.vendor ?? 'custom'),
      suggestedModels: Array.isArray(body.suggestedModels) ? body.suggestedModels.map(String) : [],
    };
    const index = db.providers.items.findIndex((p) => p.name === row.name);
    if (index >= 0) db.providers.items.splice(index, 1, row);
    else db.providers.items.push(row);
    return json(row, 201);
  }),
  http.patch(`${P}/governance/providers/:name`, async ({ request, params }) => {
    const body = (await request.json()) as Record<string, unknown>;
    const found = db.providers.items.find((p) => p.name === params.name);
    // The edit path merges; it never replaces, so a body naming only `endpoint` keeps the rest.
    if (!found) return envelope('NOT_FOUND', 404);
    if (Object.keys(body).length === 0) return envelope('VALIDATION_ERROR', 422);
    Object.assign(found, body);
    return json(found);
  }),
  http.get(`${P}/governance/policy`, () => json(db.policy)),
  http.post(`${P}/governance/policy/relaxations`, async ({ request }) => {
    const denied = requireOperator(request, true);
    if (denied) return denied;
    const body = (await request.json().catch(() => ({}))) as {
      change?: { level?: string; addClasses?: string[] };
      reason?: string;
      scope?: string;
    };
    // S1's own refusals, in the same order, so a form that passes here cannot fail there.
    if (!(body.reason ?? '').trim()) return envelope('VALIDATION_ERROR', 422);
    const level = body.change?.level ?? '';
    const wanted = body.change?.addClasses ?? [];
    if (!level || !wanted.length) return envelope('VALIDATION_ERROR', 422);
    const ceiling = db.policy.ceiling?.allowed ?? {};
    const allowed = db.policy.effective?.allowed ?? {};
    if (level === 'PHI_PII' || wanted.some((value) => !(ceiling[level] ?? []).includes(value))) {
      return envelope('AI_POLICY_RELAXATION_DENIED', 403);
    }
    const next = Array.from(new Set([...(allowed[level] ?? []), ...wanted])).sort();
    if (db.policy.effective?.allowed) db.policy.effective.allowed[level] = next;
    db.policy.isDefault = false;
    return json(
      {
        id: `aplax-${Object.keys(allowed).indexOf(level) + 1}`,
        scope: body.scope ?? 'organization',
        operator: 'Ana',
        reason: body.reason,
        change: { level, addClasses: wanted },
        roleChecked: false,
        roleGate: 'operator-name (MVP)',
        createdAt: db.state.lastTouched(),
      },
      201,
    );
  }),
  http.get(`${P}/governance/usage`, () => json(db.usage)),
  http.get(`${P}/governance/prompts`, () =>
    json({
      items: [
        { id: 'pr-1', name: 'mapping.extract', version: 4, agentType: 'MAPPING', qualifiedName: 'mapping.extract@4', active: false },
        { id: 'pr-2', name: 'domain.model', version: 2, agentType: 'DOMAIN', qualifiedName: 'domain.model@2', active: true },
        { id: 'pr-3', name: 'build.compile', version: 1, agentType: 'BUILD', qualifiedName: 'build.compile@1', active: false },
      ],
      total: 3,
    }),
  ),
  http.get(`${P}/governance/evaluations`, () =>
    json({
      items: [
        { id: 'ev-1', prompt: 'mapping.extract.v4', passed: true, caseCount: 12 },
        { id: 'ev-2', prompt: 'build.compile.v1', passed: false, caseCount: 4 },
      ],
      total: 2,
    }),
  ),
  http.get(`${P}/governance/regression-records`, ({ request }) => {
    const url = new URL(request.url);
    const all = [
      { id: 'reg-1', promptVersionId: 'mapping.extract.v4', passed: true },
      { id: 'reg-2', promptVersionId: 'domain.model.v2', passed: true },
    ];
    const name = url.searchParams.get('promptName');
    const version = url.searchParams.get('promptVersion');
    const items = name && version ? all.filter((r) => r.promptVersionId === `${name}.v${version}`) : all;
    return json({ items, total: items.length });
  }),
  http.post(`${P}/governance/prompts/activations`, async ({ request }) => {
    const denied = requireOperator(request, true);
    if (denied) return denied;
    const body = (await request.json()) as Record<string, unknown>;
    if (!body.promptName || !body.agentType || !(body.modelList as string[] | undefined)?.length) {
      return envelope('VALIDATION_ERROR', 422);
    }
    return json({ agentType: body.agentType, promptVersionId: `${String(body.promptName)}.v${String(body.promptVersion)}` });
  }),

  // ---- settings ----
  http.get(`${P}/settings`, () => json(db.systemSettings)),
  http.put(`${P}/settings`, async ({ request }) => {
    const r = requireOperator(request, false);
    if (r) return r;
    return json(await request.json());
  }),

  // ---- blueprint / registry / inputs (project-scoped) ----
  http.get(`${P}/projects/:id/blueprint/draft`, () => json(db.state.draft)),
  http.get(`${P}/projects/:id/blueprint/section-schemas`, () => json(db.sectionSchemaIndex)),
  http.get(`${P}/projects/:id/blueprint/section-schemas/:section`, () => json(db.requirementsSectionSchema)),
  http.get(`${P}/projects/:id/registry`, () => json(db.registry)),
  // S5IF-BP13: the decision list answers its own filter, so a test can tell "the page asked for one
  // decision" apart from "the page read everything and showed the first card".
  http.get(`${P}/projects/:id/decisions`, ({ request }) => {
    const wanted = new URL(request.url).searchParams.getAll('decisionIds').flatMap((value) => value.split(','));
    const items = wanted.length ? db.decisions.items?.filter((d) => wanted.includes(d.decisionId)) : db.decisions.items;
    return json({ items: items ?? [], total: (items ?? []).length });
  }),
  // S5IF-BP17: only the design project has a build record; the other one answers "nothing
  // compiled yet", which is the branch the by-file view has to show honestly.
  http.get(`${P}/projects/:id/package-files`, ({ params }) =>
    params.id === db.DESIGN_PROJECT_ID
      ? json(db.packageFiles)
      : json({ projectId: String(params.id), version: null, buildId: '', kind: 'NONE', environmentKey: null, packageHash: null, files: [], total: 0 }),
  ),
  // CP-UI-021: only the mode-B project has a development package. The other answers 404 with
  // S1's NOT_GENERATED shape (CP-UI-022), which is the arm the page's guidance is built for — and
  // it is the true answer, because a mode-A project skips the HANDOFF stage that produces it.
  http.get(`${P}/projects/:id/frontend-package/files`, ({ params }) =>
    params.id === db.HANDOFF_PROJECT_ID
      ? json(db.frontendPackage)
      : json({ errorCode: 'NOT_FOUND', message: 'no development package yet', details: { reason: 'NOT_GENERATED', requiredStage: 'HANDOFF' } }, 404),
  ),
  http.get(`${P}/projects/:id/frontend-package/files/*`, ({ params }) => {
    const path = decodeURIComponent(String(params[0] ?? ''));
    const text = db.frontendPackageFiles[path];
    if (text === undefined) {
      return json({ errorCode: 'NOT_FOUND', message: 'not in the listing', details: { reason: 'NOT_EXIST' } }, 404);
    }
    return new HttpResponse(text, { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
  }),
  // S5IF-BP16: narrowed the way S1 narrows, so a filter tested here behaves like the real one —
  // an edge survives when either end matches, and both of its ends stay in the answer.
  http.get(`${P}/workflows/:id/object-graph`, ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const types = sp.getAll('dhis2Type');
    const keys = sp.getAll('businessKey');
    const runId = sp.get('runId');
    const full = db.objectGraph.nodes;
    // orphans are computed on the complete graph, before any filter narrows what is returned
    const orphans = db.objectGraph.orphans;
    let nodes = full.map((node) => ({ ...node }));
    let edges = db.objectGraph.edges.map((edge) => ({ ...edge }));

    // S2 keeps the matched nodes plus their one-hop edges, so a crossing reference still renders
    // both of its ends (BP-TD-009)
    if (types.length > 0 || keys.length > 0) {
      const matched = nodes.filter(
        (n) => (types.length === 0 || types.includes(String(n.dhis2Type)))
          && (keys.length === 0 || keys.includes(String(n.businessKey))),
      );
      const keep = new Set(matched.map((n) => String(n.businessKey)));
      for (const edge of edges) {
        if (keep.has(String(edge.fromBusinessKey))) keep.add(String(edge.toBusinessKey));
        if (keep.has(String(edge.toBusinessKey))) keep.add(String(edge.fromBusinessKey));
      }
      nodes = nodes.filter((n) => keep.has(String(n.businessKey)));
      edges = edges.filter((e) => keep.has(String(e.fromBusinessKey)) && keep.has(String(e.toBusinessKey)));
    }

    return json({
      ...db.objectGraph,
      kind: runId ? 'ENVIRONMENT' : db.objectGraph.kind,
      runId,
      environmentKey: runId ? 'dev' : null,
      blueprintVersion: sp.get('blueprintVersion') ?? db.objectGraph.blueprintVersion,
      nodes,
      edges,
      orphans,
      filters: { dhis2Types: types, businessKeys: keys },
    });
  }),
  http.get(`${P}/projects/:id/inputs`, () => json(db.inputs)),

  // ---- business inputs (UI-REQS-001..003) ----
  http.get(`${P}/upload-limits`, () => json(db.uploadLimits)),
  http.post(`${P}/projects/:id/inputs`, async ({ request }) => {
    const operator = requireOperator(request, false);
    if (operator) return operator;
    // FR-REQ-001 mirrors S1: no declaration, no artifact — S1 records what the uploader said,
    // never a level it picked for them.
    // jsdom's XHR posts FormData as text/plain, so the mock cannot always parse the fields;
    // S1's own suite covers the enforcement, and the app-level assertions here are about gating.
    let form: FormData | null = null;
    try {
      form = await request.formData();
    } catch {
      form = null;
    }
    const declared = form ? String(form.get('dataClassification') ?? '') : 'INTERNAL';
    if (!declared) return envelope('VALIDATION_ERROR', 422, { details: { fields: [{ path: 'dataClassification', message: 'required' }] } });
    if (declared === 'PHI_PII' && form && String(form.get('phiDeclared') ?? '') !== 'true') {
      return envelope('VALIDATION_ERROR', 422, { details: { fields: [{ path: 'phiDeclared', message: 'PHI/PII must be declared' }] } });
    }
    // A multipart field is a string or a File; the mock only needs its two display fields.
    const entry = (form?.get('file') ?? null) as { name?: string; size?: number } | string | null;
    const name = typeof entry === 'string' || entry === null ? 'input' : String(entry.name ?? 'input');
    const size = typeof entry === 'string' || entry === null ? 0 : Number(entry.size ?? 0);
    if (size > db.uploadLimits.maxUploadBytes) {
      return envelope('UPLOAD_REJECTED', 400, { details: { reason: 'SIZE', violation: 'SIZE_EXCEEDED', maxBytes: db.uploadLimits.maxUploadBytes } });
    }
    return json(
      {
        artifactId: `INP-${Date.now() % 1000}`,
        filename: name,
        mediaType: String(form?.get('mediaType') ?? 'application/octet-stream'),
        sizeBytes: size,
        contentHash: 'sha256:mock',
        dataClassification: declared,
        parseStatus: null,
        createdAt: db.state.lastTouched(),
      },
      201,
    );
  }),
  http.post(`${P}/projects/:id/inputs/:artifactId/parse`, ({ request }) =>
    requireOperator(request) ?? json({ stageId: 'ST-req', stepRunId: 'STEP-1', accepted: true }, 202),
  ),
  http.get(`${P}/projects/:id/inputs/:artifactId/column-mapping`, () => json(db.columnMapping)),
  http.get(`${P}/projects/:id/inputs/:artifactId/llm-log`, () => json(db.llmLog)),
  http.get(`${P}/projects/:id/inputs/:artifactId/llm-log/:exchangeId`, () => json(db.llmExchange)),
  http.get(`${P}/workflows/:id/nrr`, ({ request }) => {
    const category = new URL(request.url).searchParams.get('category');
    const items = category ? db.nrr.items.filter((item) => item.category === category) : db.nrr.items;
    return json({ ...db.nrr, items, total: items.length });
  }),
  http.post(`${P}/artifacts/:artifactId/explain`, ({ request }) =>
    requireOperator(request, false) ?? json({ explanationArtifactId: 'ART-EXPLAIN-1' }, 200),
  ),
  http.get(`${P}/workflows/:id/agent-runs/:runId`, ({ params }) =>
    json({ runId: String(params.runId), workflowId: db.WORKFLOW_ID, status: 'RUNNING', summary: null, questions: [], issues: [], progress: { step: 'mapping', completedFraction: 0.4 } }),
  ),
  http.post(`${P}/workflows/:id/agent-runs/:runId/cancel`, ({ request, params }) =>
    requireOperator(request, false) ?? json({ runId: String(params.runId), status: 'CANCELLED' }),
  ),

  // ---- deployment runs ----
  http.get(`${P}/workflows/:id/deployment-runs`, () => json({ items: db.state.runs, total: db.state.runs.length })),
  http.get(`${P}/deployment-runs`, () => json({ items: db.state.runs, total: db.state.runs.length })),
  http.get(`${P}/deployment-runs/:id`, ({ params }) => json(db.state.runs.find((r) => r.id === params.id) ?? DEFAULT_RUN)),
  // UI-DEP-005: the committed run answers with the documents its execution produced. Listed
  // before the parameterised summary because MSW takes the first match, and `:id` matches RUN-9.
  http.get(`${P}/deployment-runs/RUN-9/summary`, () =>
    json({ runId: 'RUN-9', status: 'COMPLETED', environmentKey: 'env-dev', environmentClass: 'TEST', blueprintVersion: 'v7', outdated: false, dhis2Version: '2.41.0', profileVersion: '1', connection: { ok: true }, validation: { staticOk: true, dhis2Ok: true }, approval: { packageGate: 'APPROVED', deployGate: 'APPROVED', approvals: 2, requiredApprovals: 2 }, payload: { objectCount: 12 }, changeStats: { create: 5, update: 4, delete: 1 }, maintenanceActions: [], execution: { executionId: 'EX-9', status: 'SUCCESS' }, verification: { consistent: false, lastVerifiedAt: '2026-03-04T09:20:00Z', postVerifyArtifactId: 'ART-PV-1' }, snapshot: { status: 'TAKEN', artifactId: 'ART-SNP-1' }, diffReportArtifactId: 'ART-DIFF-1' }),
  ),
  http.get(`${P}/deployment-runs/:id/summary`, ({ params }) =>
    // the status comes from the mutable row because a cancel (below) is expected to change what
    // the console shows; every other field is the fixture's own.
    json({ runId: 'RUN-1', status: db.state.runs.find((r) => r.id === params.id)?.status ?? 'VALIDATED', environmentKey: 'env-dev', environmentClass: 'TEST', blueprintVersion: 'v7', outdated: false, dhis2Version: '2.40.7', profileVersion: '1', connection: { ok: true }, validation: { staticOk: true, dhis2Ok: true }, approval: { packageGate: 'APPROVED', deployGate: 'WAITING_APPROVAL', approvals: 1, requiredApprovals: 2 }, payload: { objectCount: 12 }, changeStats: { create: 5, update: 4, delete: 1 }, maintenanceActions: [], execution: {}, verification: {}, snapshot: {} }),
  ),
  http.post(`${P}/deployment-runs/:id/cancel`, ({ request, params }) => {
    const denied = requireOperator(request);
    if (denied) return denied;
    const run = db.state.runs.find((r) => r.id === params.id);
    if (run) run.status = 'CANCELLED';
    return json({ deploymentRunId: String(params.id), status: 'CANCELLED' });
  }),
  http.get(`${P}/executions/:executionId`, ({ params }) =>
    json({
      executionId: String(params.executionId),
      status: 'SUCCESS',
      applied: true,
      payloadHash: 'sha256:payload-1',
      reportArtifactId: 'ART-EXR-1',
      importReportArtifactId: 'ART-IMP-1',
      steps: [{ name: 'commit', status: 'DONE' }],
      issues: [
        {
          businessKey: 'dataElement.weight',
          description: 'name collided with an existing element; merged',
          level: 'WARNING',
          suggestedAction: 'rename the element in the blueprint',
        },
      ],
    }),
  ),
  http.get(`${P}/executions/:executionId/report`, ({ params }) =>
    json({ executionId: String(params.executionId), reportArtifactId: 'ART-EXR-1', importReportArtifactId: 'ART-IMP-1' }),
  ),
  http.post(`${P}/workflows/:id/deployment-runs`, ({ request }) =>
    requireOperator(request) ?? json({ id: 'RUN-2', status: 'CREATED', environmentKey: 'env-dev', workflowId: db.WORKFLOW_ID }, 201),
  ),
  http.post(`${P}/deployment-runs/:id/commit`, ({ request }) =>
    requireOperator(request) ?? json({ runId: 'RUN-1', status: 'COMMITTING', executionId: 'EX-9', acceptedAt: db.state.lastTouched() }, 202),
  ),

  // ---- environment bindings (UI-DEP-003, S5IF-BP09/10/11) ----
  http.get(`${P}/deployment-runs/:id/environment-bindings`, () =>
    json({ items: db.state.bindings, total: db.state.bindings.length }),
  ),
  http.get(`${P}/deployment-runs/:id/environment-bindings/:businessKey/candidates`, ({ params, request }) => {
    const query = new URL(request.url).searchParams.get('query')?.trim().toLowerCase() ?? '';
    const items = db.state.candidateObjects
      .map((c) => ({ ...c, matching: query.length > 0 && `${c.code} ${c.name}`.toLowerCase().includes(query) }))
      .filter((c) => query.length === 0 || c.matching);
    return json({
      items,
      total: items.length,
      discoveryArtifactId: 'DISC-1',
      environmentKey: 'env-dev',
      businessKey: String(params.businessKey),
      dhis2Type: 'categoryOptionGroupSet',
    });
  }),
  http.put(`${P}/deployment-runs/:id/environment-bindings/:businessKey`, async ({ request, params }) => {
    const denied = requireOperator(request, false);
    if (denied) return denied;
    const body = (await request.json().catch(() => ({}))) as { uid?: string; note?: string };
    const uid = (body.uid ?? '').trim();
    const note = (body.note ?? '').trim();
    // S1 checks both before calling S2, which refuses a binding without a note (BP-REG-010)
    if (!note) {
      return json({ error: { code: 'VALIDATION_FAILED', message: 'note is required when setting an environment binding' } }, 422);
    }
    if (!uid) {
      return json({ error: { code: 'VALIDATION_FAILED', message: 'uid is required when setting an environment binding' } }, 422);
    }
    const row = db.state.bindings.find((b) => b.businessKey === params.businessKey);
    if (!row) return json({ error: { code: 'NOT_FOUND', message: 'unknown business key' } }, 404);
    row.uid = uid;
    row.resolution = 'MANUAL';
    row.note = note;
    row.resolvedBy = request.headers.get('X-Operator');
    return json({ ...row });
  }),

  // ---- change requests (UI-OPS-001) ----
  http.get(`${P}/projects/:id/change-requests`, () => json({ items: db.changeRequests, total: db.changeRequests.length })),
  http.post(`${P}/projects/:id/change-requests`, async ({ request }) => {
    const denied = requireOperator(request, false);
    if (denied) return denied;
    const body = (await request.json()) as {
      reason?: string;
      affectedBusinessKeys?: string[];
      deletionCandidates?: string[];
    };
    return json(
      {
        id: 'CR-9',
        projectId: db.DESIGN_PROJECT_ID,
        status: 'DRAFT',
        reason: body.reason,
        affectedBusinessKeys: body.affectedBusinessKeys ?? [],
        deletionCandidates: body.deletionCandidates ?? [],
        requestedBy: 'Ana',
      },
      201,
    );
  }),
  http.post(`${P}/change-requests/:crId/impact`, () =>
    json({
      changeRequestId: 'CR-1',
      affectedStages: ['MAPPING', 'PAGE_DESIGN'],
      affectedObjectKeys: ['entity:facility'],
      affectedPages: ['page:daily-tally'],
      deletionCandidates: [],
    }),
  ),
  http.post(`${P}/change-requests/:crId/approve`, ({ request, params }) =>
    requireOperator(request) ??
    json({ id: String(params.crId), projectId: db.DESIGN_PROJECT_ID, status: 'APPROVED', reason: 'Add birth-weight aggregation', approvedBy: 'Ana' }),
  ),
  http.post(`${P}/change-requests/:crId/:step`, ({ request, params }) =>
    requireOperator(request) ?? json({ id: String(params.crId), projectId: db.DESIGN_PROJECT_ID, status: 'IN_PROGRESS', reason: 'x' }),
  ),

  // ---- audit / change requests ----
  http.get(`${P}/audit`, ({ request }) => json(auditPage(request, db.auditRecords))),
  http.get(`${P}/projects/:id/audit`, ({ request }) => json(auditPage(request, db.auditRecords))),
  http.get(`${P}/audit/commit-trace/:executionId`, ({ params }) =>
    String(params.executionId) === 'EX-9'
      ? json({ ...db.commitTrace, executionId: String(params.executionId) })
      : envelope('NOT_FOUND', 404, { details: { executionId: String(params.executionId) } }),
  ),

  // ---- artifact review, validation issues, metadata review (UI-REV-001/003/005) ----
  http.get(`${P}/workflows/:id/artifacts`, ({ request }) => {
    const sp = new URL(request.url).searchParams;
    const type = sp.get('type');
    const all = type ? db.artifacts.filter((a) => a.type === type) : db.artifacts;
    // S5IF-G04: `type` narrows server-side and the cursor walks what is left.
    const limit = Number(sp.get('limit')) || all.length || 1;
    const start = Number(sp.get('cursor')) || 0;
    const items = all.slice(start, start + limit);
    const next = start + items.length < all.length ? String(start + items.length) : null;
    return json({ items, total: all.length, limit: items.length, offset: start, nextCursor: next });
  }),
  http.get(`${P}/artifacts/:artifactId`, ({ params }) => json(db.artifacts.find((a) => a.id === params.artifactId) ?? db.artifacts[0])),
  http.get(`${P}/artifacts/:artifactId/content`, ({ params }) => {
    const record = db.artifacts.find((a) => a.id === params.artifactId);
    if (record?.contentPurged) return envelope('ARTIFACT_PURGED', 410);
    const body = db.artifactContents[String(params.artifactId)] ?? db.artifactContent;
    return new HttpResponse(body, {
      status: 200,
      headers: { 'content-type': 'application/octet-stream', 'x-data-classification': record?.dataClassification ?? 'INTERNAL' },
    });
  }),
  http.get(`${P}/artifacts/:artifactId/lineage`, () => json(db.lineage)),
  http.get(`${P}/artifacts/:artifactId/diff`, () => json(db.artifactDiff)),
  http.post(`${P}/artifacts/:artifactId/reproduce`, () => json({ hash: 'sha256:pkg3', matches: true })),
  http.get(`${P}/workflows/:id/validation-issues`, ({ request }) => {
    const params = new URL(request.url).searchParams;
    const level = params.get('level');
    const rule = params.get('ruleId');
    const key = params.get('businessKey');
    const items = db.issues.filter((issue) => {
      if (level && issue.level !== level) return false;
      if (rule && issue.ruleId !== rule) return false;
      if (key && issue.businessKey !== key) return false;
      return true;
    });
    return json({ items, total: items.length });
  }),
  http.get(`${P}/projects/:id/blueprint/versions`, () => json({ items: db.blueprintVersions, total: db.blueprintVersions.length })),
  http.get(`${P}/projects/:id/blueprint/diff`, () => json(db.blueprintDiff)),
  http.get(`${P}/projects/:id/blueprint/entries`, () => json({ projectId: db.DESIGN_PROJECT_ID, entries: [], notFound: [] })),

  // ---- M4 administration (CP-IAM-002, UI-AUTH-003) ----
  http.get(`${P}/roles`, () => json({ items: db.roles, total: db.roles.length })),
  // S5IF-G04: paged the way S1 pages, so a screen that asks for 50 cannot be green against a
  // fixture that ignores the question.
  http.get(`${P}/users`, ({ request }) => {
    const params = new URL(request.url).searchParams;
    const limit = Number(params.get('limit')) || db.users.length || 1;
    const offset = Number(params.get('offset')) || 0;
    const page = db.users.slice(offset, offset + limit);
    return json({ items: page, total: db.users.length, limit: page.length, offset, nextCursor: null });
  }),
  http.post(`${P}/users`, async ({ request }) => {
    const missing = requireOperator(request);
    if (missing) return missing;
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const created = {
      id: `USR-${String(body.subject ?? '').split('@')[0]}`,
      subject: body.subject, source: 'local', displayName: body.displayName ?? '', email: body.email ?? '',
      status: 'ACTIVE', mustChangePassword: Boolean(body.mustChangePassword), locked: false,
      lastLoginAt: null, createdAt: new Date().toISOString(),
    };
    db.users.push(created);
    return HttpResponse.json(created, { status: 201 });
  }),
  http.patch(`${P}/users/:userId`, async ({ request, params }) => {
    const missing = requireOperator(request, false);
    if (missing) return missing;
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    db.users.splice(0, db.users.length, ...db.users.map((row) => (row.id === params.userId ? { ...row, ...body } : row)));
    return json(db.users.find((row) => row.id === params.userId) ?? {});
  }),
  http.post(`${P}/users/:userId/password`, ({ request, params }) => {
    const missing = requireOperator(request, false);
    if (missing) return missing;
    db.users.splice(0, db.users.length, ...db.users.map((row) => (row.id === params.userId ? { ...row, mustChangePassword: true } : row)));
    return json({ cleared: true });
  }),
  http.put(`${P}/users/:userId/roles`, async ({ request, params }) => {
    const missing = requireOperator(request);
    if (missing) return missing;
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const assignment = {
      userId: String(params.userId),
      role: String(body.role ?? ''),
      projectId: (body.projectId as string | undefined) ?? null,
      revoked: false,
    };
    const row = db.users.find((u) => u.id === params.userId);
    if (row) row.roles = [...(heldRoles(row)), assignment];
    return json(assignment, 201);
  }),
  http.delete(`${P}/users/:userId/roles`, ({ request, params }) => {
    const missing = requireOperator(request, false);
    if (missing) return missing;
    const wanted = new URL(request.url).searchParams;
    const role = wanted.get('role') ?? '';
    const projectId = wanted.get('projectId');
    const row = db.users.find((u) => u.id === params.userId);
    // S1 deletes the matching rows and reports the revocation either way (identity/service.py),
    // so the list has to lose the role here too -- otherwise the screen shows a revoke that worked
    // and the row still carries it on the next read.
    if (row) {
      row.roles = heldRoles(row).filter(
        (a) => !(String(a.role) === role && ((a.projectId as string | null) ?? null) === projectId),
      );
    }
    return json({ userId: String(params.userId), role, projectId, revoked: true });
  }),
  http.get(`${P}/projects/:id/members`, () => json({ items: db.members, total: db.members.length })),
  http.put(`${P}/projects/:id/members`, async ({ request }) => {
    const missing = requireOperator(request);
    if (missing) return missing;
    const body = (await request.json().catch(() => ({}))) as { members?: Record<string, unknown>[] };
    db.members.splice(0, db.members.length, ...(body.members ?? []));
    return json({ items: db.members, total: db.members.length });
  }),
  http.delete(`${P}/projects/:id/members/:userId`, ({ request, params }) => {
    const missing = requireOperator(request, false);
    if (missing) return missing;
    db.members.splice(0, db.members.length, ...db.members.filter((row) => row.userId !== params.userId));
    return json({ cleared: true });
  }),
  http.get(`${P}/projects/:id/artifact-grants`, () => json({ items: db.grants, total: db.grants.length })),
  http.post(`${P}/projects/:id/artifact-grants`, async ({ request }) => {
    const missing = requireOperator(request);
    if (missing) return missing;
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const row = {
      id: `GR-${Math.random().toString(36).slice(2, 8)}`, projectId: db.DESIGN_PROJECT_ID,
      userId: body.userId, subject: String(body.userId ?? ''), classification: body.classification,
      reason: body.reason, grantedBy: 'ada@example.org', expiresAt: body.expiresAt ?? null, revoked: false,
    };
    db.grants.push(row);
    return HttpResponse.json(row, { status: 201 });
  }),
  http.delete(`${P}/projects/:id/artifact-grants/:userId`, ({ request, params }) => {
    const missing = requireOperator(request, false);
    if (missing) return missing;
    const classification = new URL(request.url).searchParams.get('classification');
    db.grants.splice(0, db.grants.length, ...db.grants.filter((row) => !(row.userId === params.userId && row.classification === classification)));
    return json({ cleared: true });
  }),
  http.get(`${P}/projects/:id/production-exemptions`, () => json({ items: db.exemptions, total: db.exemptions.length })),
  http.post(`${P}/projects/:id/production-exemptions`, async ({ request }) => {
    const missing = requireOperator(request);
    if (missing) return missing;
    const body = (await request.json().catch(() => ({}))) as Record<string, unknown>;
    const row = {
      id: `EX-${Math.random().toString(36).slice(2, 8)}`, projectId: db.DESIGN_PROJECT_ID,
      waiver: body.waiver ?? null, environmentKey: body.environmentKey ?? null, reason: body.reason,
      grantedBy: 'ada@example.org', expiresAt: body.expiresAt ?? null, revoked: false,
    };
    db.exemptions.push(row);
    return HttpResponse.json(row, { status: 201 });
  }),
  http.delete(`${P}/production-exemptions/:exemptionId`, ({ request, params }) => {
    const missing = requireOperator(request, false);
    if (missing) return missing;
    db.exemptions.splice(0, db.exemptions.length, ...db.exemptions.map((row) => (row.id === params.exemptionId ? { ...row, revoked: true } : row)));
    return json({ cleared: true });
  }),

  // ---- M4 session (UI-AUTH-001/002) ----
  // `authMode: off` unless VITE_AUTH_MODE says otherwise, so the whole existing suite keeps running
  // against the M1/M2 regime it was written for (CP-IAM-001: the switch is inert, not implied).
  http.get(`${P}/auth/session`, () => json({ ...db.auth.session, authMode: db.auth.mode })),
  http.post(`${P}/auth/login`, async ({ request }) => {
    if (!request.headers.get('X-Operator')) return envelope('OPERATOR_REQUIRED', 400);
    const body = (await request.json().catch(() => ({}))) as { subject?: string; password?: string };
    if (!body.subject || !db.auth.signIn(body.subject, body.password ?? '')) {
      // a wrong password and an unknown account answer the same way (FR-SEC-008)
      return envelope('UNAUTHENTICATED', 401, { message: 'the credentials do not match an account' });
    }
    return json({ ...db.auth.session, authMode: db.auth.mode });
  }),
  http.post(`${P}/auth/logout`, ({ request }) => {
    if (!request.headers.get('X-Operator')) return envelope('OPERATOR_REQUIRED', 400);
    db.auth.signOut();
    return json({ cleared: true, authMode: db.auth.mode });
  }),
  // the mock has no identity provider to redirect to
  http.get(`${P}/auth/oidc/request`, () => envelope('PROVIDER_UNAVAILABLE', 503)),

  // ---- AI-COST-001/002/003: usage rollup and the budget switch (UI-ADM-005) ----
  http.get(`${P}/governance/usage`, ({ request }) => {
    const groupBy = new URL(request.url).searchParams.getAll('groupBy');
    return json({
      groupBy: groupBy.length ? groupBy : db.usage.groupBy,
      dimensions: db.usageDimensions,
      items: db.usage.items,
    });
  }),
  http.get(`${P}/governance/budget`, ({ request }) => {
    const projectId = new URL(request.url).searchParams.get('projectId') ?? '';
    return json(db.budgets[projectId] ?? db.emptyBudget(projectId));
  }),
  http.put(`${P}/governance/budget`, async ({ request }) => {
    // X-Operator is mandatory on a write (CP-API-002); the Idempotency-Key is offered, not required.
    const missing = requireOperator(request, false);
    if (missing) return missing;
    const projectId = new URL(request.url).searchParams.get('projectId') ?? '';
    const body = (await request.json().catch(() => ({}))) as {
      limit?: number | null;
      currency?: string;
      thresholds?: number[];
    };
    const spent = db.usage.items.reduce((sum, item) => sum + (item.estimatedCost ?? 0), 0);
    const limit = body.limit ?? null;
    const thresholds = body.thresholds ?? [0.8];
    const ratio = limit ? spent / limit : 0;
    // the verdict is computed here, on the "server", so the screen has nothing to compare (AI-COST-003)
    const state =
      limit === null ? 'UNSET' : ratio >= 1 ? 'EXCEEDED' : thresholds.some((value) => ratio >= value) ? 'WARNING' : 'OK';
    const row = {
      projectId,
      state,
      spent,
      limit,
      currency: body.currency ?? 'USD',
      thresholdReached: limit === null ? null : Math.max(...thresholds),
    };
    db.budgets[projectId] = row;
    return json(row);
  }),

  // ---- AI-CHAT-001~003: the stage's conversation (UI-WB-010) ----
  http.get(`${P}/stages/:stageId/chat`, ({ params }) =>
    json({
      stageId: String(params.stageId),
      context: stageChatContext(String(params.stageId)),
      turns: db.chatTranscripts[String(params.stageId)] ?? [],
    }),
  ),
  http.post(`${P}/stages/:stageId/chat`, async ({ request, params }) => {
    const missing = requireOperator(request);
    if (missing) return missing;
    const stageId = String(params.stageId);
    const body = (await request.json().catch(() => ({}))) as { message?: string };
    const turns = db.chatTranscripts[stageId] ?? [];
    const answer = `Noted for ${stageId}: ${(body.message ?? '').slice(0, 60)}`;
    db.chatTranscripts[stageId] = [
      ...turns,
      { role: 'user', content: body.message ?? '' },
      { role: 'assistant', content: answer },
    ];
    return json({
      stageId,
      context: stageChatContext(stageId),
      answer,
      patchIds: [],
      questions: [
        {
          localId: 'Q-1',
          text: 'Should the header row be treated as data?',
          severity: 'WARNING',
          affects: ['ward-list'],
          suggestions: [],
          blocking: false,
        },
      ],
    });
  }),
  http.delete(`${P}/stages/:stageId/chat`, ({ request, params }) => {
    const missing = requireOperator(request, false);
    if (missing) return missing;
    delete db.chatTranscripts[String(params.stageId)];
    return json({ cleared: true, stageId: String(params.stageId) });
  }),

  // ---- fallback under /api/v1 ----
  http.all(`${P}/:rest*`, ({ request }) => {
    const url = new URL(request.url);
    if (url.pathname.includes('/conversations')) return envelope('AI_FEATURE_NOT_ENABLED', 501);
    return envelope('NOT_FOUND', 404, { details: { route: url.pathname } });
  }),
];
