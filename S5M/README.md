# S5-M · Factory Mobile Web（移动优先前端）

面向实施人员 / 审批人 / 项目负责人的 **移动优先、兼容桌面**的 H5 / PWA 控制台。它不依赖 `@dhis2/ui`，
以中文使用习惯设计；**只调用 S1 公共 REST（`/api/v1`）与 SSE**，自身不持有任何业务状态。

依据 `../requirements/S5M_移动优先前端_SRS.md`（V1.5.0）。功能范围与 S5 相同，界面/交互/技术栈全新。
界面契约为 S1 公共 REST **1.19.0**（147 条路径），`package.json` 版本 1.14.1；1.14.1 之后的代码改动见
`docs/CHANGELOG.md` 的「未发布」。

## 技术栈

Vue 3（组合式 API）+ TypeScript + Vite · Vue Router · Pinia（界面态）· `@tanstack/vue-query`（服务端态）·
Vant 4（由自研 **FUI** 封装，页面只用 FUI）· vue-i18n（zh 默认，可切 en）· dayjs(zh-cn) · VueUse。

契约由 vendored `contract/openapi.json`（S1 发布）经 `openapi-typescript` 生成 `src/api/generated/schema.d.ts`。

## 后端（S1）先跑起来

S5M 只做前端。联调需要一个运行中的 S1（默认 `:8000`）：

```sh
cd ../S1
bash scripts/install_dev.sh          # 首次：venv + 依赖
# 用替身起服务（无需真实 DHIS2），需本机 PostgreSQL socket：
export DATABASE_URL="postgresql+psycopg://ubuntu@/factory_s1_dev?host=/var/run/postgresql" \
       FACTORY_MIGRATIONS_MODE=apply FACTORY_WORKFLOW_ENGINE=inprocess \
       FACTORY_PROVIDER_MODE=fakes OBJECT_STORE_URL=memory:// SECRET_BACKEND=memory \
       FACTORY_FAKE_ENVIRONMENTS="dev:TEST,prod:PRODUCTION"
.venv/bin/factory-api --port 8000
# 另开终端灌演示数据：
.venv/bin/python scripts/seed_demo.py --base-url http://127.0.0.1:8000
```

## 开发

```sh
npm install
npm run codegen          # 由契约生成类型（契约变化后重跑）
npm run dev              # http://localhost:5174，/api/v1 代理到 VITE_S1_PROXY（默认 :8000）
```

dev-server 代理 `/api/v1`（含 SSE，不缓冲）到 S1；生产同域部署由反向代理承担（见 `deploy/nginx.conf`）。

## 校验

```sh
npm run typecheck        # vue-tsc 全量
npm run test             # Vitest：i18n zh/en key 平价、错误映射、幂等、相对时间、allowed-actions 缺失=不可用
npm run build            # typecheck + vite build（PWA 关闭时产物见 dist/）
npm run verify:live      # 对运行中 S1 的联调冒烟（REST 信封 + allowed-actions + SSE 帧 + OPERATOR_REQUIRED）
node scripts/verify-live.mjs   # 同上，可用 S1_URL 覆盖地址
```

Playwright 那一层要**先建 harness 版再跑**：

```sh
npm run build:harness && npm run test:e2e    # 之后：npm run build 把 dist/ 换回生产版
```

`test:e2e` 的 webServer 只做 `vite preview`，用哪个构建完全取决于 `dist/` 此刻是谁写的；
`npm run verify` 的最后一步是生产构建，紧跟着跑 e2e 会让所有 `/dev/json-editor`、`/dev/graph-bench`
用例集体超时（那两个路由只在 `VITE_DEV_HARNESS=1` 时注册）。`grep -c 'dev/json-editor' dist/assets/index-*.js`
为 1 才是 harness 版。整套在满载机器上要 20–30 分钟。

打开浏览器后：先在“我的”填写操作人姓名（否则全部改状态动作禁用，UM-AC-01）；刚装完的机器先开**开始使用**（`/start`，UM-SET-001）；
360 px 移动视口看底部五标签，≥1024 px 自动切换为侧栏、≥1440 px 三栏。

### 首屏包体（UM-NFR-02）

`npm run check:budget` 机器校验，实测见 `docs/reports/bundle-budget.md`：首屏 JS **128.0 KB** gzip
（`index` 39.4 + `vue` 59.9 + `query` 10.9 + 按需引入后的 `vant` 17.3，另含 `/config.js` 与 `index.html` 内联骨架），上限 200 KB；
首屏 CSS **37.1 KB** gzip（壳层改为按文件引入 FUI，绕开桶文件把整个库的样式拖进入口块）。
Vant 不再全局注册（UM-TECH-001）：每个 FUI 包装文件自带所用组件与其样式，整库 77.7 KB 降到 17.3 KB。
重型能力（CodeMirror 编辑器块 176.9 KB、API 文档视图 6.1 KB + js-yaml、图渲染器）全部按路由懒加载，
在 `budget.config.mjs` 的按名白名单里，并且每次运行都打印被豁免的体积。

### Lighthouse（UM-NFR-04 / UM-NFR-01）

`npm run report:lighthouse`（内置 4G 模拟、移动预设、取 3 次运行中位数，产物为 `docs/reports/lighthouse-mobile.md`）。
实测 **无障碍 100 / 最佳实践 100 / 性能 82**（阈值 95 / 90 / 85；TBT 已降到 40 ms，唯一丢分项是 FCP 3.2 s，
而关键路径资源在 181 ms 内下载完毕，所以那 3.2 s 是本机 CPU 等待而不是字节）。性能项仍不能在本机签署：
`host.performanceAttestable` 在 `load1 > 1.5 × 核数` 时为假（本次 3.54 / 2 核），同产物独立测量的 TBT 曾测得 2.1 s 与 29.3 s。
丢分修复记录见 `docs/adr/009`，结论摘要见 `docs/reports/README.md`。

## 能力与证据

| 能力 | 实现 | 证据 |
| --- | --- | --- |
| JSON 编辑（UM-CAP-001） | CodeMirror 6 + Ajv2020 + jsonc-parser，懒加载内核 `src/fui/internal/JsonEditorCore.vue` | `src/lib/json/json.test.ts`、`tests/e2e/jsonEditor.spec.ts` |
| 依赖图（UM-CAP-002） | 自研 Canvas（默认）+ SVG（降级）双渲染臂，两臂都实测 | `tests/e2e/graph.perf.spec.ts` → `docs/reports/graph-bench.md` |
| API 文档（UM-CAP-003） | 自研 Schema 树：`$ref`/循环守卫/深度与展开上限/推导示例/全文搜索，只读 | `src/lib/openapi/openapi.test.ts`、`src/fui/internal/OpenApiDoc.test.ts` |
| 点击预算（§6.1） | 8 条流程 × 4 视口，逐条断言交互次数 ≤ 预算（问卷一条待按整页问卷更新） | `tests/e2e/clickBudget.spec.ts` → `docs/reports/click-budget.md` |
| 引导式空状态（UM-REV-007） | `src/api/notGenerated.ts` 单一意图映射 + `FuiGuidedEmpty` | `src/api/notGenerated.test.ts`、`tests/e2e/notGenerated.spec.ts` |
| 整页问卷（UM-Q-002～004） | 阶段全部 `OPEN` 问题同屏，`answerMode` 单选/复选，`recommended`/「其他」，阻断校验，一次 `POST /questions/answers`，响应下一轮原地渲染 | `src/features/questions/QuestionDetailView.vue`；e2e 问卷用例待补（见「已知限制」） |
| 需求条目页（UM-REQ-004/005） | 新增 `add /requirements/items/-`，改/删重读后整段替换，Schema 上限，阶段关闭收起写入口 | `src/features/requirements/requirementOps.test.ts`、`requirementsView.test.ts` |
| 选型记录（UM-TECH-003） | `docs/adr/001..008`，九段模板由 `src/test/adr.test.ts` 校验 | `docs/adr/README.md` |

`docs/reports/` 下的数字全部由命令生成（见其 README），ADR 引用的实测数据一律指向这些文件。

## 架构

```
src/api      请求层：http(头注入/解包) · errors(信封→AppError) · idempotency(sessionStorage) ·
             actions(allowed-actions + ACTION 常量) · queries/mutations(vue-query 读/写) · upload(XHR)
src/sse      SSE 传输(续传/stream.reset) + 事件→精确失效 + 轮询降级 + 进度 store
src/stores   Pinia：operator · prefs(语言·字号·深色) · ui(最近访问·筛选·稍后处理·草稿·数据分级) · connection · progress
src/fui      自研组件层（UM-UI-002）+ tokens.css（明/暗，UM-UI-001）——页面唯一 UI 来源
src/i18n     zh-CN（默认）/ en，术语与错误码目录与 S5 对齐；无硬编码中文（UM-AC-16）
src/lib      time(UM-UX-004) · breakpoint(移动/桌面) · clipboard/share · toast · storage · labels
src/layouts  AppShell：移动底部标签 / 桌面侧栏 + 顶栏搜索 + 离线黄条 + 操作人引导 + toast
src/features 业务视图（工作台/待办 · 项目/向导/时间线 · 阶段 · 问题 · 审批 · 需求 · 部署 · 审核 · 变更 · 审计 · 环境/治理/设置 · 消息 · 我的）
src/router   可分享深链（完整路由地图见下节）+ 滚动恢复
```

## 已实现的关键规则

- 按钮可用性只来自 `allowed-actions`；缺失=不可用，绝不臆测（UM-BR-01/02）。
- 改状态请求自动带 `X-Operator`；幂等键按 intent 复用、成功释放；COMMIT 网络失败不自动重提（UM-BR-04/05）。
- 409 / `APPROVAL_INVALIDATED` 先失效资源与 allowed-actions 再中文提示（UM-NET-001）。
- 错误中文口语化 + 展示 details + 一键复制 correlationId（UM-NET-002）。
- SSE 断线按最后序号续传、`stream.reset` 全量刷；SSE 不可用自动降级轮询（前台 5s、后台暂停、回前台即刷），降级时顶栏挂「已切换为轮询刷新」——数据仍在动，但不再自己动了（UM-NET-003/004/005）；离线顶部黄条并禁改状态。
- 问题是整页问卷、一次提交：该阶段全部 `OPEN` 问题同屏，`answerMode` 决定单选/复选，`recommended` 标「推荐」，「其他」可自由填写；阻断题未答时提交按住并提示还差几题，提交后服务端 resume 一次，`QuestionnaireAnswerView.questions` 非空即原地换下一轮（UM-Q-002～004、UM-AC-03/04）。
- 需求条目页在手机上逐条写：新增 `add /requirements/items/-`，改/删重读后整段替换；字段上限读分节 Schema；阶段关闭后写入口整体收起（UM-REQ-004/005、UM-BR-09）。
- 审批“全部接受”仍逐条提交 `warningDispositions`；批准携带页面加载的 `seenBoundHashes`（UM-GATE、UM-BR-06）。
- 正式提交 = 滑动确认，PRODUCTION 红色滑块并显示环境 + 载荷哈希后 8 位（UM-DEP-003、UM-AC-06）。
- PHI/PII 永不作为数据分级默认值且需再次确认；上传后自动解析（UM-BR-07、UM-AC-09）。

## PWA（可选）

`VITE_ENABLE_PWA=1 npm run build` 生成 manifest + 静态资源 service worker（业务接口 `NetworkOnly`，
离线只缓存静态资源，UM-NET-005）。启用前请在 `public/` 放置 `pwa-192x192.png` / `pwa-512x512.png`。

## 当前交付范围

S5-M 已实现 SRS 的 M1–M4 全部页面（里程碑只作历史坐标）：移动/桌面双布局、网络层（幂等/错误/SSE/轮询/离线）、
工作台与待办、项目与向导与时间线、阶段工作区、需求上传/解析/列映射、需求条目手写页、问题整页问卷、
人工编写（表单；桌面 JSON）、审批、消息、部署（滑动提交/清单/取消运行）、审核只读视图（对象图/API 文档/交付包/开发包）、
变更申请、审计、环境/治理/设置、装机引导与凭证录入、登录/账号/角色/项目成员/授权/豁免。

契约基线 **1.19.0 / 147 条路径**（`src/test/contractVersion.test.ts` 同时钉住版本与路径数）。`package.json` 版本
**1.14.1**；1.14.1 之后代码新增的整页问卷记在 `docs/CHANGELOG.md` 的「未发布」，版本号尚未提升。

## 路由地图

| 分组 | 地址 | 说明 |
| --- | --- | --- |
| 入口与身份 | `/` · `/login` · `/auth/callback` · `/todo` · `/notifications` · `/me` · `/start` | 底部五标签；`/todo?kind=gate` 等筛选属于地址；登录与 OIDC 回调是无壳页 |
| 项目 | `/projects` · `/projects/new` · `/projects/:id` · `/projects/:id/settings` · `/projects/:id/members` · `/projects/:id/grants` · `/projects/:id/exemptions` | 成员/授权/豁免是项目路由，权利按项目答复 |
| 阶段、问题、审批 | `/stages/:id` · `/questions/:id` · `/gates/:id` | 问题页是该阶段整页问卷 |
| 输入与需求 | `/projects/:id/inputs` · `/projects/:id/inputs/:artifactId/column-mapping` · `/projects/:id/inputs/:artifactId/llm-log` · `/projects/:id/requirements` | 上传/解析、列映射复核、解析日志、需求条目手写 |
| 审核与交付 | `/projects/:id/artifacts` · `/artifacts/:id` · `/projects/:id/metadata` · `/projects/:id/issues` · `/projects/:id/graph` · `/projects/:id/api` · `/projects/:id/api/doc` · `/projects/:id/package` · `/projects/:id/handoff` | API 文档桌面路由可分享；审核内容只读（产物详情另有「清除内容」这一不可逆动作） |
| 部署与运维 | `/workflows/:id/deployments` · `/runs/:id` · `/projects/:id/change-requests` · `/projects/:id/audit` | 运行控制台含取消运行、环境绑定、执行报告 |
| 管理 | `/environments` · `/environments/:key` · `/governance`（重定向 `/governance/models`） · `/governance/:section` · `/access` · `/settings` · 404 | 治理一节一地址：`models`/`providers`/`policy`/`prompts`/`cost`/`evaluations` |
| 开发量具 | `/dev/json-editor` · `/dev/graph-bench` | 只在 `DEV` 或 `VITE_DEV_HARNESS=1` 时注册，不进生产包 |

底部五标签为「工作台 / 项目 / 待办 / 消息 / 我的」；≥1024 px 转左侧栏，≥1440 px 三栏。`/start`、`/access`、
`/governance` 等从侧栏、「我的」与工作台进入，不占第六格。

## 阶段问题：整页问卷

`/questions/:id` 不再是逐题回答：它以该问题所属阶段为范围，取 `GET /workflows/{workflowId}/questions` 中
同 `stageId`、`status=OPEN` 的全部问题，`HIGH` 优先、同级按文本排序，同屏作答。题型由 `QuestionView.answerMode`
决定（`SINGLE` 单选、`MULTIPLE` 复选），建议项按 `recommended` 标「推荐」，`changesUid` 与 `regenerates` 各自给
提示，「其他」展开自由文本。页头显示「已答 n/m 题」；有 `blocking` 题未答时提交按钮按住，页头与底部各提示
还差几题。底部只有一次「提交全部回答并重新生成」，请求是 `POST /questions/answers`
（`AnswerQuestionsBody.answers[]`，空答不发）。响应 `QuestionnaireAnswerView.questions` 就是下一轮：非空则原地
替换并滚到顶，为空则提示「已全部回答」并回阶段/待办；服务端只 resume 一次。没有 5 秒撤销条，未点提交离开
页面也不发任何回答。实现见 `src/features/questions/QuestionDetailView.vue`，文案见 `questions.*`。

## 需求条目手写页

`/projects/:id/requirements`（`src/features/requirements/`）把蓝图草稿的 `requirements` 段当作唯一清单：
解析产出与手写条目同列，可在「草稿／已批准」间切换（阶段已通过时默认已批准且只读），按状态筛选，逐条通过/驳回，
也可全部待定通过。写入走 `POST /stages/{stageId}/patches`：新增只发 `add /requirements/items/-`；改与删先重读
`GET /projects/{id}/blueprint/entries?sections=requirements&source=…`，再以 `replace /requirements/items` 整段替换，
`baseVersion` 用刚读到的版本，不按下标寻址、不发 `test` 哨兵。字段上限读
`GET /projects/{id}/blueprint/section-schemas/requirements`（businessKey 模式/长度、statement/来源说明长度、
priority/status/severity 枚举、confidence 区间），本机常量只兜底；编辑时 businessKey 锁死，合并保留 `actors`、
`source.artifactId` 等表单未展示字段。阶段关闭后 S1 不再给 `patch.submit`，页面收起全部写入口并指向变更申请。

## 装机引导与凭证录入

`/start`（`src/features/start/StartView.vue` + `startSteps.ts`）与桌面的 `/getting-started` 读同一批事实：
`GET /api/v1/setup`（契约 1.19.0）给子系统真实/替身、`validate_runtime()` 的配置问题、「登记了但从未通过连接测试」
的环境、模型与项目计数、凭证库是否可用与哪些引用已解不开；按钮可用性仍然只来自 `allowed-actions`（UM-BR-01）。
分支表在 `startSteps.ts` 里，脱离视图单测（`startSteps.test.ts`），因此「S1 说 null」与「0 个模型」在界面上
不可能被混为一谈。入口三处：≥1024px 左栏、工作台顶部卡片、「我的 → 开始使用」；不占底部五个标签之一。

**四步一步一屏**（UM-SET-001…006）：模型与提供方 → DHIS2 环境 → 第一个项目 → 跑阶段/审批/正式提交。
顶部一条可横滑的步骤条（当前一步 `aria-current="step"`），下面一次只展开当前这一步，模型与环境两步就地打开
`ProviderSheet` / `ModelSheet` / `EnvironmentFormSheet`——都是从治理页与环境页抽出来的**同一份**组件。
「能力接入」（S2/S3/S4 真身或替身）是清单末尾的事实卡，不是待办步骤：`provider_mode` 在服务器 env 文件里，
浏览器里没有任何控件能改它。

**凭证就地录入**（UM-ADM-004 / S5IF-SEC01）：引用输入框旁边的面板把一个值送出去一次（`PUT /secrets/{name}`），
拿回 `db://名字`，然后把值清掉——成功失败都清，且值不进任何浏览器存储、不进 URL、也不进幂等 intent。
`GET /secrets` 只有名字与时间；没有 `GET /secrets/{name}` 这个东西，那会是个 403/404 枚举探针。
`GET /setup` 说 `credentials.storeAvailable=false` 时服务端回 503，界面写「改用 `env://` 引用」而不是「保存失败」。

**提供方与模型**：每个提供方行可「测试连接」（`POST /governance/providers/{name}/test-connection`，结果就地显示
`healthy` 与 `detail`）；`adapter` 是只读常量 `openai_compat`（S3 的 `provider_for()` 不看这个字段），厂商地址与
模型名来自 `GET /governance/provider-catalogue` 且都可自由输入。编辑已登记模型走 `PATCH /governance/models/{ref}`，
只发改动过的字段（`capabilities` 也只发变动成员），提供方与模型标识锁死，差量里没有 `endpoint` 与
`apiKeySecretRef`；什么都没动时保存按住。

**登录**：`authMode` 为 `oidc`/`both` 时，`/login` 在账号口令之外提供「使用组织账号登录」（`GET /auth/oidc/request`
→ `/auth/callback` → `POST /auth/oidc/exchange`，code 只兑换一次）；成功后回到登录前的站内路径，本地不存 token。

## 已知限制与契约核对

- **没有浏览器内 Mock 层**：开发与 e2e 用独立 Node 替身 `scripts/mock-s1.mjs`（REST + SSE），它只覆盖用例端点，
  不是完整契约；界面自身不携带演示数据。替身目前**未实现** `POST /questions/answers`，整页问卷以契约与真实 S1 为准。
- **e2e 问卷用例尚未跟上**：`tests/e2e/clickBudget.spec.ts` 的「回答一个问题」仍按旧的 5 秒延迟提交与 `.qn__sugg`
  编写，和 1.19.0 的整页问卷不一致；本轮只改文档，未动测试。
- **唯一明确的“不做”**：在 S5M 内对目标 DHIS2 直接发起 OpenAPI 调用（try-it）。SRS（UM-REV-005、UM-IF-001）与
  `src/test/isolation.test.ts` 都限定只读，不要求代理执行端点。
- **不是 API 缺口**：object-graph 未编译时 `NOT_FOUND`、package-files `kind=NONE`、无 OPENAPI 产物，都需要先把项目
  跑到「已批准 Blueprint → S2 编译 → PAGE_DESIGN/HANDOFF」。界面按 UM-REV-007 显示引导空状态，不把它当故障。
- **选型差异**：SRS §4 推荐 G6/Scalar；实现用自研 Canvas+SVG 图与自研 OpenAPI 树（ADR 002/003），CodeMirror 6
  用于 JSON 编辑（ADR 001）。这是前端选型，不影响契约。
- **S1 接口覆盖**：`verify-live` 覆盖 object-graph / package-files / workflow-artifacts / chat / auth / budget(PUT) /
  members / grants / exemptions / roles / users 等；它跑在 `auth_mode=off`，因此“某个动作没被任何角色授予”这类
  授权洞测不出来（接口在不在，与有没有身份能做，是两件事）。问卷、需求条目页与提供方探测的端点均已在 1.19.0 契约中。
