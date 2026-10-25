# factory-control — S1 Factory 控制平面

DHIS2 App Studio / Metadata Factory 的 S1 子系统：Factory 唯一的对外 REST / SSE 入口，持有全部流程与审批状态，
通过调用 S2 / S3 / S4 的公共 API 编排设计主线与 Deployment Run，并负责把 S0～S4 组装为可部署的后端运行时。

需求依据：`../requirements/S1_Factory控制平面_SRS.md`（V1.4.0）与 `../requirements/00_总体架构与集成约定.md`（V1.3.0）。
实现范围为里程碑 **M1 + M2（MVP）**，加上 **M3 的阶段内对话、页面/API/Fixture Agent 与 HANDOFF**、
**M4 的预算写入、身份权限、多人审批与职责分离**。
（`docs/CHANGELOG.md` 一路记到 **1.19.0**：契约现 1.19.0 / 147 路径 / 172 个操作，`S5/contract/openapi.json` 与 `S5M/contract/openapi.json` 都钉在同一份上，两端签入的生成类型由 `install.sh` 逐次比对后构建）。`GET /api/v1/setup`（S5IF-SET01）把「这台机器配到哪一步了」的事实一次回齐，供两端控制台的装机引导读；它只回事实，可用性仍然只来自 `allowed-actions`。`PUT|GET|DELETE /api/v1/secrets[/{name}]`（S5IF-SEC01…03）与 `GET /governance/provider-catalogue`（S5IF-GOV07）让凭证在界面里录一次就存进 `cp.secrets`（AES-256-GCM，主密钥只由 `FACTORY_CREDENTIAL_MASTER_KEY` 提供），此后任何接口只回引用 —— 值不回显、不掩码、不给长度（S5IF-G09）。1.8.0～1.19.0 是接上 S2/S3/S4 真身之后逐条实跑修出来的路径：回归记录、上传内容扫描、审批人数、UID 池补池、按环境的删除、`codePrefix`、Run 摘要的产物槽位与 `contentPurged`、变更申请自带的 `deletionCandidates`、跨项目列表按成员关系过滤、提供方「测试连接」、模型的差量 `PATCH`，以及把「一题一答」换成整页问卷的 `POST /questions/answers`（1.19.0）——缘由与依据都写在 CHANGELOG 每一条的标题里。
M4 的权限由 `FACTORY_AUTH_MODE` 启用，默认 `off` 时保持 MVP 行为：
`X-Operator` 即身份、授权钩子恒允许、既有 S5 客户端不变；`local|oidc|both` 下认证与角色、
多人审批和职责分离全部生效，启动报告与 `/healthz` 回显 `authMode`。高可用部署与 Tracker / DataStore / App 上传执行器仍不在范围内。
供 S5 使用的公共 REST 补充见 `../requirements/S1_add_api_for_S5.md`，其交付记录在 `docs/CHANGELOG.md`。

## 进程

| 入口 | 说明 |
| --- | --- |
| `factory-api` | REST + SSE，无状态，可水平扩展；启动时按 00 doc 6.10 顺序执行迁移与注册 |
| `factory-worker` | `FACTORY_WORKFLOW_ENGINE=temporal` 时为 Temporal 工作进程；`inprocess` 时运行开发用定时循环 |

编排引擎有两套同接口实现：`engine/inprocess.py`（线程池，调用与服务层完全相同的入口）与
`engine/temporal.py`（每阶段一个 `CpStageExecution` 工作流，`CpScheduledJobs` 承接服务端 schedule）。
活动重试严格遵循 CP-WF-009：只有 S0 错误目录中标记 `retryable` 的错误才会重试。

## REST 表面的分层

`rest/` 是一个子包，不是一个文件：

| 位置 | 职责 |
| --- | --- |
| `rest/app.py` | 只装配：建 app、挂中间件、注册异常处理、`include_router` |
| `rest/routes/<group>.py` | 一个资源组一个模块，handler 保持 `def`（同步）并返回 `dict`；`setup.py` 是唯一的跨提供方聚合读 |
| `rest/schemas/<group>.py` | 请求与响应的 Pydantic 模型；`CamelModel` 一处决定 wire 命名 |
| `rest/deps.py` | `runtime_of` / `operator_of` / `authorize` |
| `rest/envelope.py` | 把 `FactoryError` 变成状态码与相关头 |
| `rest/failure.py` | SRS 8.3 失败信封的唯一构造点（含按码定型的 `details`），中间件的拒绝也走它 |
| `rest/route_meta.py` | 逐操作的 `x-requirements`（S1 `CP-*` 与 `S5IF-*` 合并）与跨切请求头引用 |
| `rest/paging.py` | 分页信封的唯一实现：`nextCursor` 编解码、`sortBy`/`sortDir` 校验、页长上限（S5IF-G04） |
| `rest/download.py` | 产物下载文件名的三条规则与 `Content-Disposition` 拼法（S5IF-FLD09） |
| `rest/openapi.py` | 文档级补充：`components.parameters` / `components.headers` / `ErrorDetails` / 版本；`servers` 留空由部署注入 |

启动长任务的端点（阶段运行与重跑、输入解析、漂移重检、评测发起、项目导出与导入、COMMIT）
一律 202 并返回同一个受理信封 `{stageId?, stepRunId, accepted}`（CP-API-012）：
`stepRunId` 是结果落在哪条记录上的 id，S5 由此去 SSE 与对应查询端点观察，响应里没有
同步结果，也没有 `engine`（部署形态属内部细节）。

路由模板（前缀 + 路径 + `{参数名}`）必须逐字符保持：幂等作用域取自路由模板，
`operationId` 取自 handler 函数名。改错一处就等于改了既有幂等重放的语义。

转发型端点（环境、Governance、Blueprint 读）**不自持事务**：S2 的 `get_entries`/`diff_versions`
带 `@external_io`，S0 禁止在事务内读产物字节；需要 session 的纯读由适配器的 `SESSION_METHODS`
自行开一个短 `unit_of_work()` 注入，而不是让 S1 把事务横跨到子系统调用上（00 文档 6.2、CP-API-007）。

## 分层与导入规则

- `foundation.py` 是包内**唯一**导入 `factory_foundation`（S0）的模块，并在此声明 `FOUNDATION_CONTRACT`；
  `bootstrap.bootstrap()` 启动即校验该契约，S0 的并发改动会立刻暴露为可读错误。
  S0 需带 `py.typed`（已补），否则 S1 的 `mypy --strict` 无法看到其签名。
- `adapters/` 是包内**唯一**导入 S2 / S3 / S4 包的模块；其余层只依赖 `ports/protocols.py` 的 Protocol。
  S2 / S3 / S4 尚未发布时，`adapters/registry.py` 回落到 `testing/fakes.py` 中的替身（CP-ASM-08），
  替身会把产物登记到真实 S0，因此 Gate 哈希复核与 COMMIT 三处哈希一致可端到端验证。
- S1 只读写 PostgreSQL schema `cp`；不跨 schema 访问、不持久化他方数据（D-08、D-14）；
  这些结构约束由 `tests/unit/test_schema_isolation.py` 断言。
- 授权统一走 `iam.authorize(operator, project_id, action)`，动作名取自附录 B；`auth=off` 下恒允许，`local|oidc|both` 下由内置角色、项目成员关系与职责分离规则判定。
  一条不变式由 `tests/unit/test_role_catalogue.py` 守住：**路由 authorize 的每个动作，至少要被一个内置角色授予**。
  `auth=off` 下授权钩子恒允许，所以"没人被授予"这种洞在测试里是隐形的，只在真实登录的第一次点击才现形
  ——`project.export` 就是这样漏掉的，而 `CP-PRJ-007` 规定它是删除的前置条件。

## 开发

```sh
bash scripts/install_dev.sh      # venv + 依赖（含本地 ../S0 可编辑安装）
bash scripts/run_dev.sh          # 迁移 + factory-api + 进程内引擎
pytest                           # 见 pyproject 的 marker 分层：unit/service/api/temporal/acceptance/e2e
mypy && ruff check src tests scripts
```

测试需要一个可写的 PostgreSQL：`export FACTORY_TEST_DATABASE_URL=postgresql+psycopg://user@/factory_s1_test?host=/var/run/postgresql`。
`tests/api/test_live_http.py` 会在本机随机端口拉起真实 uvicorn，用于验证 SSE 与幂等重放（ASGI 测试客户端会缓冲流式响应，故必须走 socket）。

**这个库同一时刻只能给一个 pytest 会话用。** `testing/harness.py` 在每个会话开始时 `DROP SCHEMA
cp, dx, bp, ai, core CASCADE` 再重放迁移，所以并行开两个会话（或把全量套件挂后台再单跑一个文件）会
让彼此的迁移撞 `pg_type_typname_nsp_index` / `pg_namespace_nspname_index`，症状是一长串
`relation "cp.system_settings" does not exist` 的 setup error——与被测代码无关。要并行就再指一个库：
`FACTORY_TEST_DATABASE_URL=…?host=/var/run/postgresql` 各给各的。S3 的 README 写着同一条规则。

两个脚本：

```sh
.venv/bin/python scripts/export_openapi.py --out docs/openapi.json   # OpenAPI 3.1，147 个路径 / 172 个操作
.venv/bin/python scripts/seed_demo.py --base-url http://127.0.0.1:8080
npx --yes openapi-typescript@7 docs/openapi.json -o /tmp/fc.types.ts # 生成侧闸：S5 的类型生成必须无警告
```

`docs/openapi.json` 是 S5 生成客户端的唯一事实来源（CP-API-001），因此**新增或修改路由后必须重跑该脚本并提交结果**。
每个 `/api/v1` 操作带 `x-requirements` 数组：S1 自己的 `CP-*` 编号与缺口清单的 `S5IF-*` 编号在同一个
通用字段里（S5IF-G11），S5 的契约漂移检查与测试组的覆盖追溯读同一个字段。

`docs/openapi.json` 的 `servers` 为空是约定而非缺省：S5 与 S1 同域（UI-APP-008），基址由部署注入；
写死任何主机名都会在别的环境里说谎（S5IF-G11 ③）。

`seed_demo.py` 需要一个以替身运行的服务：`FACTORY_PROVIDER_MODE=fakes`
且 `FACTORY_FAKE_ENVIRONMENTS=dev:TEST,prod:PRODUCTION`（替身 S4 需要先“认识”这些环境键，
真实部署则由 S4 提供）。

## 部署

- `deploy/docker/docker-compose.yml`：PostgreSQL、Temporal（含 UI）、MinIO、factory-api、factory-worker、S5 静态位。
- `deploy/helm/factory/`：两个 Deployment（api / worker）、Service、Ingress、迁移 Job（pre-install/pre-upgrade）。
  凭据只以 Secret 引用形式出现（`database.existingSecret` 等），chart 不含任何明文。
- SSE 需要每实例一条 LISTEN 连接（CP-EVT-005）：Service 只选中 api 副本，Ingress 关闭代理缓冲。

## 已验证与未验证

已验证（本机 PostgreSQL + uvicorn，逐条 HTTP 观察）：建项目（模式 A 跳过 PAGE_DESIGN / HANDOFF）→
上传输入 → 设计主线运行 → Question / Gate → 版本提升与失效传播 → Deployment Run R1…R6 →
PACKAGE_GATE → 未审批时 COMMIT 返回 409 → DEPLOY_GATE → COMMIT 成功 → COMMIT 追溯与执行报告 → 项目导出；
SSE 断线后按 `Last-Event-ID` 续传且实时事件在 2 秒内到达；缺 `X-Operator` 400、同键异体 422、
同键同体重放逐字节一致。

身份与权限在 `local` 模式下经 HTTP 层验证（`TestClient` 以 `https://testserver` 为基址，因为会话
Cookie 是 `Secure` 的：登录、带会话读取、缺 CSRF 值 403、`logout` 后 401、禁用账号即刻失效、
无角色 403、`allowed-actions` 按人不可用、执行者不得审批自己的生产 Gate）。
活体 uvicorn 冒烟（`factory-api`，替身提供方，`auth_mode=off`）：`/healthz` 200、`/api/v1/openapi.json`
回 1.19.0/147 路径、`GET /setup` 在零登记环境回 200 且各项为 0（不是 404/500）、`PUT /governance/budget` 写读一致并归一币种、`GET /auth/session` 如实回
`authMode=off`、`GET /gates?mine=true` 200。

已登录（`auth_mode=local`，本机部署 1583 端口，逐条 HTTP 观察）：`GET /governance/policy` 同返生效与
上限且 `effective ⊆ ceiling`；`POST /governance/policy/relaxations` 201 并带 `reason`/`change`/`createdAt`，
**再读一次看得见该密级确实变宽**（上限不动），PHI_PII 与越上限各回 403、缺 `change` 与空理由各回 422；
`POST /environments/{key}/disable` 之后 `allowed-actions` 把停用/测试连接/能力探测翻成
`ENVIRONMENT_DISABLED` 而编辑与锁定仍在，三条写操作直接打过去各回 409（不是 500）；
`/workflows/{id}/pause|resume|cancel` 200 且读回翻转，已取消的工作流再 pause 回 409
`WORKFLOW_CANCELLED`；`GET /users` 带 `roles`，授予后读回有、`DELETE /users/{id}/roles?role=` 后读回无；
项目按「归档 → 导出 → 删除」走完（`GET /projects/{id}/export` 202，删除 202）。
**未做**：`FACTORY_AUTH_MODE=oidc` 与 `both` 与真实组织目录的活体联调（OIDC 依赖方逻辑由本地假 IdP 的单元/接口测试覆盖），以及 `scripts/run_dev.sh` 起真实 uvicorn 的登录冒烟。未验证或不在范围内：`docker compose up --build` 与 Kubernetes 实际下发（compose 仅做配置校验）；Temporal 服务端上的工作流回放（离线以 stub 客户端与活动/工作流单测覆盖）；前端交互（属 S5 / S5-M 范围）；Tracker / DataStore / App 上传执行器（S4 未交付）。真实 S2 / S3 / S4 已按 `FACTORY_PROVIDER_MODE=real` 接入并逐条实跑（见 `user-guide.md` §8），多审批人与职责分离在用例 AC-GATE-04 中以 `auth_mode=local` 验证。
