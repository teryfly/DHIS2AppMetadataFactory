// UM-AC-01..35 → 测试文件的对照表。文件路径全部经 existsSync 校验，因此这张表不会说谎。
import { existsSync, mkdirSync, writeFileSync } from 'node:fs';

const ROWS = [
  ['UM-AC-01', '操作人缺失时改状态动作禁用', ['src/api/api.test.ts'], 'unit'],
  ['UM-AC-02', '按钮可用性只来自 allowed-actions', ['src/api/api.test.ts'], 'unit'],
  ['UM-AC-03', '整页问卷同屏：单选/多选、推荐、其他、阻断校验', [], 'manual'],
  ['UM-AC-04', '一次提交整份问卷；服务端 resume 一次，下一轮原地显示', [], 'manual'],
  ['UM-AC-05', '并发冲突提示并刷新', ['src/api/api.test.ts'], 'unit'],
  ['UM-AC-06', '幂等键复用', ['src/api/api.test.ts'], 'unit'],
  ['UM-AC-07', 'SSE 断开转轮询', [], 'manual'],
  ['UM-AC-08', '离线只读与黄条', [], 'manual'],
  ['UM-AC-09', '上传分级预选与 PHI 提示', ['src/api/upload.test.ts'], 'unit'],
  ['UM-AC-10', '360px 与 200% 字号不溢出', [], 'manual'],
  ['UM-AC-11', '1440px 三栏', [], 'manual'],
  ['UM-AC-12', '深链复制 URL 可回到同一状态', [], 'manual'],
  ['UM-AC-13', '待办卡片优先级', ['src/features/todo/todoView.test.ts'], 'unit'],
  ['UM-AC-14', '通知首屏可见失败原因', ['tests/e2e/clickBudget.spec.ts'], 'e2e'],
  ['UM-AC-15', '4G 模拟 FCP/LCP', ['scripts/lighthouse.mjs', 'docs/reports/lighthouse-mobile.md'], 'script'],
  ['UM-AC-16', '中英全覆盖、切换不刷新', ['src/i18n/parity.test.ts', 'src/test/i18nLiterals.test.ts', 'tests/e2e/i18n.spec.ts'], 'unit+e2e'],
  ['UM-AC-17', 'JSON 编辑器越界键标注与补全', ['src/lib/json/json.test.ts', 'tests/e2e/jsonEditor.spec.ts'], 'unit+e2e'],
  ['UM-AC-18', '1,000 节点中端手机 ≥30fps、5,000 节点桌面 ≥30fps、>200 默认按类型折叠', ['src/lib/graph/graph.test.ts', 'tests/e2e/graph.perf.spec.ts'], 'unit+e2e'],
  ['UM-AC-19', '未生成时的引导式空状态', ['src/api/notGenerated.test.ts', 'src/fui/FuiGuidedEmpty.test.ts', 'tests/e2e/notGenerated.spec.ts'], 'unit+e2e'],
  ['UM-AC-20', 'API 审核无可发起请求的控件/代码路径', ['src/test/isolation.test.ts', 'tests/e2e/m4Presence.spec.ts'], 'unit+e2e'],
  ['UM-AC-21', 'M4 成员/授权/豁免三页存在并按契约接线（原 MVP 裁剪，见 ADR-004）', ['src/test/m4Presence.test.ts', 'tests/e2e/m4Presence.spec.ts'], 'unit+e2e'],
  ['UM-AC-22', '账号登录后进入工作台', ['src/api/api.test.ts'], 'unit'],
  ['UM-AC-23', '引导页登记环境并把凭证录进界面', ['src/features/start/startSteps.test.ts', 'src/features/secrets/credentialField.test.ts', 'tests/e2e/startGuide.spec.ts'], 'unit+e2e'],
  ['UM-AC-24', '打开引导页的模型一步', ['src/features/start/startView.test.ts', 'tests/e2e/startGuide.spec.ts'], 'unit+e2e'],
  ['UM-AC-25', '会话过点后自助恢复，并可改自己的口令', ['src/stores/sessionLapse.test.ts', 'tests/e2e/sessionLapse.spec.ts'], 'unit+e2e'],
  ['UM-AC-26', '建账号并查看锁定状态', [], 'manual'],
  ['UM-AC-27', '改名、停用、再启用账号；授项目级角色', [], 'manual'],
  ['UM-AC-28', '模型清单里编辑已登记的模型（只发改动过的键）', ['src/features/governance/modelSheet.test.ts'], 'unit'],
  ['UM-AC-29', '打开 /todo?kind=gate，或在待办中心筛选待审批', ['src/features/todo/todoView.test.ts', 'tests/e2e/approvals.spec.ts'], 'unit+e2e'],
  ['UM-AC-30', '任何屏幕上的状态/角色/类别都有中英词；缺词原样显示', ['src/lib/vocabulary.test.ts', 'src/i18n/parity.test.ts'], 'unit'],
  ['UM-AC-31', '在需求条目页新增、改一条、删一条（追加/重读后整段替换）', ['src/features/requirements/requirementOps.test.ts', 'src/features/requirements/requirementsView.test.ts', 'tests/e2e/requirements.spec.ts'], 'unit+e2e'],
  ['UM-AC-32', '治理页对提供方点「测试连接」', ['src/features/governance/providerSheet.test.ts'], 'unit'],
  ['UM-AC-33', 'OIDC 登录往返，回调只兑换一次 code', ['src/stores/oidc.test.ts', 'tests/e2e/oidc.spec.ts'], 'unit+e2e'],
  ['UM-AC-34', '阶段运行中按 run 取消 Agent 运行', ['src/stores/progress.test.ts'], 'unit'],
  ['UM-AC-35', '按媒体类型预览/下载产物；NOT_GENERATED 空态', ['src/lib/media.test.ts', 'src/api/notGenerated.test.ts', 'tests/e2e/artifactPage.spec.ts', 'tests/e2e/notGenerated.spec.ts'], 'unit+e2e'],
];

const missing = ROWS.flatMap((r) => r[2]).filter((p) => !existsSync(p));
if (missing.length) {
  console.error('matrix references files that do not exist:', missing.join(', '));
  process.exit(1);
}
mkdirSync('docs/reports', { recursive: true });
writeFileSync('docs/reports/um-ac-matrix.md', [
  '# UM-AC-01..35 覆盖对照',
  '',
  '生成：`node scripts/report-ac-matrix.mjs`（会校验每一行的文件真的存在，路径不存在即失败）。',
  '`manual` 表示本轮没有自动化证据，仍需人工或后续补测——不是已验证。',
  '',
  '| 编号 | 内容 | 证据 | 层级 |',
  '| --- | --- | --- | --- |',
  ...ROWS.map(([id, what, files, kind]) => `| ${id} | ${what} | ${files.length ? files.map((f) => '`' + f + '`').join('<br>') : '—'} | ${kind} |`),
  '',
].join('\n') + '\n');
console.log(`docs/reports/um-ac-matrix.md written: ${ROWS.length} criteria, ${ROWS.filter((r) => r[2].length).length} with an artifact`);
