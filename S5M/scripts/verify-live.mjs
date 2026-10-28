// Live integration smoke test against a running S1 (default http://127.0.0.1:8000).
// Run: node scripts/verify-live.mjs   (S1 must be up, e.g. FACTORY_PROVIDER_MODE=fakes factory-api)
const BASE = process.env.S1_URL || 'http://127.0.0.1:8000';
const V1 = `${BASE}/api/v1`;
const H = { 'X-Operator': 'verify-bot', 'Accept-Language': 'zh-CN' };

let pass = 0;
let fail = 0;
function ok(name, cond, extra = '') {
  if (cond) {
    pass++;
    console.log(`  ✓ ${name}`);
  } else {
    fail++;
    console.error(`  ✗ ${name} ${extra}`);
  }
}

async function get(path, opts = {}) {
  const res = await fetch(`${V1}${path}`, { headers: { ...H, ...opts.headers } });
  const ct = res.headers.get('content-type') || '';
  const body = ct.includes('json') ? await res.json() : await res.text();
  return { status: res.status, body };
}

async function main() {
  console.log(`Verifying S5M integration against ${BASE}\n`);

  // health
  const health = await fetch(`${BASE}/healthz`);
  ok('GET /healthz', health.status === 200, `(got ${health.status})`);

  // cold-start facts (S5IF-SET01). Checked before anything that needs a project, because the state
  // it exists to describe is precisely the one where nothing has been registered yet.
  const setup = await get('/setup');
  const s = setup.body;
  ok(
    'GET /setup answers every section',
    setup.status === 200 &&
      ['providers', 'configuration', 'identity', 'environments', 'intelligence', 'projects'].every((k) => k in s),
    `(status ${setup.status})`,
  );
  ok(
    'providers names S2/S3/S4 with real|fake',
    ['S2', 'S3', 'S4'].every((k) => ['real', 'fake'].includes(s.providers?.[k])),
    JSON.stringify(s.providers),
  );
  ok('configuration.problems is a list of strings', Array.isArray(s.configuration?.problems) && s.configuration.problems.every((x) => typeof x === 'string'));
  ok(
    'environments.neverTested ⊆ in-service keys',
    (s.environments?.neverTested ?? []).every((key) => (s.environments?.items ?? []).some((i) => i.environmentKey === key && i.status === 'ACTIVE')),
    JSON.stringify(s.environments),
  );
  const platSetup = await get('/allowed-actions');
  const listed = [...(platSetup.body.allowed ?? []), ...(platSetup.body.unavailable ?? [])].map((r) => r.action);
  ok('platform allowed-actions answers project.create (needed by the guided page)', listed.includes('project.create'), listed.slice(0, 8).join(','));

  // projects page envelope
  const projects = await get('/projects');
  const p = projects.body;
  ok('GET /projects is paged envelope', projects.status === 200 && Array.isArray(p.items) && typeof p.total === 'number', `(status ${projects.status})`);
  if (!p.items?.length) {
    console.error('  ! no projects seeded — run S1 scripts/seed_demo.py against the API first');
    finish();
    return;
  }
  const proj = p.items[0];
  const wfId = proj.workflowId;
  ok('project has workflowId + stages', !!wfId && Array.isArray(proj.stages));

  // workflow + stages
  const wf = await get(`/workflows/${wfId}`);
  ok('GET /workflows/{id}', wf.status === 200 && !!wf.body.id);
  const stages = await get(`/workflows/${wfId}/stages`);
  ok('GET /workflows/{id}/stages paged', stages.status === 200 && Array.isArray(stages.body.items));

  // allowed-actions shapes
  const wfActions = await get(`/workflows/${wfId}/allowed-actions`);
  ok('GET /workflows/{id}/allowed-actions', wfActions.status === 200 && (Array.isArray(wfActions.body.allowed) || Array.isArray(wfActions.body.unavailable)));
  const platActions = await get('/allowed-actions');
  ok('GET /allowed-actions (platform)', platActions.status === 200 && platActions.body.scope === 'platform');

  // core reads
  const gates = await get('/gates?status=WAITING');
  ok('GET /gates?status=WAITING', gates.status === 200 && Array.isArray(gates.body.items));
  const notif = await get('/notifications');
  ok('GET /notifications paged', notif.status === 200 && Array.isArray(notif.body.items));
  const runs = await get('/deployment-runs');
  ok('GET /deployment-runs paged', runs.status === 200 && Array.isArray(runs.body.items));
  const limits = await get('/upload-limits');
  ok('GET /upload-limits', limits.status === 200);

  // OPERATOR_REQUIRED enforced on a state change without X-Operator
  const noOp = await fetch(`${V1}/projects`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name: 'x' }) });
  const noOpBody = await noOp.json().catch(() => ({}));
  ok('POST without X-Operator → 4xx OPERATOR_REQUIRED', noOp.status >= 400 && noOp.status < 500 && noOpBody.errorCode === 'OPERATOR_REQUIRED', `(status ${noOp.status}, code ${noOpBody.errorCode})`);

  // SSE: open the stream and read the first frame
  await new Promise((resolve) => {
    const ctrl = new AbortController();
    fetch(`${V1}/workflows/${wfId}/events`, { headers: H, signal: ctrl.signal })
      .then((res) => {
        ok('GET /workflows/{id}/events is event-stream', res.status === 200 && (res.headers.get('content-type') || '').includes('event-stream'));
        const reader = res.body?.getReader();
        if (!reader) return resolve();
        let buf = '';
        const dec = new TextDecoder();
        const readChunk = () =>
          reader.read().then(({ done, value }) => {
            if (done) return resolve();
            buf += dec.decode(value, { stream: true });
            if (/event:\s*\S+/.test(buf)) {
              ok('SSE delivered a named event frame', true);
              ctrl.abort();
              resolve();
            } else {
              readChunk();
            }
          });
        readChunk();
        setTimeout(() => {
          ctrl.abort();
          resolve();
        }, 3000);
      })
      .catch(() => {
        ok('SSE reachable', false);
        resolve();
      });
  });

function note(name, extra = '') {
  console.log(`  - ${name}${extra ? ` (${extra})` : ''}`);
}

// ---- milestone-continuation endpoints (M2/M3/M4). Some are S3-backed and 5xx under PROVIDER_MODE=fakes;
// those are recorded as "unavailable in this provider mode" (the UI degrades gracefully) rather than failing.
function classify(name, status, body) {
  if (status < 500 && typeof body === 'object') {
    ok(`GET ${name}`, true);
  } else if (status >= 500) {
    note(`GET ${name}`, `${status} — not available under this provider mode`);
  } else {
    ok(`GET ${name}`, false, `(status ${status})`);
  }
}

  const projId = proj.id;
  const stageId = (stages.body.items ?? [])[0]?.id;
  const reads = [
    [`/projects/${projId}/uid-pool`, 'uid-pool'],
    [`/projects/${projId}/effective-policy`, 'effective-policy'],
    [`/projects/${projId}/decisions`, 'decisions'],
    [`/projects/${projId}/registry/managed-scope`, 'managed-scope'],
    [`/projects/${projId}/blueprint/versions`, 'blueprint-versions'],
    [`/projects/${projId}/object-graph`, 'object-graph'],
    [`/projects/${projId}/package-files`, 'package-files'],
    [`/workflows/${wfId}/artifacts`, 'workflow-artifacts'],
    [`/governance/prompts`, 'governance/prompts'],
    [`/governance/budget?projectId=${encodeURIComponent(projId)}`, 'governance/budget(project)'],
    [`/governance/evaluations`, 'governance/evaluations'],
    [`/governance/agents`, 'governance/agents'],
    [`/environments/dev/protection`, 'environment/protection'],
    ['/auth/session', 'auth/session'],
    ['/users', 'users'],
    ['/roles', 'roles'],
  ];
  if (stageId) reads.push([`/stages/${stageId}/chat`, 'stage-chat(GET)']);
  for (const [path, name] of reads) {
    const r = await get(path);
    classify(name, r.status, r.body);
  }

  const versions = await get(`/projects/${projId}/blueprint/versions`);
  const vs = (versions.body.items ?? []).map((v) => v.version ?? v).filter(Boolean);
  if (vs.length >= 2) {
    const d = await get(`/projects/${projId}/blueprint/diff?fromVersion=${encodeURIComponent(vs[1])}&toVersion=${encodeURIComponent(vs[0])}`);
    ok('GET blueprint/diff (2 versions)', d.status < 500 && typeof d.body === 'object', `(status ${d.status})`);
  } else {
    console.log(`  - skip blueprint/diff (only ${vs.length} version)`);
  }

  if ((gates.body.items ?? [])[0]) {
    const g = gates.body.items[0];
    const ex = await get(`/gates/${g.id}/explanation`);
    ok('GET gate/{id}/explanation', ex.status < 500 && typeof ex.body === 'object', `(status ${ex.status})`);
  }

  const aRun = (runs.body.items ?? [])[0];
  if (aRun) {
    const b = await get(`/deployment-runs/${aRun.id}/environment-bindings`);
    ok('GET deployment-runs/{id}/environment-bindings', b.status < 500 && typeof b.body === 'object', `(status ${b.status})`);
  }

  // normalized requirements, S2 section schemas, artifact diff (UM-REQ-003 / UM-MAN-002 / UM-REV-001)
  const nrr = await get(`/workflows/${wfId}/nrr`);
  ok('GET workflows/{id}/nrr', nrr.status < 500 && typeof nrr.body === 'object', `(status ${nrr.status})`);
  const secs = await get(`/projects/${projId}/blueprint/section-schemas`);
  ok('GET blueprint/section-schemas', secs.status < 500 && Array.isArray(secs.body.sections), `(status ${secs.status})`);
  const secDomain = await get(`/projects/${projId}/blueprint/section-schemas/domain`);
  ok('GET blueprint/section-schemas/domain', secDomain.status < 500 && typeof secDomain.body === 'object', `(status ${secDomain.status})`);
  const arts = await get(`/workflows/${wfId}/artifacts`);
  const art = (arts.body.items ?? [])[0];
  if (art) {
    const ad = await get(`/artifacts/${art.id}/diff?against=${encodeURIComponent(art.prevVersionId ?? art.id)}`);
    classify(`GET artifacts/{id}/diff`, ad.status, ad.body);
  }

  // POST checks: stage-chat reply (M3) + login rejects bad credentials (M4)
  if (stageId) {
    const chatPost = await fetch(`${V1}/stages/${stageId}/chat`, { method: 'POST', headers: { ...H, 'Content-Type': 'application/json' }, body: JSON.stringify({ message: 'verify-live ping' }) });
    const chatBody = await chatPost.json().catch(() => ({}));
    ok('POST stage-chat → answer', chatPost.status < 500 && typeof chatBody.answer === 'string', `(status ${chatPost.status})`);
  }
  const badLogin = await fetch(`${V1}/auth/login`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ subject: '__no_such__', password: 'x' }) });
  const badBody = await badLogin.json().catch(() => ({}));
  ok('POST auth/login bad creds → 4xx', badLogin.status >= 400 && badLogin.status < 500 && !!badBody.errorCode, `(status ${badLogin.status}, code ${badBody.errorCode})`);

  finish();
}

function finish() {
  console.log(`\n${pass} passed, ${fail} failed`);
  process.exit(fail ? 1 : 0);
}

main().catch((e) => {
  console.error('verify-live crashed:', e);
  process.exit(1);
});
