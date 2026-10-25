# factory-dhis2 — S4 DHIS2 集成与执行 / DHIS2 Integration & Execution

Python 实现的 **S4 DHIS2 集成与执行子系统**，需求来自
`../requirements/S4_DHIS2集成与执行_SRS.md`，受 `../requirements/00_总体架构与集成约定.md`
与总体 SRS 约束。

S4 是系统中**唯一**访问 DHIS2 实例、**唯一**解析 DHIS2 凭证的子系统。它管理目标环境、
探测版本与能力、发现既有元数据，并以受控方式执行 VALIDATE、COMMIT 与部署后验证。
S4 不做任何架构或业务判断（总体 SRS 4.4“DHIS2 Client 只负责协议与执行”）。

当前代码版本 **0.1.0**（`pyproject.toml`），对应 S4 SRS V1.3.0 与 S1 REST 契约 1.19.0
（147 条路径）。

- Python 3.12+、SQLAlchemy 2.x（同步）、Pydantic v2、Alembic、httpx、PostgreSQL schema `dx`。
- 公共入口：**`factory_dhis2.api`** —— 其他子系统只能导入它（00 文档 6.1、D-13）。
- 测试替身：**`factory_dhis2.testing`** —— `FakeDhis2Api`（内存公共 API，覆盖全部操作）、
  `FakeDhis2Instance` + `FakeDhis2Transport`（内存 DHIS2 语义、真实 httpx 协议，支持多账户、
  脚本化 503/401、异步任务与任务日志）、`Dhis2Harness`（真实 PostgreSQL）。
- 唯一依赖是 S0（`factory_foundation`）。
- **0.1.0 只交付 `Dhis241Profile`**。兼容范围名义上是 v2.40–v2.43，但 2.40/2.42/2.43 的
  Profile 数据与回归尚未交付（M4）；连接或探测这些版本会得到 `DHIS2_VERSION_UNSUPPORTED`。
- **0.1.0 只有 Metadata 执行器启用**。Tracker（M3）、DataStore/App Upload（M5）、Fixture
  导入与演示用户均已注册，但入口一律拒绝 `DX_FEATURE_NOT_ENABLED`，不发出任何请求。

S4 从不：判断 Gate、绕过调用方的 Gate 结论、访问 DHIS2 数据库（只走官方 Web API，总体 SRS 2.9）、
操作未绑定环境、把凭证交给任何其他子系统、调用 S1/S2/S3。

## 模块地图

| 路径 | 内容 | 需求 |
| --- | --- | --- |
| `api.py` | 全部公共函数与类型；按 SRS 7 章分节 | §7 |
| `bootstrap.py` `registries.py` | `MIGRATION_CONFIG`、`initialize()`，产物/审计/错误码/ID 前缀四张目录表 | 00 文档 6.10 |
| `config.py` `deps.py` | 超时 60 秒、退避、异步阈值 500、UID 批量与存在性分批上限、CA bundle 与正文日志开关；transport / clock / sleeper / **TLS 校验**注入槽 | DX-CLI-001/003/004/006 |
| `target.py` `environment/` | 档案 CRUD、补丁叠加、PRODUCTION 规则、时区+星期+时段（含跨午夜）时间窗、紧急锁定、凭证引用与到期、`EnvironmentTarget` 装配 | DX-ENV-001…010 |
| `client/` | 逻辑资源解析、按范围即时解析凭证、只读重试、脱敏、TLS、OTel span 与独立指标；运行期目录取自该环境产物 | DX-CLI-001…008 |
| `profiles/` | 版本能力 Profile 的数据文件与自动装载注册表；0.1.0 只有 `data/2.41/` | DX-CAP-001/002/007 |
| `capability/` | 探测（Schema 默认同步**全目录**类型）、与版本 Profile 合成 CAPABILITY_PROFILE（按类型合并，不整体替换基线；描述器覆盖在此生效）、新鲜度与过期标记、最新索引的只读查询、运行期换成该环境产物里那一份目录 | DX-CAP-001/003…010、DX-CLI-005 |
| `connection/` | 只读连接测试矩阵、**按凭证范围各自**的实际/缺失权限、元数据导入的 `access.manage` 只读证据、成功才触发探测、结果持久化与回读 | DX-CON-001…007 |
| `discovery/` | 范围解析（UID 与 code 分开、code 前缀本地复核、Program 限制）、规范化与排除易变字段的内容哈希、组织层级（上限 5,000 节点）、default 四件套、结果列表与对象检索、最近发现 ID | DX-DSC-001…012 |
| `queries/` | UID 批量生成与形态校验、存在性（identifiableObjects + 目录扫描）、关联数据判定（保守方向） | DX-QRY-001…006 |
| `payload/` | 解开 S2 的 METADATA_PACKAGE、manifest 段访问、哈希与 projectId 一致性 | DX-EXE-003/005/006 |
| `execution/` | 计划、漂移、Snapshot、VALIDATE、COMMIT、异步任务与**崩溃后续跑原任务**、对账、人工核实、部署后验证、维护、删除、报告、执行器顺序守卫 | DX-EXE-001…050 |
| `envlock/` | 环境级提交锁：一环境一行、跨项目、不超时释放、待人工核实时保持 | DX-EXE-030/031 |
| `executors/` | 统一 `plan/validate/commit/verify`；0.1.0 只有 Metadata 启用，其余三个注册但拒绝 | DX-EXE-001/002 |
| `diff/` | 提交前后对比、两环境部署状态对比、DIFF_REPORT | DX-DIFF-001/002 |
| `contract/` | 对外发布的 CAPABILITY_PROFILE / DISCOVERY_RESULT 内容格式，模型与 JSON Schema 经 `api.content_format_schema` 导出 | DX-CAP-008、DX-DSC-009、§1.2-11 |
| `cleanup/` | 项目数据清理（只删发现索引，执行、步骤、验证、锁与 Snapshot 索引留下） | DX-DATA-02 |
| `dto/` `models/` `migrations/` | 公共 DTO、`dx` ORM 表与 Alembic 迁移（0001 初始 + 保留/锁触发器，0002 补三列） | §5、DX-DATA-01/03 |
| `testing/` | Fake API、内存实例与 transport（含按凭证区分的多账户）、PostgreSQL harness、契约包装配、连接测试前置记录 | DX-API-001、DX-TST-006 |

## 公共面与在线/离线边界

`factory_dhis2.api.__all__` 是冻结的跨仓库接口（S1 用 `getattr` 按名字解析，S2 按自己的
`ReadonlyDhis2` 协议调用四个只读函数）。0.1.0 的公共操作共 38 个，按边界分三类：

| 类别 | 函数 | 对 DHIS2 的调用 |
| --- | --- | --- |
| 环境生命周期 | `create_environment` / `update_environment` / `disable_environment` / `get_environment` / `list_environments` / `get_protection` / `set_emergency_lock` / `list_expiring_credentials` | 0（事务内或只读 `dx`） |
| 离线/事务内（0 次 DHIS2） | `get_last_connection_test`（别名 `last_connection_check`）、`get_capability_profile_index`、`latest_discovery_result`、`list_discovery_results`、`get_execution`、`get_execution_by_key`、`get_execution_status`、`resolve_manual_check`、`delete_project_data`、`content_format_schema`、`diff_environments` | 0；`search_discovery_objects` 也 0，但它读产物字节，故仍标 `@external_io` |
| 在线操作 | `test_connection`、`check_capability_freshness`、`probe_capabilities`、`discover`、`generate_uids`、`uid_exists`、`code_exists`、`has_associated_data`、`prepare_and_validate`、`check_drift`、`commit`、`reconcile`、`post_verify`、`run_maintenance`、`delete_objects`、`diff_snapshot_vs_current`（仅当同一执行下已有 `POST_COMMIT` Snapshot 时可离线；部署后验证的 Snapshot 挂在 POST_VERIFY 执行上，通常走现场读回）、`import_fixtures`（当前直接拒绝，不发请求） | 至少 1 次 GET/写请求；`import_fixtures` 例外 |

- `MIGRATION_CONFIG` / `DX_MIGRATION_SCRIPT_LOCATION` / `initialize` 是启动面；`External I/O`
  标记与 S1 的 `EXTERNAL_IO_METHODS` 对齐，`tests/unit/test_architecture.py` 扫源码锁定。
- 离线视图存在的意义是“聚合视图不碰实例”：S1 的 `GET /environments/{key}/capability-profile`
  目前仍走 `check_capability_freshness`（在线），这是接线差异，不是 S4 没有离线索引。
- `diff_snapshot_vs_current` 在**同一执行**下找不到 `POST_COMMIT` Snapshot 时当场用 discovery 凭据回读；
  没有基准且没有可回读载荷时返回 `diffReportArtifactId=null` 与原因，不产出空报告。

## 三条链路共用同一套代码

接缝在 **`httpx.BaseTransport`**，而不是 mock 掉 Client：

1. `Dhis2Client` 始终用注入的 transport 构造 `httpx.Client`；URL 由 Capability Profile 的
   逻辑资源目录生成，认证头按凭证范围即时解析，重试、退避、脱敏、correlationId、OTel span、
   分页与状态码→错误映射全部在这一层完成。
2. 那一份目录是**该环境探测确认过的**产物，不是代码里打包的版本 Profile：装配目标后由
   `capability/runtime_profile.apply_environment_capability()` 在事务外换成产物内容（进程内
   有界缓存），所以实例不暴露的端点会被明确拒为"这个环境不支持"，同步回来的字段清单也
   真的用得上（DX-CLI-005、DX-CAP-003）。
3. `testing/transport.py` 用真实 HTTP 语义（状态码、`pager`、ImportReport、task 资源）
   伺服 `testing/instance.py` 的内存实例，可预设版本、对象、导入结果、漂移、超时与“结果不明”。
4. `tests/contract/` 不装 transport → 真实网络。

因此执行管线与执行器对三种模式**零分支**，DX-NFR-07（新增 DHIS2 版本只需新增 Profile）
与 DX-CAP-007（每个 Profile 都要回归）才有可能成立。

## 测试替身

`factory_dhis2.testing` 输出四个名字，分工是刻意的：

| 替身 | 是什么 | 适合测什么 |
| --- | --- | --- |
| `FakeDhis2Api` | 内存公共 API，覆盖除 `initialize` 与标记函数外的全部操作；可 `add_environment` / `add_object` / `queue_validate` / `queue_commit` / `set_drift` / `mark_stale`，并记录 `calls` | S1/S2 的业务编排、幂等键、锁定、离线视图；`content_format_schema` 直接复用真实实现，`import_fixtures` 可在非 PRODUCTION 环境演练 M3 路径 |
| `FakeDhis2Instance` | 可脚本化的 2.41 实例：`version`、`objects`、`sharing`、多账户 `accounts`、`import_reports`、`async_job_ids`/`tasks`、`scripted`（按路径片段返回 503/401 若干次） | 只替换网络；真实 `Dhis2Client`、Profile 解析、重试、脱敏、任务轮询全部照跑 |
| `FakeDhis2Transport` | `httpx.BaseTransport`，把内存实例接到真实 HTTP 语义上（JSON/HTML Content-Type、pager、绝对 `Location`、任务日志） | 客户端行为、分页终止条件、409/415/200 异步语义 |
| `Dhis2Harness` | 独占 PostgreSQL，`reset()` 用真实迁移重建 `core` 与 `dx`；默认库 `factory_test_s4` | 事务/锁/迁移/执行持久化/产物登记 |

`tests/contract/` 不装 transport，直接连真实在线实例；因此同一套执行管线在 Fake API、内存
实例 + 真实 Client、真实实例三种模式下**零分支**。Fake 与真身必须同形：Fake 有而真身没有的
规则（例如过去 `sharing` 顶层 `manage`）不得作为测试依据。

## 确定性怎么保证，而不是怎么承诺

1. 内容哈希一律经 S0 的规范化 JSON，且先排除 Profile 声明的易变字段
   （`profiles/data/2.41/volatile_fields.json`）——仅 `lastUpdated` 变化不报漂移（AC-EXE-07）。
2. 问题 ID 由规则 + 对象 + 消息派生（`dto/issue.py`），同一问题在同一载荷上永远是同一个 ID。
3. Snapshot / Discovery / Plan 文档写产物前按键排序，同一输入得到同一哈希。
4. 执行步骤逐条落库（`dx.execution_step`），幂等续跑是查表而不是推断（DX-EXE-004）。
5. 异步任务引用按实例返回**原样**保存，不重新推导 URL（DX-EXE-034、DX-AC-03）。
6. `tests/unit/test_architecture.py` 扫源码：只有 Client 能用 httpx、外部 I/O 标记集合与
   S1 的 `EXTERNAL_IO_METHODS` 对齐、`dx` 无跨 schema 外键、源码中不含可用凭证。
7. `tests/unit/test_public_surface.py` 读取 S1/S2 的源码逐条比对函数名、位置顺序与返回
   字段名——跨仓库契约的破坏在本仓库就会被抓住。
8. 「这次导入走不走异步」只有一处规则（``execution/plan.async_import_decision``）：计划产物里
   那一份是权威（同一次部署的 VALIDATE 与 COMMIT 必须同一条路），**产物读不到时按对象数与
   环境阈值重算**并标成 ``decidedBy=recomputed:threshold``，而不是退回同步——大载荷同步提交
   会顶穿 HTTP 超时，那正是阈值规则要防的事。重算时只认调用方显式给的 ``async``：合并过
   Profile 默认值的那一份恒带 ``async=false``，拿它当「调用方指定」会让规则彻底失效。
8. "这次导入走不走异步"只有一处规则（`execution/plan.async_import_decision`）：计划产物里
   那一份是权威（同一次部署的 VALIDATE 与 COMMIT 必须同路），**产物读不到时按对象数与环境
   阈值重算**并标成 ``decidedBy=recomputed:threshold``，而不是退回同步——大载荷同步提交会
   顶穿 HTTP 超时，那正是阈值规则要防的事。重算时只看调用方显式给的 ``async``，不看合并过
   Profile 默认值的那一份（默认值恒带 ``async=false``）。

## 安装与测试

```bash
cd S4
uv venv --python 3.12 .venv
VIRTUAL_ENV=.venv uv pip install -e ".[dev]"      # S0 是 editable 路径依赖

.venv/bin/pytest -q tests/unit/client tests/unit/test_architecture.py   # 纯函数与源码扫描，不需要数据库
# 55432 就是 factory-cp-pg 容器；它的 restart policy 是 no，主机重启后它是停止状态，
# 此时套件报的是 setup error 而不是"连不上库"。先 docker start factory-cp-pg。
export FACTORY_TEST_DATABASE_URL="postgresql+psycopg://factory:factory@localhost:55432/factory_test_s4"
.venv/bin/pytest -q --cov=factory_dhis2            # 其余（含请求 harness 的单元测试）需要 PostgreSQL
.venv/bin/ruff check src tests && .venv/bin/mypy
.venv/bin/python scripts/quality_gate.py           # 整体 ≥85%，execution/envlock ≥90%
```

`Dhis2Harness` 会从零重建 `core` 与 `dx`，失败的运行不留残骸；它只动
`FACTORY_TEST_DATABASE_URL` 指向的库。因为它会 DROP 并重建 schema，**两个测试会话不能共用
同一个库**——S4 的默认库因此是 `factory_test_s4`。

### 在线契约测试（DX-NFR-05）

```bash
export FACTORY_DX_LIVE_DHIS2_BASE_URL="https://play.im.dhis2.org/stable-2-41-10"
export FACTORY_DX_LIVE_DHIS2_USERNAME=admin FACTORY_DX_LIVE_DHIS2_PASSWORD=district
export FACTORY_DX_LIVE_ENABLED=1
.venv/bin/pytest -q -m contract tests/contract
```

默认只跑**只读 + `importMode=VALIDATE`**（AC-EXE-01：不产生任何环境变化）。写入类断言额外
需要 `FACTORY_DX_LIVE_ALLOW_COMMITS=1`：目标是一个公开共享实例，未经许可的写入会影响其他
使用者。写入对象一律带 `factory_contract_` 前缀的 code，便于在实例里认出是谁写的。

## 实测得到的 2.41 事实（写进 Profile，不写进代码）

| 探测项 | 结果 |
| --- | --- |
| `GET /api/system/info.json` | 200，`version=2.41.10` → 版本识别用 `system.info` |
| `GET /api/about.json` | **404** → 不得依赖 `about` |
| `GET /api/system/id?limit=5` | 200 `{"codes":[…]}` → `system.id` 的真实端点 |
| `GET /api/reserved/ids`、`/api/reserved/id` | **404** → 旧文档里的 UID 生成端点在本实例不可用 |
| `GET /api/dataElements.json?fields=…&pageSize=2` | 200，带 `pager{page,pageSize,pageCount,total,nextPage}` |
| `GET /api/schemas/dataElement.json` | 200，属性式 Schema |
| `GET /api/me.json?fields=…authorities…` | 200 → 连接测试的权限/角色/组织范围检查；不带 `fields` 时**不返回** `authorities`/`userRoles`，且 `authorities` 是**字符串列表** |
| `GET /api/identifiableObjects.json?criterion=…` | 200（接口存在），但**对确实存在的 UID 也返回空集合** → `uid_exists` 不信它，回退到按目录逐类 `filter=id:in:[…]` 扫描并记录 `basis` |
| `GET /api/{type}.json?…&totalPages=true` | `pager` 只有 `{page,pageSize,total,pageCount,nextPage}`，**没有** `pages`/`isLast` → 翻页终止条件按 `pageCount` |
| `POST /api/metadata.json` 不带 `Content-Type` | **415** → Client 必须自己带 `application/json`（用 `content=` 传字节时 httpx 不会自动补） |
| `POST /api/metadata.json?async=true` | **200**（不是 202），`Location` 指向**任务类型集合** `…/system/tasks/METADATA_IMPORT`，任务 ID 只在响应体 `response.id` 里 |
| `GET /api/system/tasks/METADATA_IMPORT/{jobId}` | 返回**日志条目数组**；终态是一条 `completed=true` 的条目，结论与计数在其 `message` 文本里 |
| `…?importReportMode=FULL` | 不改变上面这一点：实例对异步任务**不提供**逐对象 ImportReport |
| 目录里 `metadata.import` / `metadata.delete` | 是操作端点不是类型；当成类型去扫会得到 `GET /api/metadata.json?filter=id:in:[…]` 这种 200 但无意义的响应 |
| `POST /api/metadata?importMode=VALIDATE` 校验不通过 | **HTTP 409** + 完整报告；顶层 `status=WARNING`，真正的结论在 `response.status=ERROR`。Client 因此对 409 **不抛异常**，否则逐对象错误全丢 |
| 同步 ImportReport 形状 | `{"httpStatus","httpStatusCode","status","message","response":{"responseType":"ImportReport","status","stats","typeReports":[{"klass","stats","objectReports":[{"uid","displayName","errorReports":[{"message","errorCode","mainId"}]}]}]}}`；对象条目**没有** `status` 字段，有 `errorReports` 就是被拒 |
| `identifier=ASSIGNED` + 新 UID 的 CREATE | **成功**（`created=1`）。S2 的 UID 本来就取自 `system/id`，这是元数据部署的正常路径 |
| `GET /api/system/tasks/METADATA_IMPORT/{jobId}` 的日志级别 | 失败时是 `level=ERROR`，成功时是 `level=INFO` 且消息写 `status OK`；带 `errorReports` 的对象按 BLOCKING 处理，不按警告 |
| `GET /api/dataElements/{uid}.json`（点名 `fields` 或 `:all` 都一样） | 取值**等于默认值**的字段根本不出现在 JSON 里（实测 `zeroStorageType=value_if_zero` 被省略）。部署后验证与对账因此共用 `property_defaults.json` 判定“省略 ≠ 没生效” |
| `GET /api/schemas/{type}.json` 全目录扫一遍 | 34 类里 **30 类**答得出、4 类 404：`organizationUnit`（目录里 `organisationUnit` 的美式拼写别名）、`trackedEntityDataElementGroup`、`trackedEntityOptionSet`、`validationNotification`——后三类在 DHIS2 里不是对象类型而是别的类型的字段名。探测把这 4 类记进 `probed.unavailableResources`，运行期目录随之不再对外声称支持它们（DX-CLI-005 的负事实） |

异步导入这两行直接决定了 `execution/jobs.py` 的写法：任务引用必须**同时**保存 ID 与类型
URL（合成成 job 级地址再轮询），而报告是从任务日志**合成**的——状态与计数取自终态条目，
全部条目进 `detailedMessages`，并带 `reportSource=TASK_LOG` 标记，免得有人把它当成同步
ImportReport 用。这是 00 文档 0.2 权威顺序第 1、2 条（实例与官方接口优先于我们的文档）在本子系统
的具体落点，由 `test_async_import_exposes_a_task_log_and_s4_synthesizes_the_report` 钉住。

这组事实正是契约测试的价值：内存假实例是我们自己写的，它永远不会告诉我们某个端点其实不存在。

* **`list_discovery_results` 用 `limit`/`offset`。** S4 SRS V1.3.0 已把这处对齐实现（不再写
  `page`），并与 `search_discovery_objects` 保持同一套分页词汇；`total` 不进返回值，因为该
  函数的输出在 SRS 里就是“索引列表”，总数在检索函数上给。
* **`get_last_connection_test` 是正式名称，`last_connection_check` 是兼容别名。** S1 契约
  1.19.0 的 `ExecutionAdapter.METHOD_MAP` 已使用正式名称（`adapters/registry.py`），别名只为
  不破坏旧调用方而保留；新代码应使用正式名。
* **`DX_MANUAL_CHECK_PENDING` 已删除。** DX-AC-04 逐字要求待人工核实期间的第二个提交返回
  `CONCURRENCY_ERROR`；另造一个调用方没登记的码，S1 会把它按 500 报出去。更具体的原因放在
  `details.reason = "MANUAL_CHECK_PENDING"`。

### SRS V1.3.0 / S1 契约 1.19.0 的落点

| 需求 | 实现 | 证明 |
| --- | --- | --- |
| DX-CON-005 最近一次连接测试的只读查询 | `api.get_last_connection_test` → `connection/tester.latest_connection_test`；另有 `last_connection_check` 别名 | `tests/integration/test_readonly_views.py` |
| DX-CAP-009 最新能力索引的只读查询 | `api.get_capability_profile_index` → `capability/service.latest_index` | 同上（含“读它不打实例”） |
| DX-DSC-010/011 发现列表与对象检索 | `list_discovery_results`（scope 过滤下推 SQL、补 `dhis2_version`、`limit/offset`）、`search_discovery_objects`、`latest_discovery_result` | 同上 + `test_search_over_five_thousand_objects_is_paged` |
| DX-EXE-027 / DX-AC-14 级别字段 `level` | `dto/issue.Issue.level`（不再接受 `severity`），并按 BP-VAL-001 补齐 `dhis2Type`/`location`/`details` | `test_standardized_issues_use_the_unified_level_field` |
| DX-AC-11 多入口零 DHIS2 调用 | 上述函数 + `get_execution*` + `diff_environments` 全部只读 `dx`/产物 | `test_the_four_readonly_queries_call_dhis2_zero_times` 等 |
| DX-EXE-038/043/044 定向删除 | `execution/deletion.py`：候选集 → Snapshot → VALIDATE → 关联数据复查 → `metadata.delete`；S1 1.9.0 起有 `POST /environments/{key}/delete-objects` | `tests/unit/execution/test_deletion.py` |
| DX-DIFF-001/002 环境侧 Diff | 有 POST_COMMIT 时离线比较；证据不足返回 `null` 与原因；已清除产物按错误呈现 | `tests/unit/diff/`、`test_...` |
| DX-CAP-008 / DX-DSC-009 内容格式 | `api.content_format_schema` 导出 CAPABILITY_PROFILE / DISCOVERY_RESULT 的 JSON Schema 与模型 | `test_s4_publishes_the_content_formats_through_the_public_api` |
| DX-CLI-007 DHIS2 指标与核心 API 分离 | `client/tracing.py`：meter `factory.dhis2` 上的 `dhis2.call.duration`（ms 直方图）与 `dhis2.call.count`，外加进程内累加器 | `tests/unit/client/test_client_behaviour.py` 的 otel 一节（SDK 内存 reader 真实采集） |

**DX-CLI-007 的两个设计取舍。** ① 分离是**结构性**的：仪器只在 `factory.dhis2` 这个 meter
上创建，运行期用 `tracing.configure_meter_provider()` 把 provider 交给应用（默认取全局），
所以实例慢到 60 秒也不会进"Factory API 变慢"那条曲线——测试用同一个 provider 上另开的
`factory.control` meter 证明两边的数据点各归各处。② 维度只有 SRS 点名的三项（环境、逻辑
资源、状态码）：重试**不**放大调用计数（尝试次数在 span 属性上），也没有 projectId、URL、
凭证——前两者是高基数，后者压根不该出现在观测数据里（DX-CLI-002）。
没装 `opentelemetry` 时 span 为 no-op、仪器为 `None`，但累加器照样记，因此这几个用例只是
跳过而不是失败；`[dev]` extra 已包含 OTel，正常开发环境会真的走仪器路径。

**对调用方的唯一行为影响**：DX-EXE-012 要求「计划中每个操作对应凭证范围的服务账户具备所需
权限」，而这份事实在系统里唯一的来源是已持久化的连接测试结果（DX-CON-004）。因此**环境从没
做过连接测试时，`prepare_and_validate` 的计划会以 BLOCKING 不通过**（以前是「没有缺失记录」
就放行）。S1 侧的正常流程本就包含登记后的连接测试（SRS 6.1 第 2 步），影响面是新登记的
环境必须先 `test_connection(required_scopes=["import", …])` 才能进 R3。

## 日志与 TLS：安全是默认，观察是显式选择

* **DX-CLI-006**：要求 TLS 的环境把 `verify` 换成一个强制校验的 `SSLContext`（`tls.py`），
  证书无效就在握手阶段失败——`tests/unit/client/test_tls_verification.py` 起一台真的本地
  HTTPS 服务（自签名证书由 `openssl` 现生成）来证明这件事，因为 mock 的 transport 根本不
  经过 TLS 层。证书问题**不重试**（DX-CLI-003 列的重试条件里没有 TLS），部署方要接企业
  PKI/mTLS 时用 `deps.set_tls_adapter_factory()` 交出校验配置。
  上下文按 `ca_bundle` **缓存**：`ssl.create_default_context()` 实测 40–66 毫秒（装载系统
  信任库），不缓存的话这笔代价会摊到**每一个** DHIS2 请求上——一次探测（34 类 Schema +
  二十几个端点体检）光建上下文就要十几秒。实测一个测试子集：967 次取校验配置只建了 48 个
  上下文（每测试一个，`deps.reset_providers()` 清缓存），同样的子集从 53.8 秒回到 12.7 秒。
* **DX-CLI-004**：正文默认不进日志，且这个开关不在日志级别上而在 `FACTORY_DX_LOG_BODIES`
  上——"debug 模式就把请求响应全打出来"对可能含个人健康数据的载荷不是可接受的默认。开了
  之后依次过 `redact_headers`（认证头只留机制名）、`redact_structure`（敏感键名与本进程
  解析过的凭证值）和 formatter 的第三层脱敏，最后**先脱敏再截断**（反了会把
  `***REDACTED***` 截成 `***REDAC`）。
* 结构化字段必须嵌在 `extra={"extra_fields": {…}}` 里：S0 的 formatter 只并这个字典，平铺
  进 `extra=` 的键会设到 LogRecord 属性上但**不出现在日志行里**（有测试锁住这点）。

## 与 SRS API 表的偏离

* **异步导入的 DHIS2_IMPORT_REPORT 是合成件。** SRS DX-EXE-027/033 假定“任务跑完就能拿到
  ImportReport”，而 2.41.10 实测不提供：任务查询只有日志条目。S4 按实例事实实现——状态与
  计数取自那条 `completed=true` 的条目，全部条目存进 `detailedMessages`，产物里带
  `reportSource=TASK_LOG` 标记。**没有**逐对象明细时不假装拥有，也不把结论说成成功。
* **`executors/` 是统一接口（DX-EXE-001）的一层薄壳，不是第二条管线。**
  `MetadataExecutor` 的 `plan/validate/commit/verify` 各自委派给 `execution/` 里已实现并测过的
  那一份——`commit` 因此重走 `execution.commit.commit` 的准入与幂等路径，而不是另写一遍提交
  逻辑；Tracker / DataStore / App Upload 与演示用户在被调用时抛 `DX_FEATURE_NOT_ENABLED` 并点名
  里程碑，且**一次外部调用都不发**（DX-EXE-002、SRS 12 章）。

* **`uid_exists` / `code_exists` / `has_associated_data` 的返回类型。** SRS 7.3 要求带上占用
  对象的类型与 code（DX-QRY-002），而 S2 的既有调用形态是 `dict(...)` 后按真值判断
  （`factory_blueprint/s4/adapter.py`）。解决方式是返回 `dict` 的**值子类**：
  `bool(x) == x["exists"]`，同时携带 `dhis2Type`/`code`/`uid`。S2 的用法逐字节不变，
  SRS 要求的信息也在。该等价性由测试断言（`tests/unit/testing/test_fakes.py`）。
* **VALIDATE 可以不声明期望哈希。** DX-EXE-005 的“必须声明并核对期望哈希”约束的是**提交类**
  函数（COMMIT / DELETE / FIXTURE）。`prepare_and_validate` 允许 `expected_hash=None`——S1 在
  PACKAGE_GATE 之前手上可能只有产物 ID——此时取产物自身哈希并写回结果，供 S1 在 COMMIT 时
  报回来；一旦声明就必须一致（AC-EXE-02），不一致就是 `DX_PAYLOAD_HASH_MISMATCH`。
* **两份状态都要真拿得到才产出 Diff。** `diff_snapshot_vs_current` 既没有 POST_COMMIT Snapshot
  又没有可回读的载荷产物时，报「无法对比」并给原因，**不产出** DIFF_REPORT：把空集合当成
  “现在的环境”会产出一份“所有对象都不见了”的报告，那是把“我没法知道”说成一次大规模删除。
  S4 SRS V1.3.0 的 DX-DIFF-001 已按此语义写明。另：POST_COMMIT 只在传入的执行 ID 下查找，
  而部署后验证把它挂在 POST_VERIFY 执行上，因此对 COMMIT 执行调用通常走现场读回；这是
  §13 第 5 条记录的未决接线问题。
* **`probe_capabilities` 返回产物 ID 字符串**，而不是模型：S1 的 `METHOD_MAP` 为它配的是
  `None` DTO 并直接把返回值当 ID 用（`factory_control/adapters/registry.py`、`services/runs.py:308`）。
  S4 SRS V1.3.0 的 7.2 已按“产物 ID 字符串”描述。
* **`Dhis2ValidationOutcome.steps`。** SRS 7.4 未列该字段，但 DX-EXE-004 要求“按幂等键查询
  步骤状态”，同一载荷的 R3 结果里带上步骤清单能省掉调用方再一次查询。S1 的镜像
  `extra="ignore"`，多给字段不影响它。
* **`delete_project_data` 不写 S4 的审计事件。** 附录 A 没有 `dx.project.data_deleted`，
  项目删除本身由 S1 审计（附录 A 是事件清单的权威）。
* **`DeletionPolicy` 取值。** S4 SRS V1.3.0 已枚举为 `FORBID` / `SAFE_ONLY`（PRODUCTION 默认
  `FORBID`）；删除一律要求通过 DX-QRY-004 的关联数据检查。这里保留实现说明，是因为取值是
  【新增】的可执行化裁决，不是上位文档原有的枚举。
* **Schema 包的权威来源。** `profiles/data/2.41/schemas.json` 只带离线基线
  （`controlableFields`、`forbidden`）；`required` 与 `properties` 由 `capability/probe`
  从实例 `/api/schemas` 同步后进 CAPABILITY_PROFILE 产物——00 文档 0.2 权威顺序第 1、2 条要求以
  实例为准，S2 读的永远是合成后的产物（DX-CAP-003：禁止运行时猜测）。同步范围默认是
  **目录里的每一类**（`default_schema_types`），不是曾经那"核心 10 类"：产物里没有的类型
  S2 只能当"没有字段清单"，而收窄（`schema_types=`）必须是调用方的显式选择。
  合并是**按类型**的：同步范围之外的类型保留基线，实例答不出的字段（`forbidden`、
  `controlableFields` 这些"项目/版本决定"）不会被同步结果清空。
* **类型描述器覆盖生效的位置。** 覆盖（`descriptor_overrides.json`）压过 Schema 包：
  `required`/`forbidden`/`controlableFields` 整项替换（出现了就替换，空列表也是），
  `properties` 按键合并。它作用在 `VersionProfile.object_schema` 这个唯一出口上，并且
  **在产物里就生效**——S2 只读产物，覆盖如果留在 S4 代码里等于没生效。优先级是
  覆盖 > 实例同步 > 离线基线。
* **未交付的能力按里程碑分别拒绝。** `import_fixtures`、Tracker/DataStore/App Upload 执行器、
  演示用户抛 `DX_FEATURE_NOT_ENABLED`（M3/M5）；`run_maintenance` 里不受支持的动作抛
  `DX_ENVIRONMENT_INVALID`（`CLEAR_APP_CACHE` 之外的 MVP 拒绝，见 SRS DX-EXE-013）。
  **`Dhis240Profile`、`Dhis242Profile`、`Dhis243Profile` 都没有随 0.1.0 发布**，连接/探测会报
  `DHIS2_VERSION_UNSUPPORTED`；它们不是“导出后拒绝”的函数，而是缺失的数据目录。扩展点已经就位：
  启用它们需要补 Profile 数据与实现，不是重构。
* **`get_capability_profile_index`、`latest_discovery_result`、`get_execution_status`、
  `run_maintenance`、`diff_snapshot_vs_current`、`diff_environments`、`content_format_schema`
  尚未进入 S1 的 `ExecutionAdapter.METHOD_MAP`。** 它们在 S4 可用并有测试，但还不能经 S1 的
  REST 面到达；S1 的 `GET /environments/{key}/capability-profile` 目前走 `check_capability_freshness`
  （会访问实例），而不是离线的索引查询。收敛方式是 S1 补映射，不是 S4 删除公共函数。
* **`get_protection` 的 `dataExportPolicy` 形态。** S1 只当不透明 dict 用并参与 Gate 指纹
  （`services/gates.py:219-237`），因此这里的取值固定为
  `{llmDataPolicy, rank, environmentClass, ...}` 且键序稳定——指纹抖动会让每次 DEPLOY_GATE
  审批看起来都是陈旧的。

### 集成过程中发现的三处约束冲突（已按上述方式解决）

1. **产物写入必须拆成“事务外暂存 + 事务内登记”。** D-11 要求状态与审计同事务，00 文档 6.2
   又禁止在事务内做外部 I/O；两者在“生成产物并把 ID 写进执行行”这一步正面相遇。所以每个
   `save_*` 助手都是 `stage_document()` 后在自己的短事务里 `register_staged(session=…)`，
   而不是 `stage_and_register_json(..., session=…)` —— 后者会在 S0 的守卫下直接抛
   `TRANSACTION_MISUSE`，是运行期错误而不是风格问题。
2. **manifest 的 `hashes` 段在 S2 的实现里叫 `payloadFiles`。** S4 按产物实际形状读取
   （`payload/package.REQUIRED_MANIFEST_SECTIONS`）：权威顺序第 3 条把“已批准的产物”排在
   文档字面之前。
3. **`factory_dhis2.execution` 不重导出 `commit` 与 `validate`。** 包属性与同名子模块冲突时，
   `factory_dhis2.execution.commit` 解析出来是函数而不是模块，`import … as module`、
   monkeypatch、reload 全部静默失效。调用方走完整路径。

## 验收标准与证明位置

| 标准 | 测试（全部为仓库里真实存在的名字） |
| --- | --- |
| AC-VAL-02 — ImportReport 保存、错误映射回 businessKey 与来源阶段 | `tests/unit/execution/test_importreport.py`（`test_a_report_with_errors_does_not_pass` 等 14 条） |
| AC-EXE-01 — VALIDATE 不改变环境 | `test_prepare_and_validate_produces_plan_snapshot_and_report`；契约 `test_validate_changes_nothing`、`test_async_import_exposes_a_task_log_and_s4_synthesizes_the_report` |
| AC-EXE-02 — 哈希与最近一次成功 VALIDATE 不一致则拒绝提交 | `test_commit_is_refused_without_a_matching_validate`、`test_a_package_that_is_not_the_approved_content_is_refused` |
| AC-EXE-03 — VALIDATE 后 REUSE 对象被改 → `DRIFT_DETECTED` 与清单 | `test_drift_between_validate_and_commit_blocks_the_commit`、`test_a_changed_content_hash_is_drift_with_both_hashes_as_evidence` |
| AC-EXE-04 — 同环境并发提交 → 一个执行、一个 `CONCURRENCY_ERROR` | `test_second_committing_execution_is_refused`、`test_two_sessions_racing_for_one_environment_are_serialised_by_the_advisory_lock` |
| AC-EXE-05 — 提交中断后按幂等键查询；无任务 ID 时 NEEDS_MANUAL_CHECK + 对账 | `test_a_repeated_call_with_the_same_idempotency_key_returns_the_same_execution`、`tests/unit/execution/test_reconcile.py`、`test_an_unconfirmed_maintenance_replays_the_same_key_without_a_second_request` |
| AC-EXE-06 — 提交后逐对象比对受控字段、引用与共享 | `test_update_object_mismatch_is_detected_on_controlled_field`；契约 `test_commit_then_post_verify` |
| AC-EXE-07 — 易变字段不参与状态哈希 | `test_a_volatile_only_change_does_not_count_as_drift`、`test_the_state_hash_ignores_a_volatile_only_change`、`test_volatile_fields_exclude_everything_unstable` |
| AC-CAP-01（S4 部分）/ AC-ENV-01 | `tests/contract/test_dhis2_241_live.py`（2.41 全链）、`tests/integration/test_flows_over_fake_instance.py` |
| DX-AC-01 — 缺权限时报告缺失项且不写入实例 | `tests/integration/test_connection_scoped_authorities.py`（`test_the_failed_import_probe_wrote_nothing_to_the_instance` 断言连接测试**全程只有 GET**；`test_an_import_account_without_manage_fails_and_names_the_missing_right` 给出缺什么）。Fake 侧的对应脚本是 `test_test_connection_reports_missing_authorities` |
| DX-AC-02 — PRODUCTION 共用 Secret 被拒绝保存 | `test_production_environment_sharing_one_secret_is_refused_on_save` |
| DX-AC-03 — 崩溃后同幂等键继续轮询原任务 | `test_a_crash_during_polling_resumes_the_task_instead_of_resubmitting`（重入不产生第二次 `metadata.import`，只问那一个已存任务）、`test_a_resumed_commit_that_still_cannot_be_confirmed_keeps_the_lock`；先落库半边是 `test_async_submit_persists_task_reference_before_any_polling` |
| DX-AC-04 — 待人工核实期间另一项目提交 → `CONCURRENCY_ERROR` | `test_a_holder_awaiting_manual_check_is_reported_as_pending_not_merged`（代码逐字是 `CONCURRENCY_ERROR`，原因在 `details.reason=MANUAL_CHECK_PENDING`）、`test_second_committing_execution_is_refused` |
| DX-AC-05 — 紧急锁定时提交 → `ENVIRONMENT_LOCKED` | `test_emergency_lock_blocks_commit`、`test_reject_if_locked_refuses_a_committing_operation` |
| DX-AC-06 — 实例升级后新鲜度返回“版本已变化”并标记过期 | `test_freshness_is_valid_right_after_probe_and_expires_on_version_change`、`test_capability_profile_index_reports_staleness_without_probing` |
| DX-AC-08 — 生成 500 个 UID 全部合法且互不相同 | `test_generated_uids_are_valid_and_distinct`（在线契约）、`test_generate_uids_respects_the_default_batch_limit_of_one_hundred` |
| DX-AC-09 — 对 PRODUCTION 导入 Fixture 被拒绝 | `test_import_fixtures_is_refused_on_production_but_rehearsable_on_dev`（M3 落地后由真实实现接管） |
| DX-AC-10 — 延后校验表达式在部署后验证失败但不修改实例 | `test_deferred_expression_failure_produces_issue_and_no_write` |
| DX-AC-11 — 多个只读入口返回结果且 DHIS2 调用次数为 0 | `test_the_four_readonly_queries_call_dhis2_zero_times`、`test_never_tested_environment_has_no_row`（`tests/integration/test_readonly_views.py`） |
| DX-AC-12 — 5,000 对象的发现结果里按类型与关键字检索并分页 | `test_search_over_five_thousand_objects_is_paged` |
| DX-AC-13 — 缺元数据导入权限时逐项齐备、按范围列出缺失、总体失败 | `test_an_import_account_without_manage_fails_and_names_the_missing_right` |
| DX-AC-14 — 标准化问题使用统一 Issue 结构，级别字段是 `level` | `test_standardized_issues_use_the_unified_level_field`、`test_issue_from_error_keeps_the_unified_shape` |
| DX-AC-17 — 进行中/待核实执行阻止改 baseUrl、Secret 引用 | `test_changing_the_base_url_while_an_execution_is_running_is_refused`、`test_changing_secret_references_while_an_execution_is_running_is_refused`、`test_a_pending_manual_check_execution_also_blocks_a_target_change` |
| DX-AC-18 — 从未连接测试的环境不能生成计划 | `test_a_never_connection_tested_environment_cannot_be_planned`、`test_a_connection_test_that_skipped_the_needed_scope_is_not_evidence` |
| DX-AC-19 — Secret 错误不泄露凭证 | `test_error_message_never_contains_the_secret`（`db://` 的 keyMismatch 由 S0/S1 后端产生，S4 只负责按引用报告） |
| DX-AC-20 — 删除策略/关联数据整批拒绝 | `test_one_candidate_with_associated_data_refuses_the_whole_batch`、`test_associated_data_check_is_redone_between_validate_and_commit` |
| DX-AC-22 — 内容格式经公共 API 发布 | `test_s4_publishes_the_content_formats_through_the_public_api` |
| DX-CLI-004 — 正文默认不记录，调试记录也脱敏 | `test_bodies_are_not_logged_by_default`、`test_debug_body_logging_redacts_credentials_and_sensitive_fields`、`test_debug_body_logging_truncates_after_redaction`；`test_structured_log_fields_reach_the_log_line` 锁住 S0 formatter 的 `extra_fields` 约定 |
| DX-CLI-005 — 端点与字段目录来自环境的产物 | `tests/unit/capability/test_runtime_profile.py`（往返、负事实删除、按类型合并）、`test_the_runtime_catalogue_is_the_probed_one_not_the_bundled_one`（假实例）、契约 `test_the_runtime_catalogue_is_the_persisted_one_not_the_bundled_baseline`（真实例：`dataElement.properties` 只有产物里才有） |
| DX-CLI-006 — 证书无效时请求失败 | `tests/unit/client/test_tls_verification.py`：真起一台自签名 HTTPS 实例，断言失败、**不重试**、注入的适配器才是说话的那个、CA bundle 能救回来 |
| DX-CLI-007 — 每次调用产生 span 与独立指标 | `test_span_and_metric_are_recorded_per_call` 加 otel 一节 5 条（SDK 内存 reader 真实采集：数据点、三项维度、500 可见、重试不放大、与核心 API 的 meter 互不掺） |
| DX-CAP-001 — Schema 包覆盖全目录、描述器覆盖生效 | `tests/unit/capability/test_schema_sync_scope.py`（默认范围、真实同步、可收窄、答不出的记成负事实）、`tests/unit/test_descriptor_overrides.py`（覆盖压过包、进产物、往返幂等）、`test_the_probe_syncs_the_whole_catalogue_into_the_artifact`、契约 `test_the_schema_pack_covers_the_whole_catalogue` |
| DX-EXE-033 — 计划产物读不到时重算异步 | `test_a_missing_plan_document_recomputes_the_async_decision`、`test_a_recorded_decision_wins_even_when_the_count_says_otherwise`、`test_recomputing_ignores_the_profile_default_async_flag`、`test_an_environment_override_still_applies_when_recomputing` |

追溯靠**docstring 里的编号**而不是函数名（与 S2/S3 同一约定），因此查某条标准的全部证明是：

```bash
grep -rn --include="*.py" "AC-EXE-05" tests      # 该编号出现的所有测试
```

## 加一个 DHIS2 版本需要做什么

新增 `profiles/data/<系列>/`：`profile.json`、`resources.json`、`schemas.json`、
`import_params.json`、`volatile_fields.json`、`deprecated.json`、`removed.json`、
`descriptor_overrides.json`、`validation_rules.json`、`associated_data.json`，外加可选的
`property_defaults.json`（实例回读时会省略的默认值字段）。注册表自动装载，
Client、执行器与管线一行不改（DX-CAP-002、DX-NFR-07）。加一种元数据对象类型同理：
在 `resources.json` 的 `typeEndpoints` 里加一行。
