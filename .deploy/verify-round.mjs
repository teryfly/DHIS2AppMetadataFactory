// Post-deploy live probe: reads the deployment's own admin credential from .deploy/env (never from
// argv, never printed), and checks the two consoles actually serve this round's code plus the action
// the Handoff pages now gate on.
import { readFileSync } from 'node:fs';
import { request } from 'node:https';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const HOST = '127.0.0.1';
const DESK = 1583;
const MOB = 1584;

// The deployment's own certificate is the trust anchor: `install.sh` puts 127.0.0.1 and the public
// IP in the SANs, so a probe against the loopback address verifies normally. Nothing global is
// relaxed — an unverified TLS connection would prove nothing about the site users actually reach.
const CA = readFileSync(join(import.meta.dirname, 'tls', 'cert.pem'));

let pass = 0;
let fail = 0;
function ok(name, cond, extra = '') {
  if (cond) { pass++; console.log(`  ✓ ${name}`); } else { fail++; console.error(`  ✗ ${name} ${extra}`); }
}

function raw(port, path, { method = 'GET', headers = {}, body } = {}) {
  return new Promise((resolve, reject) => {
    const req = request({ host: HOST, port, path, method, headers, ca: CA, servername: 'localhost' }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, headers: res.headers, text: Buffer.concat(chunks).toString('utf8') }));
    });
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

function envValue(file, key) {
  const line = readFileSync(file, 'utf8').split('\n').find((l) => l.startsWith(`${key}=`));
  return line ? line.slice(key.length + 1).trim().replace(/^["']|["']$/g, '') : '';
}

// Every chunk the entry HTML references, plus every modulepreload it hoists.
async function servedAssets(port) {
  const index = await raw(port, '/');
  const refs = [...index.text.matchAll(/(?:src|href)="\/(assets\/[^"]+?)"/g)].map((m) => `/${m[1]}`);
  const bodies = [];
  for (const ref of refs) bodies.push((await raw(port, ref)).text);
  return { status: index.status, html: index.text, joined: bodies.join('\n'), count: refs.length };
}

/**
 * A named lazy chunk, asked of the server rather than of `dist/`: the whole point is that the bytes a
 * browser would download are this round's. `file` is matched against the built asset names, so a hash
 * change between the two sides shows up as a 404 instead of a false pass.
 */
async function servedChunk(port, dir, pattern, expect) {
  const { readdirSync, readFileSync } = await import('node:fs');
  const name = readdirSync(dir).find((f) => pattern.test(f) && readFileSync(`${dir}/${f}`, 'utf8').includes(expect));
  if (!name) return { found: false, why: `${expect} is not in any local asset under ${dir}` };
  const res = await raw(port, `/assets/${name}`);
  return { found: res.status === 200 && res.text.includes(expect), why: `${name} → HTTP ${res.status}` };
}

async function main() {
  const env = '.deploy/env';
  const user = envValue(env, 'FACTORY_BOOTSTRAP_ADMIN_SUBJECT') || envValue(env, 'FACTORY_BOOTSTRAP_ADMIN_USERNAME');
  const pass2 = envValue(env, 'FACTORY_BOOTSTRAP_ADMIN_PASSWORD');

  const s5 = await servedAssets(DESK);
  ok('S5 :1583 首页 200 且引用了 chunk', s5.status === 200 && s5.count > 0, `(status ${s5.status}, ${s5.count})`);
  ok('S5 首屏里没有替身标记', !s5.joined.includes('VITE_ENABLE_MOCK:"1"') && !s5.html.includes('mockServiceWorker'));
  ok('S5 服务的是本轮的构建：原因码文案在 chunk 里', s5.joined.includes('PACKAGE_NOT_GENERATED'));
  ok('S5 补齐的错误码在 chunk 里（DHIS2_TASK_LOST）', s5.joined.includes('DHIS2_TASK_LOST'));
  const dev = await servedChunk(DESK, 'S5/dist/assets', /\.js$/, 'frontend_package.download');
  ok('S5 线上下发的分块带着新的门控动作名', dev.found, dev.why);

  const s5m = await servedAssets(MOB);
  ok('S5M :1584 首页 200 且引用了 chunk', s5m.status === 200 && s5m.count > 0, `(status ${s5m.status}, ${s5m.count})`);
  ok('S5M 服务的是生产构建（没有 harness 路由）', !s5m.joined.includes('dev/json-editor'));
  ok('S5M 服务的是本轮的构建：漂移门控的动作名在 chunk 里', s5m.joined.includes('deployment_run.drift_check'));
  const seg = await servedChunk(MOB, 'S5M/dist/assets', /^FuiSegmented-.*\.js$/, 'fui-chips__chip');
  ok('S5M 的新选择控件分块可下载', seg.found, seg.why);
  const chips = await servedChunk(MOB, 'S5M/dist/assets', /.*\.css$/, 'fui-chips__chip--on');
  ok('S5M 的共用 chip 样式表随分块一起下发', chips.found, chips.why);
  const hof = await servedChunk(MOB, 'S5M/dist/assets', /\.js$/, 'frontend_package.download');
  ok('S5M 线上下发的分块带着新的门控动作名', hof.found, hof.why);

  if (!user || !pass2) {
    console.error('  ! 没有读到引导管理员凭据，跳过登录后的 allowed-actions 检查');
  } else {
    const csrfHeaders = {};
    const loginBody = JSON.stringify({ subject: user, password: pass2 });
    const res = await raw(DESK, '/api/v1/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(loginBody), 'X-Operator': user },
      body: loginBody,
    });
    ok('活体登录 200/204', res.status === 200 || res.status === 204, `(got ${res.status})`);
    const cookie = (res.headers['set-cookie'] || []).map((c) => c.split(';')[0]).join('; ');
    csrfHeaders.Cookie = cookie;
    const csrf = (res.headers['set-cookie'] || []).map((c) => c.split(';')[0]).find((c) => c.startsWith('factory_csrf='));
    if (csrf) csrfHeaders['X-CSRF-Token'] = csrf.split('=')[1];
    ok('会话与 CSRF Cookie 都拿到了', !!cookie && !!csrf);

    const projects = await raw(DESK, '/api/v1/projects?includeArchived=true', { headers: csrfHeaders });
    const list = JSON.parse(projects.text).items || [];
    ok('登录后能列项目', projects.status === 200 && list.length > 0, `(status ${projects.status})`);
    for (const p of list.slice(0, 3)) {
      const wf = await raw(DESK, `/api/v1/projects/${encodeURIComponent(p.id)}/workflow`, { headers: csrfHeaders });
      if (wf.status !== 200) { console.log(`  · ${p.id} 没有工作流（未开始），跳过`); continue; }
      const workflowId = JSON.parse(wf.text).id;
      const aa = await raw(DESK, `/api/v1/workflows/${encodeURIComponent(workflowId)}/allowed-actions`, { headers: csrfHeaders });
      const view = JSON.parse(aa.text);
      const names = [...(view.allowed || []), ...(view.unavailable || [])].map((e) => e.action);
      ok(
        `${p.id} 的 allowed-actions 里有 frontend_package.download`,
        names.includes('frontend_package.download'),
        `(status ${aa.status}, ${names.length} 个动作)`,
      );
      const pkg = names.includes('frontend_package.download')
        ? [...(view.allowed || []), ...(view.unavailable || [])].find((e) => e.action === 'frontend_package.download')
        : null;
      if (pkg && !pkg.available) {
        // Both answers are honest and both are handled on screen: a project that never produced a
        // package says PACKAGE_NOT_GENERATED, an archived one says PROJECT_ARCHIVED — and the page
        // must not promise a stage in the second case.
        ok(
          `${p.id} 被 withhold 时给得出可显示的原因码`,
          ['PACKAGE_NOT_GENERATED', 'PROJECT_ARCHIVED'].includes(pkg.reasonCode),
          `(reason ${pkg.reasonCode})`,
        );
      }
    }

    // The artifact page offers download / copy-link / purge, all three decided by this one address
    // family, so it is asked of the live server: `?download=true` must arrive as an attachment with
    // a filename, and the plain read must not (UM-REV-001's two controls are only distinct if the
    // bytes behind them are).
    let artProbed = false;
    for (const p of list.slice(0, 6)) {
      const wf = await raw(DESK, `/api/v1/projects/${encodeURIComponent(p.id)}/workflow`, { headers: csrfHeaders });
      if (wf.status !== 200) continue;
      const workflowId = JSON.parse(wf.text).id;
      const arts = await raw(DESK, `/api/v1/workflows/${encodeURIComponent(workflowId)}/artifacts?limit=5`, { headers: csrfHeaders });
      const rows = JSON.parse(arts.text).items || [];
      const aa = await raw(DESK, `/api/v1/workflows/${encodeURIComponent(workflowId)}/allowed-actions`, { headers: csrfHeaders });
      const view = JSON.parse(aa.text);
      const entries = [...(view.allowed || []), ...(view.unavailable || [])];
      for (const row of rows.slice(0, 3)) {
        const id = String(row.id);
        const plain = await raw(DESK, `/api/v1/artifacts/${encodeURIComponent(id)}/content`, { headers: csrfHeaders });
        if (plain.status === 410) { console.log(`  · ${id} 内容已清除，换下一个`); continue; }
        const dl = await raw(DESK, `/api/v1/artifacts/${encodeURIComponent(id)}/content?download=true`, { headers: csrfHeaders });
        const disp = String(dl.headers['content-disposition'] || '');
        ok(`${id} 的内容可就地读取`, plain.status === 200, `(status ${plain.status})`);
        ok(`${id} 就地读取不强制下载`, !plain.headers['content-disposition'], `(got ${plain.headers['content-disposition']})`);
        ok(`${id} 的下载地址是附件且带文件名`, dl.status === 200 && /^attachment;/.test(disp) && /filename/i.test(disp), `(status ${dl.status}, disposition ${disp})`);
        ok(
          `${p.id} 的 allowed-actions 里有 artifact.download`,
          entries.some((e) => e.action === 'artifact.download'),
          `(status ${aa.status}, ${entries.length} 个动作)`,
        );
        artProbed = true;
        break;
      }
      if (artProbed) break;
    }
    if (!artProbed) console.log('  · 线上没有可读的产物内容，跳过下载探针');
  }

  console.log(`\n${pass} passed, ${fail} failed`);
  process.exitCode = fail ? 1 : 0;
}

main().catch((e) => { console.error(String(e && e.message || e)); process.exitCode = 1; });
