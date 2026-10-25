# DHIS2 App Studio / Metadata Factory
# S4 DHIS2 集成与执行（DHIS2 Integration & Execution）子系统需求规格说明书

## 0. 文档信息

| 项目 | 内容 |
| --- | --- |
| 子系统 | S4 DHIS2 集成与执行 |
| 文档版本 | V1.3.0（对照 `factory-dhis2` 0.1.0 实现与 S1 契约 1.19.0（147 条路径）反向校准：补齐环境更新/禁用/紧急锁定与保护字段、连接测试与能力探测的在线/离线边界、发现与检索语义、执行准入/幂等/对账/人工核实/删除/维护/Diff、`db://`/`env://` 引用与主密钥不匹配、Profile 支持范围（v2.40–v2.43，0.1.0 仅 `Dhis241Profile`），并如实标注 Tracker/DataStore/App Upload 与 2.40/2.42/2.43 Profile 尚未交付） |
| 上位文档 | 总体 SRS V1.4.0；《00 总体架构与集成约定》V1.3.0（下称“00 文档”） |
| 交付形式 | Python 3.12+ 库（建议包名 `factory_dhis2`），发布到私有 PyPI |
| 依赖 | S0 公共 API |
| 数据库 schema | `dx` |
| 需求编号前缀 | DX- |
| 来源标记 | 【直引】【裁剪】【调整】【新增】，定义见 00 文档 0.3 |

---

## 1. 目的与范围

### 1.1 目的

S4 是系统中唯一访问 DHIS2 实例、唯一解析 DHIS2 凭证的子系统（总体 SRS 4 章“只有执行层可以访问 DHIS2”）。它管理目标环境，探测版本与能力，发现既有元数据，并以受控方式执行 VALIDATE、COMMIT 与部署后验证。S4 不做任何架构或业务判断（总体 SRS 4.4“DHIS2 Client 只负责协议与执行”）。本文档是 S4 开发组的唯一需求输入。

### 1.2 范围内

1. Environment 档案的完整生命周期：登记、更新、禁用、生产保护设置、紧急锁定与凭证到期；
2. DHIS2 Client（认证、超时、重试、脱敏、逻辑资源解析、TLS）；
3. 连接测试与按凭证范围的权限报告；
4. 版本能力 Profile 与 Capability Profile（探测、合成、新鲜度、离线索引）；
5. 元数据发现、发现结果列表与对象检索；
6. 为 S2 提供的只读查询：UID 生成、UID 与 code 存在性、关联数据检查；
7. 执行管线：执行计划、漂移检查、Snapshot、VALIDATE（含服务端表达式校验）、COMMIT、异步任务、对账、人工核实、部署后验证、维护动作、定向删除、执行报告；
8. 执行器：Metadata（M2，0.1.0 唯一启用的执行器）、Tracker Data（M3）、DataStore 与 App Upload（M5）；后三者在本版注册但一律拒绝；
9. 环境级提交锁与执行幂等；
10. 环境侧 Diff：提交前 Snapshot 与提交后状态、同一版本在两个环境中的部署状态；
11. 发布 CapabilityProfile、DiscoveryResult 等内容格式定义（含 `content_format_schema`）；
12. 项目数据清理（只清发现索引，执行记录按保留策略留下）。

### 1.3 范围外

| 不负责 | 所属 |
| --- | --- |
| Gate 判定、审批、阶段推进、是否允许执行的业务判定 | S1 |
| 编译、Blueprint、Registry、Managed Scope 的计算、映射决策、元数据载荷生成 | S2 |
| 模型调用与数据外发判定 | S3 |
| 产物存储、审计链、事务、Secret 后端 | S0 |
| DHIS2 实例数据库备份与恢复 | 实例运维（总体 SRS 2.3） |

S4 禁止：产生架构或业务决策；绕过调用方给出的 Gate 结论自行决定提交；访问 DHIS2 数据库（只能经官方 Web API，总体 SRS 2.9）；对未绑定环境执行任何操作；把凭证交给任何其他子系统；调用 S1、S2、S3。

---

## 2. 领域概念与定义

| 概念 | 定义 |
| --- | --- |
| Environment | 一个目标 DHIS2 实例的档案，以 environmentKey 标识（总体 SRS 14.1） |
| 环境类别 | DEV、TEST、UAT、PRODUCTION |
| 凭证范围 | 按操作区分的服务账户用途：discovery（只读）、import（元数据导入）、tracker、datastore、apps、maintenance（总体 SRS 21.3） |
| 版本能力 Profile | 封装某 DHIS2 版本行为差异的数据与代码，如 `Dhis241Profile` |
| Capability Profile | 版本能力 Profile 与目标实例实际探测结果合成的环境能力记录，以 CAPABILITY_PROFILE 产物持久化 |
| 逻辑资源名 | 与版本无关的 DHIS2 接口名称（如 `metadata.import`、`tracker.events`），由 Capability Profile 解析为实际端点 |
| 发现结果 | 某时刻目标环境既有元数据的索引与内容，以 DISCOVERY_RESULT 产物持久化 |
| 执行（Execution） | 一次针对单一环境的执行管线实例，带幂等键 |
| 执行计划 | 执行前生成的计划：环境、执行器、载荷哈希、导入参数、预期变更、维护动作、表达式校验安排 |
| 漂移（Drift） | 目标环境中 REUSE / REFERENCE / UPDATE 对象在观测基准之后被他人修改 |
| Factory Snapshot | S4 保存的目标环境相关对象状态记录，不是 DHIS2 事务快照或数据库备份（总体 SRS 15.4）；分 `PRE_COMMIT` / `POST_COMMIT` 两种实际产出 |
| 环境提交锁 | 同一环境同一时刻只允许一个提交类执行的独占锁（`pg_advisory_xact_lock` + `dx.environment_lock` 一行一环境） |
| 无法确认（NEEDS_MANUAL_CHECK） | 执行结果无法由系统确定是否已生效的状态；环境锁保持，直到人工核实 |
| 在线操作 | 本次调用会向目标 DHIS2 实例发出至少一个请求；包括连接测试、探测、发现、只读查询、VALIDATE、COMMIT、对账、部署后验证、维护与删除 |
| 离线只读视图 | 只读 `dx` 或产物字节、对目标实例的请求次数为 0 的查询；包括最近连接测试、能力索引、发现列表、执行状态与内容格式。`search_discovery_objects` 与 `diff_environments` 不访问实例，但因读产物字节仍属“外部 I/O” |

---

## 3. 功能需求

### 3.1 Environment 档案（DX-ENV）

- **DX-ENV-001**【调整】（14.1、D-03）Environment 字段：environmentKey、名称、baseUrl、环境类别、版本约束、认证方式（PAT / BASIC）、按凭证范围的 Secret 引用、是否要求 TLS、LLM 数据外发策略（总体 SRS `llmDataPolicy`）、请求超时（默认 60 秒）、状态（ACTIVE / DISABLED）、备注、按环境的异步导入阈值、按凭证范围的凭证记录（范围、Secret 引用、到期日、标签）、创建与更新时间。Environment 不配置 Managed Scope（D-03）。`versionConstraint` 在保存时必填；环境类别默认 TEST、认证方式默认 PAT、`tlsRequired` 默认 true；`get/list` 返回的 `version` 与 `profileVersion` 取自最近一次 Capability Profile 索引，未探测时为空且不访问 DHIS2。
- **DX-ENV-002**【直引】（FR-ENV-001）PRODUCTION 类环境必须额外配置：允许提交的时间窗、删除策略、紧急锁定开关；时间窗按环境时区以“星期 + 时间段”列表表示（如“周二、周四 20:00–23:00”，ISO 星期 1=周一…7=周日），当前时刻落在任一时段内即为允许；支持跨午夜时段（`end <= start`，如 22:00–02:00）；时区默认 UTC；删除策略取值为 `FORBID`（禁止删除）与 `SAFE_ONLY`（只允许通过删除安全检查的对象），非生产环境默认 `SAFE_ONLY`、PRODUCTION 默认 `FORBID`；紧急锁定记录锁定原因、变更时间与变更人。PRODUCTION 未配置时间窗即视为不允许提交；非生产环境未配置时间窗时视为允许。所需审批人数与操作员角色随 M4 权限引入：**尽管 S1 的 M4 认证已交付，S4 0.1.0 的 `_validate_m4_fields` 仍拒绝任何非空值**（`DX_ENVIRONMENT_INVALID`，`details.milestone="M4"`），列在库中恒为空，S1 因此回退到 1 人；这是当前实现的边界，不能表述为“已可配置”。
- **DX-ENV-003**【调整】（FR-ENV-002、FR-SEC-006、CP-SEC-001）Environment 中禁止存放凭证本身，只能存放 Secret 引用：`env://…`（值留在部署环境变量）或 `db://…`（值经认证加密存于 S1 的凭证库，S5IF-SEC01），亦接受 S0 已登记后端的其他 `scheme://` 引用。S4 只保存和转交引用字符串，不解析 scheme；引用由 S0 Secret 适配器在请求时解析，`db://` 后端由 S1 在具备主密钥时注册，主密钥只来自部署环境变量、不入库。引用指向的密文落在 `cp` 中不构成违反——那条记录不是可用的凭证。若 `db://` 记录由另一把主密钥写入，S0 以 `reason=keyMismatch` 报 Secret 不可用，S4 统一转成 `DHIS2_AUTH_ERROR`（消息说明“different master key”，`details` 只含 environmentKey/scope/secretRef），不重试、不回显任何密钥派生值或明文。
- **DX-ENV-004**【直引】（FR-ENV-003）系统不存在默认环境；每个 S4 公共函数凡涉及 DHIS2 操作，都必须显式接收 environmentKey。以执行 ID 为主语的函数（`get_execution*`、`reconcile`、`resolve_manual_check`、`diff_snapshot_vs_current`）从执行行取环境键。`get_environment` 对未知环境返回 `None`，其余按 environmentKey 定位的写函数与只读视图对未知环境报 `DX_ENVIRONMENT_INVALID`。
- **DX-ENV-005**【直引】（FR-SEC-011）PRODUCTION 类环境的 discovery 与 import 必须引用不同的 Secret（不同账户）；创建与更新都按合并后的完整档案校验，不满足即拒绝保存。
- **DX-ENV-006**【调整】（FR-SEC-009）优先使用个人访问令牌（PAT，`Authorization: Bearer`）；BASIC 的 Secret 值形态为 `user:password`。每个 Secret 引用可登记到期日期与标签；`list_expiring_credentials` 返回 **ACTIVE 环境**中 `expiresAt <= now + withinDays` 的记录（默认 14 天，可由 `FACTORY_DX_CREDENTIAL_EXPIRY_WARNING_DAYS` 调整），ACTIVE 环境中已经过期的记录也返回且 `daysLeft=0`；禁用环境的已过期引用由内部的过期提醒单独处理，不进入该预警查询。该查询只读 `dx`，不访问 DHIS2，由 S1 定时调用并通知。
- **DX-ENV-007**【新增】紧急锁定开启后，该环境的 COMMIT、Fixture 导入、维护动作与定向删除全部拒绝（`ENVIRONMENT_LOCKED`），读操作与离线视图不受影响；开关变更写审计。紧急锁定只能通过 `set_emergency_lock` 修改：通过环境档案提交 `emergencyLock=true` 会被拒绝（避免绕过“谁、何时、为何”的记录）。理由：总体 SRS FR-ENV-001 列出开关但未定义效果。
- **DX-ENV-008**【新增】环境配置的每次变更（含 Secret 引用、时间窗与删除策略）写审计；存在进行中执行（`RUNNING`）或待人工核实执行（`NEEDS_MANUAL_CHECK`）的环境不允许修改 baseUrl 与 Secret 引用，其余字段可改。理由：总体 SRS 21.4 要求审计“环境配置变更、凭证引用变更”，并避免执行中途更换目标。
- **DX-ENV-009**【新增】环境是全局对象，可被多个项目登记为目标环境；禁用环境前必须没有进行中的执行，已禁用环境再次禁用会被拒绝。禁用后的环境仍可用于离线只读视图（历史索引、保护设置），但任何需要装配 `EnvironmentTarget` 的 DHIS2 操作都报 `DX_ENVIRONMENT_INVALID`。理由：按 D-03，环境不承载项目信息。
- **DX-ENV-010**【新增】环境更新采用“补丁叠加”：未给出的字段保持不变，`credentials` 给出时整体替换并重新校验必填范围；更新后的 PRODUCTION 规则与 M4 字段限制与创建一致。理由：创建时能过、改一次就绕过的缝隙必须堵死（DX-ENV-005/008）。

### 3.2 DHIS2 Client（DX-CLI）

- **DX-CLI-001**【直引】（FR-IF-006）全部 DHIS2 请求必须经由 DHIS2 Client 发出，统一处理认证、超时（默认 60 秒，可按环境配置）、重试、日志脱敏与 correlationId 传递。只有 `factory_dhis2.client.session` 与 TLS 模块可直接使用 HTTP 客户端；其余模块一律经 Client。
- **DX-CLI-002**【调整】（C-10、FR-SEC-006、FR-SEC-007）每个请求绑定唯一环境；凭证在请求时经 S0 Secret 适配器按凭证范围解析（引用为 `env://…`、`db://…` 或 S0 已登记的其他 scheme 皆同一处解析），解析出的明文只存在于进程内存：S4 不得把它写入任何持久化位置、拼进 URL、放进日志、异常或产物，也不得回传给调用方。`db://` 主密钥不匹配时按 DX-ENV-003 统一报认证失败，不换密钥重试。本条约束的是解析后的明文；其密文由 S1 按 CP-SEC-001 存放，不属于 S4 的写入范围。
- **DX-CLI-003**【调整】（19.2.2）只有读请求与任务状态查询在超时、HTTP 429、502、503、504、网络中断时自动重试（指数退避、默认最多 3 次、可配置）；导入类写请求禁止自动重试，失败后由执行管线按 DX-EXE-035 进入对账/人工核实。TLS 校验失败不重试。
- **DX-CLI-004**【直引】（FR-SEC-008）日志与异常中对 Authorization 头、Cookie、令牌、密码以及本进程解析过的凭证值进行脱敏；请求与响应正文默认不记录，只有显式设置 `FACTORY_DX_LOG_BODIES` 时才在 DEBUG 记录，且先脱敏后截断。结构化字段必须放入 S0 formatter 识别的 `extra_fields`。
- **DX-CLI-005**【直引】（FR-API-001、14.3）按逻辑资源名调用，实际端点、方法、参数名、分页方式由该环境**已持久化的 Capability Profile 产物**解析（不是代码里打包的版本 Profile 基线）；代码中不写死版本相关路径。探测确认实例不提供的资源从运行期目录中移除，调用时报“该环境不支持”。
- **DX-CLI-006**【直引】（2.8、tlsRequired）要求 TLS 的环境必须校验证书；证书无效时请求失败且不重试。企业自签 CA 可经 `FACTORY_DX_TLS_CA_BUNDLE` 提供信任库。
- **DX-CLI-007**【新增】每个 DHIS2 调用产生 OpenTelemetry span 与指标（环境、逻辑资源、状态码、耗时），使用 `factory.dhis2` meter；DHIS2 调用延迟单独监控，不计入核心 API 指标（`factory.control`）。重试不放大调用计数（尝试次数记在 span 属性上），维度不含 projectId、URL 或凭证。
- **DX-CLI-008**【新增】BASIC 认证的 Secret 必须形如 `user:password`，否则在发请求前报 `DHIS2_AUTH_ERROR`；环境未配置某个范围的凭证时，该范围的调用同样在发请求前失败，错误只含范围名与引用。理由：把“没配账户”与“实例拒绝”分开，运维才不会去轮换一个不存在的令牌。

### 3.3 连接测试（DX-CON）

- **DX-CON-001**【直引】（14.2）连接测试结果以固定名称的检查项逐项报告（每项状态为 `PASSED` / `FAILED` / `SKIPPED` 并附说明），并给出总体结论与测试所用的凭证范围。检查项固定为：`address`（地址可达）、`tls`（启用 TLS 时证书有效，否则 SKIPPED）、`auth`（discovery 凭证被接受）、`version`（实例上报版本且满足 `versionConstraint`）、`versionProfile`（存在覆盖该版本的 Profile）、`scope.<scope>`（各凭证范围的只读探针与该账户的授权）、`account`（discovery 账户的角色、授权与组织范围）。`discovery` 总是检查；`import`、`tracker`、`datastore`、`apps`、`maintenance` 只按调用方声明的 `requiredScopes` 检查，未声明的报 SKIPPED 而不是 PASSED。元数据导入权限以对象自身的 `access.manage` 为只读证据。
- **DX-CON-002**【直引】（FR-SEC-010）连接测试必须报告每个凭证范围**实际具备**与**缺少**的权限：`grantedAuthorities` / `missingAuthorities` 按范围分组，`accountRoles` / `accountOrgScope` 给出各账户事实；系统默认不要求超级用户权限。
- **DX-CON-003**【新增】连接测试只能使用只读请求（`GET /api/me`、`GET /api/system/info`、读取对象与 `access`），禁止以试写方式检测权限。理由：总体 SRS FR-DSC-005 的精神扩展到连接测试，测试不得改变目标环境。
- **DX-CON-004**【新增】连接测试结果持久化（时间、各检查项结果、版本、按凭证范围归组的实际/缺失权限、所需范围、总体结论、能力产物 ID、操作者），并写审计 `dx.connection.tested`。同一幂等键且同一 `requiredScopes` 的重复调用回放已持久化结果。理由：总体 SRS 21.4 要求审计连接测试。
- **DX-CON-005**【新增】提供“最近一次连接测试结果”的只读查询 `get_last_connection_test(key)`（别名 `last_connection_check`），只读 `dx`、不访问 DHIS2；从未测试返回 `None`，未知环境报 `DX_ENVIRONMENT_INVALID`。理由：部署控制台等聚合视图须在不访问 DHIS2 的前提下显示连接状态（S1 CP-UI-012、契约 1.19.0）。
- **DX-CON-006**【新增】只有总体结论为 PASSED 的连接测试才触发一次能力探测，并把 CAPABILITY_PROFILE 产物 ID 写入该次测试记录；失败的测试不刷新能力记录。理由：成功测试已经证明认证与目录可用，此时合成能力是顺带的；失败时探测只会重复失败。
- **DX-CON-007**【新增】地址不可达、TLS 无效、认证失败必须由失败本身分别定性，不得一律写成认证失败；地址不可达时不发出凭据，证书失败时不在不安全信道上判定认证。理由：否则运维会去轮换一个本来没问题的令牌。

### 3.4 版本 Profile 与 Capability Profile（DX-CAP）

- **DX-CAP-001**【直引】（14.3）版本能力 Profile 由以下部分组成：资源目录（逻辑资源名 → 端点、方法、参数名、分页方式、所需凭证范围）、Schema 包（该版本元数据对象 Schema，可从实例 `/api/schemas` 同步并版本化）、导入参数（元数据与 Tracker 导入支持的参数与默认值、异步任务查询方式）、弃用与移除清单、类型描述器覆盖、版本特有的校验规则集、表达式语法版本标识、易变字段清单与默认值清单（供漂移、Snapshot、部署后验证共用）。
- **DX-CAP-002**【直引】（FR-CAP-001）兼容范围为 v2.40～v2.43：0.1.0 只提供 `Dhis241Profile`（版本约束 `2.41.x`，回归实例 2.41.10）；`Dhis240Profile`、`Dhis242Profile`、`Dhis243Profile` 属 M4，**尚未随包发布**，连接或探测这些版本会报 `DHIS2_VERSION_UNSUPPORTED`，该环境不得用于执行。新增 Profile 只需新增数据目录，不修改执行器与 Client 的核心代码。连接测试识别到的版本没有对应 Profile 时，结论为失败并报告“版本不受支持”。
- **DX-CAP-003**【直引】（FR-CAP-002）连接目标环境后，S4 把“版本 Profile + 实例实际探测结果”合成为该环境的 Capability Profile，以带版本的 CAPABILITY_PROFILE 产物持久化；S2、S3 只读取已持久化的产物，禁止运行时猜测。探测使用 discovery 凭证范围，只发 GET；若实例上报的 major.minor 与所用 Profile 系列不一致，拒绝发布产物（`DHIS2_VERSION_UNSUPPORTED`），不拿另一个版本的目录说谎。
- **DX-CAP-004**【直引】（FR-API-002）全部 Profile（v2.40 及以上）的资源目录中，Tracker 数据相关逻辑资源必须解析为 `/api/tracker` 及其 `trackedEntities`、`enrollments`、`events`、`relationships` 子资源；旧 Tracker 端点必须登记在弃用与移除清单中。2.41 的目录已包含 `tracker.import`、`tracker.*` 与 `dataStore`、`apps` 等资源；资源存在不代表对应执行器已交付（DX-EXE-002）。
- **DX-CAP-005**【直引】（FR-CAP-005）目标实例的 major.minor 版本变化后，该环境的 Capability Profile 标记为过期，下一次操作前强制重新探测；同一 major.minor 内的补丁号差异不改变端点形状，不标记过期，但 `check_capability_freshness` 会回报当前补丁版本。新探测发布后，该环境此前未过期的能力索引统一标记为 `superseded`。
- **DX-CAP-006**【新增】提供新鲜度检查 `check_capability_freshness(key)`：读取实例当前版本并与最新未过期 Capability Profile 比较，返回 `VALID` / `EXPIRED` / `VERSION_CHANGED` 及索引元数据；S1 在每个 Run 步骤开始前调用（CP-WF-022）。实例不可达时返回 `EXPIRED` 并给出原因（不假装有效），从未探测同样返回 `EXPIRED`；没有覆盖当前版本的 Profile 时按版本不支持报错。
- **DX-CAP-007**【直引】（FR-CAP-004）每个已交付 Profile 必须有回归测试：用 Golden Project 在对应版本的 DHIS2 官方在线演示实例上完成 VALIDATE 与 COMMIT，遵循 3.11 节的测试规则。0.1.0 仅有 `Dhis241Profile` 的回归；2.40/2.42/2.43 的回归随 M4 交付。不使用容器化 DHIS2 实例。
- **DX-CAP-008**【新增】S4 必须以契约包形式发布 CAPABILITY_PROFILE 与 DISCOVERY_RESULT 产物内容格式：`content_format_schema(artifact_type)` 返回 JSON Schema 与对应 Pydantic 模型，未知类型报 `DX_FEATURE_NOT_ENABLED`；供 S2、S3 解析（00 文档 6.1、BP-ASM-03）。
- **DX-CAP-009**【新增】提供按环境读取最新 Capability Profile 索引的只读查询 `get_capability_profile_index(key)`（Profile 版本、DHIS2 版本、探测时间、已持久化的 `stale` 与原因、产物 ID、内容哈希），只读 `dx`、不访问 DHIS2，也不重新判定新鲜度；未知环境报 `DX_ENVIRONMENT_INVALID`，从未探测返回 `None`。新鲜度状态枚举为 `VALID` / `EXPIRED` / `VERSION_CHANGED`。理由：聚合视图只能显示最近一次探测结果（S1 CP-UI-012）。
- **DX-CAP-010**【新增】探测的 Schema 同步范围默认是**该版本目录里的全部类型**；调用方可用 `schema_types` 显式收窄。实例答不出的 Schema 类型记入 `unavailableResources` 并从运行期目录移除，属于“实例的负面事实”。Schema 合成优先级为：类型描述器覆盖 > 实例同步 > 离线基线；`controlableFields`/`forbidden` 等“项目/版本决定”的字段不会被实例同步清空。理由：S2 只读产物，收窄必须是显式选择。

### 3.5 元数据发现（DX-DSC）

- **DX-DSC-001**【调整】（FR-DSC-001）发现产出“既有元数据索引”：对象类型、UID、code、name、lastUpdated、内容哈希、关键引用关系（单对象引用、引用数组与 `members` 包装）；默认还包含范围内对象的完整内容（规范化前的实例 JSON），供 S2 合并 UPDATE 对象与生成 Diff（BP-ASM-03）。Greenfield 只产出 default 对象，不拉取内容。
- **DX-DSC-002**【直引】（FR-DSC-002）发现范围可配置：`kind`（`RUN_TARGET` / `DESIGN_REFERENCE` / `GREENFIELD`）、按对象类型、按 code 前缀、按组织层级深度；另可指定一组明确的 UID 或 code（两者分别按 `id:in:[…]` 与 `code:in:[…]` 过滤，环境绑定编译时由调用方根据 Registry 传入）。范围必须至少选中一种要读的东西（类型、明确清单、组织深度或 greenfield），否则报 `DX_DISCOVERY_SCOPE_INVALID`，不退化成全量。**0.1.0 限制**：`programs` 目前只作为 `program` 类型的 `id:in` 过滤，尚未实现“按 Program 归属过滤其他类型”；code 前缀在已选类型内生效并在本地复核前缀。
- **DX-DSC-003**【直引】（7.1）Greenfield 项目的发现只读取版本、能力与 default 对象（default CategoryCombo、Category、CategoryOption、CategoryOptionCombo）；default 对象以 `defaults` 映射返回 UID，不混入 `objects` 列表。
- **DX-DSC-004**【直引】（FR-DSC-005）发现只使用 discovery 凭证范围，只发 GET，禁止对目标环境产生任何写入；发现前后实例状态应完全一致。
- **DX-DSC-005**【直引】（FR-DSC-004）发现结果是带时间戳的 DISCOVERY_RESULT 产物与 `dx.discovery_index` 索引；数据分级默认 CONFIDENTIAL（总体 SRS 20.1“目标实例的元数据导出”），并登记来源 Capability Profile 产物 ID。
- **DX-DSC-006**【新增】组织层级按配置深度读取（UID、code、name、层级、父节点、名称路径），供映射引用 OrgUnit；单次最多保留 5,000 个节点，超出时返回 BLOCKING 问题而不是静默截断。理由：总体 SRS FR-COV-007“系统通过发现读取目标环境的组织层级”。
- **DX-DSC-007**【新增】内容哈希必须在排除 Profile 声明的易变/派生字段（`lastUpdated`、`created`、`href`、`access` 等）后，对规范化 JSON 计算；顶层的 `id` 排除，嵌套层的 `id` 保留（它表示“引用了谁”，抹掉会漏报引用改写）；列表顺序保留。理由：否则漂移检测会因无关字段频繁误报，且引用改写会静默漏报。
- **DX-DSC-008**【新增】发现为外部 I/O 函数，接受幂等键、进度回调与取消令牌；同一幂等键与同一范围重复调用回放已登记结果，完成后写审计 `dx.discovery.completed`。理由：00 文档 6.3、总体 SRS 21.4。
- **DX-DSC-009**【新增】S4 必须以契约包形式发布 DISCOVERY_RESULT 内容格式（DX-CAP-008 的同一入口）。理由：S2、S3 解析依赖。
- **DX-DSC-010**【新增】提供发现结果列表的只读查询 `list_discovery_results(key, project_id=None, scope=None, limit=50, offset=0)`：按环境（可选项目与范围 kind）分页返回发现结果索引（产物 ID、项目、范围 kind、对象数、阻塞问题、DHIS2 版本、发现时间、内容哈希、发现 ID）。过滤在 SQL 中完成；不返回总数；不访问 DHIS2。未知环境返回空列表（这是历史查询，不是环境存在性判断）。
- **DX-DSC-011**【新增】提供发现对象检索：`search_discovery_objects(discovery_artifact_id, dhis2_type=None, query=None, limit=50, offset=0)`，在指定发现结果产物中按类型精确匹配、按 code / name / UID 的大小写无关子串检索对象，按 `(类型, UID)` 稳定排序后分页返回 UID、code、name、lastUpdated、内容哈希与总数。只读产物内容、不访问 DHIS2；产物不可解析时报 `DX_INPUT_ARTIFACT_INVALID`。理由：环境绑定人工设置须从该环境的候选对象中选择（S1 CP-UI-005、DX-AC-12）。
- **DX-DSC-012**【新增】发现中的失败必须显式：请求的类型不在目录里、读取某类型失败、组织层级截断都产生 BLOCKING 问题并随结果返回；不得用“读不到”冒充“没有对象”。理由：静默缺口会让 S2 在编译期才发现引用不存在。

### 3.6 为 S2 提供的只读查询（DX-QRY）

- **DX-QRY-001**【新增】`generate_uids`：调用实例官方 UID 生成接口（逻辑资源 `system.id`）批量生成 UID；默认单次上限 100（可配置），超出分批；返回前校验格式（11 位、首字母、字母数字）并去重；实例返回不足时继续申请补齐，绝不自造 UID；`count <= 0` 返回空列表且不发请求。该接口不修改实例。理由：S2 的 UID 池（BP-REG-008a）。
- **DX-QRY-002**【新增】`uid_exists`：批量检查一组 UID 在实例中是否已被任何对象占用，返回占用对象的类型与 code。先查 `identifiableObjects`；对没答上来的 UID 退回按目录逐类型 `filter=id:in:[…]` 扫描，并在 `basis` 中记录依据。扫描中途失败或无法判定时，未确定的 UID 一律按“已占用”报告（保守方向）。理由：总体 SRS FR-REG-007；2.41 实测 `identifiableObjects` 可能对确实存在的 UID 返回空集。
- **DX-QRY-003**【新增】`code_exists`：按对象类型批量检查一组 code 是否存在（`filter=code:in:[…]`，默认每批 50），返回对应 UID 与依据；类型不在目录里或读取不完整时按“已存在”报告，避免 S2 撞号。理由：总体 SRS FR-REG-008。
- **DX-QRY-004**【新增】`has_associated_data`：检查指定对象在实例中是否存在关联业务数据（如数据元的数据值或事件数据、Program 的注册、选项集被数据使用等，判定方式按类型由 Profile 的 `associated_data.json` 定义）。0.1.0 覆盖 DataElement、TrackedEntityAttribute、Program、ProgramStage、OptionSet 五类；其他类型、Profile 未登记判定、或判定端点不可用时一律返回“有关联数据”，即不允许删除。理由：总体 SRS FR-CMP-009 删除安全检查；判不出来就不许删。
- **DX-QRY-005**【新增】以上查询只使用 discovery 凭证范围，只发 GET；结果不持久化、不写审计。理由：只读、无状态。
- **DX-QRY-006**【新增】`uid_exists` / `code_exists` / `has_associated_data` 的返回值是 `dict` 的值子类：`bool(x) == x["exists"]`（关联数据为 `x["hasData"]`），同时携带 `dhis2Type`/`code`/`uid`/`basis` 等字段。理由：S2 既有实现按真值判断，普通富字典会让“已占用”与“可用”都恒为真（DX-QRY-002）。

### 3.7 执行管线（DX-EXE）

#### 3.7.1 通用

- **DX-EXE-001**【直引】（FR-EXE-001）每个执行器实现统一接口：`plan`、`validate`（如支持）、`commit`、`verify`，并返回标准化执行结果。0.1.0 只有 Metadata 执行器启用；另外三个注册但四个方法一律拒绝。
- **DX-EXE-002**【调整】（15.1）执行器与里程碑：Metadata Executor（M2，已实现，支持服务端 VALIDATE）、Tracker Data Executor（M3，未实现，`DX_FEATURE_NOT_ENABLED`）、DataStore Executor（M5，未实现）、App Upload Executor（M5，未实现）。未启用的执行器在**任何 DHIS2 访问之前**拒绝，并点名里程碑，不得先探测实例再解释。
- **DX-EXE-003**【调整】（15.2）执行的输入来自产物：METADATA_PACKAGE（含 S2 的 manifest，BP-CMP-027a）、FIXTURE_PACKAGE、调用方给出的环境键与期望载荷哈希；S4 不调用 S2。manifest 的必需段（`uidToBusinessKey`、`objects`、`changeSummary`、`controlledFields`、`expressionValidation` 等）缺失时按 BLOCKING 拒绝；载荷自身携带的 projectId 与调用方 projectId 不一致时拒绝。
- **DX-EXE-004**【新增】全部执行函数为外部 I/O 函数，接受幂等键、进度回调与取消令牌；执行记录与步骤状态持久化在 `dx.execution` / `dx.execution_step`。同一幂等键重复调用时：VALIDATE 从已完成的 PLAN/DRIFT/SNAPSHOT/VALIDATE 步骤继续并恢复原结论；COMMIT 若已有提交出去的任务引用则继续轮询原任务，否则回放已记录结果，绝不重做不可逆步骤。理由：00 文档 6.3；总体 SRS FR-EXE-009、NFR-REL-003。
- **DX-EXE-005**【直引】（C-08、FR-EXE-006）S4 不判断 Gate；但提交类函数（COMMIT、DELETE、FIXTURE）必须校验调用方给出的期望载荷哈希与载荷产物的实际哈希一致，且与该环境最近一次成功 VALIDATE 的载荷哈希一致；COMMIT 还要求当前环境状态哈希等于 VALIDATE 记录的状态哈希，否则报 `DX_VALIDATION_STALE`。两次 VALIDATE 之间环境被改动，同样必须先重新 VALIDATE。
- **DX-EXE-006**【新增】`prepare_and_validate` 允许 `expected_hash=None`（S1 在 PACKAGE_GATE 前可能只有产物 ID）：此时取产物自身哈希并写回结果，供 COMMIT 报回；一旦声明就必须一致，否则 `DX_PAYLOAD_HASH_MISMATCH`。理由：DX-EXE-005 的“必须声明”约束的是提交类函数。

#### 3.7.2 执行计划

- **DX-EXE-010**【调整】（15.2 PLAN）生成执行计划：环境、执行器及固定顺序、载荷产物与哈希、Capability Profile 与版本、导入参数、预期变更（来自 S2 变更摘要；缺失时给 WARNING 而不是阻断，但写明“无从核对”）、维护动作（默认无）、表达式校验安排（`atValidate` / `atPostVerify`）、漂移检查对象清单、异步导入决定、问题清单；以 EXECUTION_PLAN 产物保存。计划自己的 BLOCKING 问题必须写进文档，续跑时据此阻断。
- **DX-EXE-011**【直引】（FR-EXE-005）元数据导入参数由 Capability Profile 定义，默认：以 UID 作为标识符（`identifier=ASSIGNED`）、创建并更新策略（`importStrategy=CREATE_AND_UPDATE`）、全部成功或全部失败（`atomicMode=ALL`）；`importMode` 由步骤决定，调用方指定会被忽略/拒绝。调用方选择的非默认参数必须在计划中标记为“需在 DEPLOY_GATE 显式确认”；参数不在该版本允许集合内时报 `DX_ENVIRONMENT_INVALID`。
- **DX-EXE-012**【调整】（13.4“Capability ↔ 执行计划”）生成计划时校验：计划中每个执行器所需逻辑资源被目标环境 Capability Profile 支持、对应凭证范围已配置、**最近一次已持久化连接测试覆盖该范围且没有缺失权限**；不满足为 BLOCKING。环境从未做过连接测试同样 BLOCKING——不能把“没测过”当成“没缺失”。本步骤只读 `dx`，不访问实例。
- **DX-EXE-013**【直引】（FR-EXE-007）维护动作默认关闭；只能由调用方在计划请求中列出。MVP 支持的维护动作仅有“清理应用缓存”（`CLEAR_APP_CACHE`）；分析表生成等延后；请求其他动作在发任何 DHIS2 请求前报 `DX_ENVIRONMENT_INVALID`。

#### 3.7.3 漂移检查与 Snapshot

- **DX-EXE-020**【直引】（FR-DSC-004、15.2 DRIFT CHECK）检查 manifest 中每个 REUSE / REFERENCE / UPDATE 对象（以及 `changeSummary.referencesOutsideManagedScope` 中的引用对象）当前的 lastUpdated 与内容哈希是否仍等于观测基准；同一对象只监测一次。对象不存在（`MISSING`）或内容哈希变化（`CONTENT_CHANGED`）返回 `DRIFT_DETECTED`；只有 `lastUpdated` 变化（`UPDATED_ONLY`）或 Registry 没有观测哈希（`NO_BASELINE`）不构成漂移，但会出现在对象清单中作为证据。
- **DX-EXE-021**【新增】漂移检查提供独立函数 `check_drift(key, package_artifact_id, project_id=None)`，供 S1 在 DEPLOY_GATE 审批与 COMMIT 请求时调用（CP-GATE-005、CP-GATE-017）。它只读实例、不写任何东西。理由：总体 SRS FR-VAL-004 要求审批前检测漂移。
- **DX-EXE-022**【直引】（FR-SNP-001）Snapshot 包含：Managed Scope 内全部对象、本次载荷涉及的全部对象与观测基准对象的并集，每个对象的当前 JSON、UID、lastUpdated、内容哈希、是否存在、Managed Scope 定义（取自 manifest）、载荷对象清单、环境 ID、DHIS2 版本与时间戳；以 SNAPSHOT 产物保存，数据分级 CONFIDENTIAL。实际产出 `PRE_COMMIT`（VALIDATE/COMMIT/DELETE 前）与 `POST_COMMIT`（部署后验证）两种；`CURRENT` 仅为保留值，0.1.0 不产出。
- **DX-EXE-023**【直引】（FR-SNP-002）Snapshot 用于提交前后对比、审计、漂移检测、对账与生成修正性载荷的参考。Snapshot 的状态指纹只由“环境键 + 每个对象的类型/UID/内容哈希”派生，大段内容不参与指纹，避免无关字段展开把 VALIDATE 误判为过期。

#### 3.7.4 VALIDATE

- **DX-EXE-025**【直引】（FR-VAL-001）服务端校验以 `importMode=VALIDATE` 提交与 COMMIT 完全相同的载荷与导入参数；导入参数取自本次计划，COMMIT 复用该次成功 VALIDATE 的计划参数，不接受新的参数集。
- **DX-EXE-026**【直引】（AC-EXE-01）VALIDATE 不得使目标环境产生任何变化；只使用 import 凭证范围的只读语义。异步 VALIDATE 只创建任务，不改变元数据。
- **DX-EXE-027**【直引】（FR-VAL-002）DHIS2 返回的 ImportReport 必须完整保存为 DHIS2_IMPORT_REPORT 产物，并标准化为 S2 定义的统一 Issue 结构（BP-VAL-001，级别字段为 `level`）：问题 ID、级别、对象（UID，经 manifest 映射回 businessKey；映射不上的 UID 保留并标记为载荷之外）、说明、来源阶段（按错误类别映射：引用与类型错误 → MAPPING，Schema 与字段错误 → MAPPING，页面相关 → PAGE_DESIGN，无法判定 → MAPPING）。2.41 的 `typeReports[].objectReports[]` 必须展开；带 `errorReports` 的对象按 BLOCKING 处理，不按 `IGNORED` 降级。
- **DX-EXE-028**【调整】（D-05、FR-CMP-011）对 manifest 中标记为“可服务端校验”的表达式，逐条调用实例的表达式校验接口（逻辑资源 `programRules.condition.validate`、`programIndicators.expression.validate`、`validationRules.expression.validate`），结果写入校验报告与执行报告的 `expressionValidation`。没有对应接口的类型、Profile 未登记该资源、或实例返回 404 的，计入 `skipped` 而不是 BLOCKING；只有实例明确判定为否或权限被拒才阻断。
- **DX-EXE-029**【直引】（FR-VAL-003、FR-VAL-004）VALIDATE 失败时禁止任何形式的自动提交；VALIDATE 结果只对“该载荷哈希 + 该环境 + 该时刻环境状态哈希”有效，S4 把三项写入 `dx.validation_record`（同一三元组只保留一行，刷新报告引用与时间）。COMMIT 前状态哈希不一致报 `DX_VALIDATION_STALE`（可重试，需重新 VALIDATE）。
- **DX-EXE-048**【新增】R3 的每次执行（成功、失败或被阻断）都产出 EXECUTION_REPORT 产物；COMMIT 在拿到 ImportReport 后把终态、报告与锁释放一并落库；删除在已经产生 Snapshot/VALIDATE 事实时产出报告，若在取锁后、首份事实前即被拒则只留执行行与审计；维护在拿到结论（或进入 NEEDS_MANUAL_CHECK）时产出报告。理由：总体 SRS 21.4 与 AC-AUD-01 的追溯链要求，同时不把“提交请求根本没发出”伪装成一次有报告的部署。

#### 3.7.5 COMMIT

- **DX-EXE-030**【直引】（NFR-CON-002）对同一环境的提交类执行（COMMIT、Fixture 导入、维护动作、删除）必须获得环境级独占锁；未获得锁时返回 `CONCURRENCY_ERROR`。锁跨项目生效，获取由 `pg_advisory_xact_lock` 串行化，持锁事实落在 `dx.environment_lock`（一环境一行）。
- **DX-EXE-031**【新增】锁在执行结束（成功、失败或被拒绝）时释放；执行进入 NEEDS_MANUAL_CHECK 时锁保持，直至人工核实后由调用方释放。锁不因超时而自动释放；只有持锁执行本人能释放。理由：防止在结果不明时另一提交改变环境。
- **DX-EXE-032**【直引】（15.2 COMMIT）COMMIT 的准入顺序是硬性的：① 期望哈希一致且存在同一哈希的成功 VALIDATE；② 紧急锁定关闭且处于允许提交的时间窗内（非生产未配置时间窗视为允许）；③ 同一载荷的前序执行器已成功且已生效；④ 执行行与环境锁在同一事务落地；⑤ 提交前再次漂移检查；⑥ 提交前 Snapshot 与状态哈希复核；⑦ 提交。任何一步不满足都不提交。
- **DX-EXE-033**【直引】（FR-EXE-008）载荷对象数超过阈值（默认 500，可按环境用 `asyncImportThreshold` 覆盖，也可由调用方在计划里显式 `async=true/false`）时使用异步导入，否则同步导入。计划产物里记录的异步决定是权威，VALIDATE 与 COMMIT 必须走同一条路；计划产物读不到时按对象数与环境阈值重算并标记 `decidedBy=recomputed:threshold`，不得退回同步。异步导入：提交任务后**立即**持久化任务 ID 与查询地址，轮询到终态，保存 ImportReport，更新执行状态。
- **DX-EXE-034**【直引】（FR-EXE-009）进程在等待异步任务期间崩溃后恢复时，必须根据已持久化的任务 ID / 查询地址继续查询，禁止重新提交；恢复路径不重跑哈希、时间窗、漂移与取锁（载荷已交出、锁已由该执行持有）。2.41 的异步提交返回 200 而不是 202，`Location` 指向任务类型集合，任务 ID 在响应体；轮询返回日志条目数组，S4 从终态日志**合成**报告并标记 `reportSource=TASK_LOG`，不伪造逐对象明细。
- **DX-EXE-035**【调整】（NFR-REL-003、19.2.2）提交请求发出后未得到响应、轮询超时、或同步提交无响应时，执行状态为 NEEDS_MANUAL_CHECK 且锁保持；S4 提供对账函数 `reconcile(execution_id)`：读取载荷对象当前状态，与提交前 Snapshot 和载荷三方比对，给出 `APPLIED` / `NOT_APPLIED` / `PARTIAL_MISMATCH` / `UNKNOWN` 与逐对象证据；它只读、不写状态、不释放锁、不重新提交。任何读回失败使整体结论为 UNKNOWN，不把“没看到”说成“没生效”。
- **DX-EXE-036**【新增】提供按幂等键或执行 ID 查询执行状态（含步骤、是否已生效、ImportReport/Snapshot/Plan/报告产物 ID、异步任务引用）。`get_execution_status` 对 S4 内部进行态 `PLANNED` / `RUNNING` 对外报告为 `NEEDS_MANUAL_CHECK`（S1 只认三个终态），细节保留在步骤与 `detail`。理由：S1 在 COMMIT 重试前必须先确认（CP-GATE-018、总体 SRS AC-EXE-05）。
- **DX-EXE-037**【直引】（FR-EXE-002）一次部署含多个执行器时顺序固定为 Metadata → DataStore → Tracker Data → App Upload；前一步失败时后续不执行；执行报告说明哪些步骤已生效。顺序守卫按**同一环境 + 同一载荷哈希**已持久化的 `COMPLETED` 且 `applied=true` 执行记录判定，前序处于 NEEDS_MANUAL_CHECK 不算完成，报 `DX_EXECUTION_ORDER`。0.1.0 只有 Metadata 可运行。（Run 中 POST_VERIFY 位于 Metadata 之后、Tracker 之前，由 S1 编排，CP-WF-021。）
- **DX-EXE-039**【新增】COMMIT 的最终状态只有 `COMPLETED`（ImportReport 通过）、`FAILED`（实例拒绝或漂移/状态失效）与 `NEEDS_MANUAL_CHECK`（结果不明）。`FAILED` 必须释放锁；`NEEDS_MANUAL_CHECK` 必须保留锁并写审计 `dx.commit.needs_manual_check`。理由：三种结局的锁语义不同，混同会让环境要么永久锁死、要么在结果不明时被第二次提交。

#### 3.7.6 定向删除

- **DX-EXE-038**【调整】（FR-CMP-009、15.6 定向删除）删除候选集作为独立载荷提交：来源为 manifest 的 `deletionCandidates`（S1 契约 1.14.0 起由 Change Request 的 `deletionCandidates` 授权并随编译进入 manifest）或载荷的 `@DELETE` 集合；每个候选必须能解析出 `(dhis2Type, uid)`，否则报 `DX_INPUT_ARTIFACT_INVALID`，绝不删除无法寻址的对象。候选的 businessKey 在当前 Blueprint 版本中必须处置为 `DEPRECATED`（该判定由 S1/S2 在编译期完成，S4 不重做业务判断）。
- **DX-EXE-043**【新增】删除准入：紧急锁定关闭；删除策略不为 `FORBID`；计划以 `importStrategy=DELETE`、`atomicMode=ALL` 成立；先做 Snapshot，再以 `importMode=VALIDATE` 校验同一载荷；VALIDATE 通过后**逐个候选当场重做**关联数据检查（DX-QRY-004）。任何一个候选有（或无法判定）关联数据，整批拒绝（`DX_DELETION_FORBIDDEN`），不删一半。
- **DX-EXE-044**【新增】删除提交使用逻辑资源 `metadata.delete`、`importMode=COMMIT`，异步决定与阈值规则同 COMMIT；成功保存 ImportReport 与 EXECUTION_REPORT，失败/结果不明按 COMMIT 的同一套锁语义处理。同一幂等键重入时，若已有未完成的删除，返回 NEEDS_MANUAL_CHECK 而不是再删一次；幂等记录已完成的调用只回放第一次结论。S1 契约 1.9.0 的 `POST /environments/{key}/delete-objects` 是 S1 侧的唯一入口，`Idempotency-Key` 必填且不可由 S1 编造。
- **DX-EXE-049**【新增】删除的“已生效”判定是对象不在环境中；对账把删除候选的期望终态视为“不存在”，对象仍在且与提交前一致为 NOT_APPLIED，仍在但内容已变为 PARTIAL_MISMATCH。

#### 3.7.7 部署后验证

- **DX-EXE-040**【直引】（FR-EXE-010）提交完成后，从目标环境读回全部 CREATE、UPDATE 对象，逐字段比对受控字段（CREATE 对象比对载荷中出现的全部字段，UPDATE 对象比对 manifest 的 `controlledFields`），并检查引用是否落地、共享设置与关键权限是否生效。全部读取使用 discovery 凭证范围，验证过程不写实例。
- **DX-EXE-041**【调整】（D-05）对 manifest 中标记为“延后校验”的表达式，此时调用实例表达式校验接口逐条校验，结果写入部署后验证报告；失败只报告不修正。没有接口或 404 的表达式计入 skipped。
- **DX-EXE-042**【直引】（FR-EXE-011）部署后验证失败时结果为 FAILED，报告列出差异；S4 不自动修正。结果以 POST_VERIFICATION_REPORT 产物保存，同时保存一份 POST_COMMIT Snapshot；它是 `diff_environments` 的“提交后”侧与下一次对比的基准（`diff_snapshot_vs_current` 的跨执行关联限制见 13 章第 5 条）。无法按 UID 寻址的 CREATE/UPDATE 对象产生 BLOCKING 问题，不得静默跳过。
- **DX-EXE-047**【新增】`post_verify` 自己创建一条 POST_VERIFY 执行；`execution_id` 可选，给出时只在那条被验证的执行上记录 POST_VERIFY 步骤，不改变它的终态。报告中的 `consistent` 与本次 POST_VERIFY 执行的 `applied` 同值。理由：提交是否生效是 COMMIT 的事实，验证结论是本次验证的事实，两者不能互相覆盖。

#### 3.7.8 维护动作与报告

- **DX-EXE-045**【直引】（15.2 MAINTENANCE、FR-EXE-007）部署后验证之后，执行计划中已列出并经批准的维护动作，使用 maintenance 凭证范围。0.1.0 只支持 `CLEAR_APP_CACHE`（逻辑资源 `maintenance.dataCache`，`valueStrategy=INVALID`）；请求未在计划中列明的动作报 `DX_ENVIRONMENT_INVALID`；紧急锁定与 PRODUCTION 时间窗同样适用；第一个动作失败即停止后续动作。请求已发出但无法确认时进入 NEEDS_MANUAL_CHECK 且保持锁。
- **DX-EXE-046**【直引】（15.2 REPORT）生成 EXECUTION_REPORT：各步骤状态、已生效范围、ImportReport 与 Snapshot 引用、维护动作结果、耗时、操作者。VALIDATE 失败、计划阻断、删除被拒同样有报告（DX-EXE-048）；报告是产物，不是只存在于返回值里的临时结构。
- **DX-EXE-050**【新增】`run_maintenance` 需要传入一条已有执行（其计划里列明了动作），且该执行必须属于同一环境；跨环境调用报 `DX_ENVIRONMENT_INVALID`。理由：维护动作改变环境，不能借另一环境的“已批准计划”放行（DX-BR-02）。

### 3.8 Tracker Data Executor（DX-TRK，M3）

**0.1.0 状态：未交付。** `TrackerExecutor` 在注册表里存在，但 `plan` / `validate` / `commit` / `verify` 全部抛 `DX_FEATURE_NOT_ENABLED` 并点名 M3，且不发出任何 DHIS2 请求；`import_fixtures` 同样在入口处拒绝。下列条款是 M3 的验收要求，不是本版的实现事实。

- **DX-TRK-001**【直引】（15.1）使用新 Tracker 导入 API，支持 `importMode=VALIDATE` 与 COMMIT。
- **DX-TRK-002**【直引】（FR-EXE-003、FR-POL-006）默认只允许合成数据（FIXTURE_PACKAGE），禁止对 PRODUCTION 类环境执行。真实实现必须在创建任何账户或发送任何写入之前完成环境类别检查；当前拒绝发生在更早的里程碑检查处。
- **DX-TRK-003**【调整】（FR-COV-008）演示用户：按 Fixture 包中的用户规格，通过用户管理接口创建账户，密码一次性生成并经 S0 写入 Secret Manager，执行报告中只记录 Secret 引用；禁止用于 PRODUCTION。0.1.0 的 `DemoUserProvisioner` 注册但拒绝，`dx.fixture.imported` 审计事件已登记但尚未产生。
- **DX-TRK-004**【直引】（FR-POL-007）使用真实数据作为 Fixture 只能导入 DEV 或 TEST 类环境（真实数据导入流程不在当前范围）。`FakeDhis2Api.import_fixtures` 允许在非 PRODUCTION 环境演练编排，真实 API 始终拒绝，直到 M3 接管。

### 3.9 DataStore 与 App Upload Executor（DX-M5）

**0.1.0 状态：未交付。** 两个执行器与 2.41 资源目录里的 `dataStore`/`dataStore.write`、`apps`/`apps.upload` 已登记，但四个方法一律抛 `DX_FEATURE_NOT_ENABLED`（M5），不发出任何请求。

- **DX-M5-001**【直引】（15.1）DataStore Executor：按 DataStore 变更集写入 namespace / key / value；无服务端 VALIDATE，使用系统自定义校验与读后比对；需要的 `DATASTORE_CHANGESET` 产物格式属 M5，本版不猜格式。
- **DX-M5-002**【直引】（15.1）App Upload Executor：通过应用管理接口上传或更新自定义应用包；无服务端 VALIDATE，上传后读取应用列表确认版本；应用包与版本清单格式属 M5。

### 3.10 环境侧 Diff（DX-DIFF）

- **DX-DIFF-001**【调整】（15.5）提供两类对比，均输出逐对象行（新建、更新（字段级）、不变、仅引用）与 DIFF_REPORT 产物：
  1. `diff_snapshot_vs_current(execution_id)`：以该执行的提交前 Snapshot 为基准；在**同一执行**下查找 POST_COMMIT Snapshot，找不到时用 discovery 凭据当场读回（此时为在线操作）。部署后验证把 POST_COMMIT 挂在它自己创建的 POST_VERIFY 执行上，因此对 COMMIT 执行 ID 调用时通常走当场读回（见 13 章第 5 条）。没有基准、且既无 POST_COMMIT Snapshot 又无可回读载荷时，返回 `diffReportArtifactId=null` 与原因，**不产出**“所有对象都不见了”的空报告。
  2. `diff_environments(project_id, key_a, key_b, blueprint_version=None)`：取两个环境各自**最近一次成功且已生效**执行的 Snapshot（POST_COMMIT 优先，否则 PRE_COMMIT，并在 `artifacts` 中如实标出）；给了 Blueprint 版本就在最近 20 条成功执行中找计划版本匹配的那一次。全程只读 `dx` 与产物，不访问 DHIS2；没有任何一侧有成功执行时拒绝生成（没有可比较的对象），一侧没有时按空的一侧对比并在 `summary.notes` 说明。
- **DX-DIFF-002**【新增】产物被 S0 保留策略清除（S1 契约 1.13.0 的 `contentPurged` 回执）时，读取该产物的 Diff、对账、验证或检索必须以“产物不可读”的错误呈现，不得当成空内容或空集合。理由：把“字节已清除”读成“没有对象”会伪造一次大规模删除。

### 3.11 回归测试与测试替身（DX-TST）

- **DX-TST-001**【直引】（总体 SRS 25.4、FR-TST-001）Profile 回归与契约测试的目标实例为 DHIS2 官方在线演示实例：

| 版本 | Profile | baseUrl（不含 `/api`） | 0.1.0 状态 |
| --- | --- | --- | --- |
| 2.40.12 | `Dhis240Profile` | `https://play.im.dhis2.org/stable-2-40-12` | 未交付（M4），无 Profile 数据 |
| 2.41.10 | `Dhis241Profile` | `https://play.im.dhis2.org/stable-2-41-10` | 已交付并有契约/回归 |
| 2.42.6 | `Dhis242Profile` | `https://play.im.dhis2.org/stable-2-42-6` | 未交付（M4），连接/探测报版本不支持 |
| 2.43.1 | `Dhis243Profile` | `https://play.im.dhis2.org/stable-2-43-1` | 未交付（M4），连接/探测报版本不支持 |

  清单保存在 S4 仓库的 `tests/contract/live_instances.json`；实例在测试中登记为 TEST 类环境，认证方式 BASIC，凭证为官方公开的演示账户，以 `env://` Secret 引用在 CI 中提供（代码与日志中不出现凭证）。Environment 的 baseUrl 不含 `/api` 后缀，由资源目录拼接接口路径。
- **DX-TST-002**【调整】（FR-TST-002）测试隔离与清理：
  1. 每次运行生成运行标识，本次创建的全部元数据对象的 code 以 `FT_<运行标识>_` 或 `factory_contract_` 为前缀；
  2. 不修改、不删除实例中既有的演示对象，只以 REFERENCE / REUSE 方式引用（组织单元、default 分类组合等）；需要“既有对象”的 Brownfield 场景（UPDATE、REUSE、漂移），由本次运行先创建前置对象再执行；
  3. 漂移测试只修改本次运行创建的对象；
  4. 运行结束（成功或失败）后删除本次创建的 Tracker 数据与元数据（元数据经删除执行器，Tracker 数据由测试夹具经 Tracker 导入的删除策略清理）；清理失败只告警，下次运行开始时按前缀清理残留；
  5. 只导入合成数据；
  6. 同一实例上本项目的回归运行串行执行（CI 并发组）。
- **DX-TST-003**【调整】（FR-TST-003）回归按日定时执行并在发版前执行，不作为合并阻断检查（合并检查使用 `factory_dhis2.testing` 的 Fake）。实例不可达、在运行中被重置、返回 5xx 或 429 时，本次结果记为“无法判定”并按指数退避重试（最多 3 次）；只有 VALIDATE、COMMIT、部署后验证的结果与期望不符才记为失败。发版前，每个已交付的版本都必须有 7 天内全部通过的回归结果；0.1.0 只有 2.41 满足该条件。
- **DX-TST-004**【调整】（FR-TST-004）回归断言只针对行为（导入结果、问题映射、部署后验证一致性、Profile 解析的端点与参数），不断言演示实例上既有数据的数量或内容，也不以演示实例上的耗时判定 DX-NFR-02 等性能要求。
- **DX-TST-005**【新增】除完整回归外，每日对已纳入的实例执行只读冒烟检查（连接测试、能力探测、范围受限的发现），用于尽早发现官方实例版本变化或下线；发现实例补丁版本变化时生成提醒，由 S4 组更新清单。
- **DX-TST-006**【新增】`factory_dhis2.testing` 提供四种替身：`FakeDhis2Api`（内存公共 API，覆盖除启动函数外的全部操作，可预设校验/提交/漂移/锁定/权限缺失，并允许在非 PRODUCTION 环境演练 Fixture）、`FakeDhis2Instance`（可脚本化的 2.41 实例：版本、对象、共享、多账户凭证、导入报告、异步任务、503/401、超时与“结果不明”）、`FakeDhis2Transport`（真实 httpx 语义，含 pager、Location、任务日志）、`Dhis2Harness`（独占 PostgreSQL，从零重建 `core` 与 `dx`）。Fake 与真实实现必须同形；Fake 有而真身没有的规则不得作为测试依据。

---

## 4. 业务规则汇总

| 编号 | 规则 | 来源 |
| --- | --- | --- |
| DX-BR-01 | 只有 S4 访问 DHIS2；只经官方 Web API | 【直引】4 章引言、2.9 |
| DX-BR-02 | 每个操作显式绑定环境；无默认环境 | 【直引】P-09、FR-ENV-003 |
| DX-BR-03 | 凭证只以 Secret 引用配置，只在进程内存中解析；引用不写入日志、异常或产物 | 【直引】FR-ENV-002、FR-SEC-007 |
| DX-BR-04 | 发现、连接测试与只读查询不得写入实例；离线只读视图对实例调用次数为 0 | 【直引】FR-DSC-005 |
| DX-BR-05 | VALIDATE 与 COMMIT 使用同一载荷与同一参数；COMMIT 不接受新的参数集 | 【直引】FR-VAL-001、P-07 |
| DX-BR-06 | 提交前漂移检查；提交载荷哈希须等于最近一次成功 VALIDATE 的哈希，且状态哈希未变 | 【直引】FR-EXE-004、FR-DSC-004 |
| DX-BR-07 | 同一环境同时只有一个提交类执行；结果不明时锁不释放 | 【调整】NFR-CON-002 |
| DX-BR-08 | 异步任务 ID 立即持久化；恢复时查询不重提 | 【直引】FR-EXE-008/009 |
| DX-BR-09 | 部署后验证与 VALIDATE 失败都不自动修正、不自动提交 | 【直引】FR-VAL-003、FR-EXE-011 |
| DX-BR-10 | Tracker 数据导入禁止 PRODUCTION；默认只允许合成数据 | 【直引】FR-EXE-003 |
| DX-BR-11 | 紧急锁定开启时拒绝全部写类执行（含删除与维护） | 【新增】 |
| DX-BR-12 | 执行顺序 Metadata → DataStore → Tracker → App Upload；前一步失败或未确认，后续不执行 | 【直引】FR-EXE-002 |
| DX-BR-13 | `db://` 主密钥不匹配按认证失败报告，只给引用与原因，不重试、不回显密钥派生值 | 【新增】DX-ENV-003 |
| DX-BR-14 | 判不出来就按保守方向：UID/code 存在性报“已占用”，关联数据报“有”，对账报 `UNKNOWN` | 【新增】DX-QRY-002/003/004、DX-EXE-035 |
| DX-BR-15 | 0.1.0 只有 Metadata 执行器可运行；其余执行器与服务在入口拒绝，不发外部请求 | 【调整】DX-EXE-002 |
| DX-BR-16 | 删除候选来自经 CR 授权的 `deletionCandidates`；不可寻址的候选一律不删 | 【调整】DX-EXE-038 |
| DX-BR-17 | 已清除（`contentPurged`）的产物不可当作空内容使用 | 【新增】DX-DIFF-002 |

---

## 5. 数据需求（schema `dx`）

全部实体含创建与更新时间，略。

| 实体 | 最少字段 | 来源 |
| --- | --- | --- |
| Environment | id、key、name、baseUrl、environmentClass、versionConstraint（保存必填）、authMode、tlsRequired、llmDataPolicy、timeoutSeconds、status、asyncImportThreshold、notes | 【调整】23.1、D-03 |
| EnvironmentCredential | environmentKey、scope、secretRef、expiresAt、label | 【新增】DX-ENV-006 |
| EnvironmentProtection | environmentKey、timezone、commitWindows、deletionPolicy、emergencyLock、lockReason、lockChangedAt、lockChangedBy、requiredApprovals（0.1.0 恒空）、operatorRole（0.1.0 恒空） | 【调整】FR-ENV-001 |
| ConnectionTest | id、environmentKey、testedAt、dhis2Version、requiredScopes、checks、grantedAuthorities、missingAuthorities、result、capabilityProfileArtifactId、operator | 【新增】DX-CON-004 |
| CapabilityProfileIndex | id、environmentKey、profileVersion、dhis2Version、probedAt、stale、staleReason、artifactId、contentHash、idempotencyKey | 【直引】23.1 |
| DiscoveryIndex | id、environmentKey、projectId（可空）、scope、artifactId、contentHash、discoveredAt、idempotencyKey、objectCount、dhis2Version、blockingIssues | 【调整】23.1 |
| Execution | id、projectId（可空）、environmentKey、executorType、mode（VALIDATE / COMMIT / POST_VERIFY / FIXTURE / DELETE / MAINTENANCE）、payloadArtifactId、payloadHash、planArtifactId、idempotencyKey、asyncJobRef、pollUrl、status、applied、envStateHash、objectCount、detail、importReportArtifactId、snapshotArtifactId、reportArtifactId、operator、startedAt、completedAt、manualCheckOutcome、manualCheckNote、resolvedAt、resolvedBy | 【调整】23.1 |
| ExecutionStep | executionId、step、status、startedAt、completedAt、detail（续跑所需的产物 ID 与结论就在这里） | 【新增】DX-EXE-004 |
| ValidationRecord | environmentKey、payloadHash、envStateHash、executionId、validatedAt、result、importReportArtifactId、snapshotArtifactId、issueCount | 【新增】DX-EXE-005、DX-EXE-029 |
| EnvironmentLock | environmentKey、holderExecutionId、acquiredAt、releasedAt、releasedBy、releaseReason、note | 【新增】DX-EXE-030 |
| SnapshotIndex | id、environmentKey、executionId、kind（PRE_COMMIT / POST_COMMIT）、scope、artifactId、contentHash、dhis2Version、objectCount、createdAtSnapshot | 【直引】23.1 |
| DemoUserCredential | executionId、username、secretRef（M3；0.1.0 不写入） | 【新增】DX-TRK-003 |

`dx.execution.status` 内部使用 `PLANNED` / `RUNNING` 两个进行态；对外 `ExecutionStatus` 只有 `COMPLETED` / `FAILED` / `NEEDS_MANUAL_CHECK`，查询函数把进行态报告为 `NEEDS_MANUAL_CHECK`（DX-EXE-036）。

版本 Profile 数据（资源目录、Schema 包、弃用清单等）随 S4 包以数据文件发布，不存数据库；0.1.0 只随包发布 `2.41` 目录。

数据管理：
- **DX-DATA-01**【直引】（FR-DATA-003）`dx` schema 变更通过迁移脚本管理：`0001_dx_initial` 由 ORM 元数据建全部表并安装保留触发器与环境锁一致性触发器；`0002_dx_v110_columns` 以 `ADD COLUMN IF NOT EXISTS` 补 `environment.async_import_threshold`、`discovery_index.dhis2_version`、`connection_test.granted_authorities`。
- **DX-DATA-02**【新增】执行、验证记录、锁记录永久保留（随项目删除时只删除 projectId 关联的发现索引，执行与步骤、验证记录、锁记录、Snapshot 索引均保留以支撑审计追溯）；数据库触发器 `dx.retention_guard` 在删除这三张表时直接拒绝。理由：总体 SRS FR-SEC-014 追溯要求。
- **DX-DATA-03**【新增】`dx.environment_lock` 由 `dx.environment_lock_guard` 保证“有持有者必有 acquiredAt、已释放必有 releasedAt”，避免僵尸锁。`delete_project_data` 不写 S4 审计事件：附录 A 没有该事件，项目删除本身由 S1 审计。

---

## 6. 流程需求

### 6.1 环境登记、更新与连接测试

1. 平台使用者创建环境（引用 Secret）→ 校验字段、PRODUCTION 规则与 TLS/地址自洽 → 写审计。
2. 连接测试 → 只读检查各项（地址、TLS、认证、版本、Profile、按范围权限、账户）→ 通过时合成 Capability Profile 产物 → 返回检查结果与缺失权限；失败时只记录失败，不刷新能力。
3. 变更配置 → 叠加补丁并重新校验；有进行中/待核实执行时拒绝改 baseUrl 与 Secret 引用 → 审计。
4. 禁用或紧急锁定 → 先检查无进行中执行（禁用）或记录原因/时间/人（锁定）→ 审计。

### 6.2 发现（设计基准环境或 Run 目标环境）

1. 新鲜度检查（必要时重新探测能力）→ 按范围与明确对象清单读取 → 规范化与计算哈希 → 保存 DISCOVERY_RESULT 与索引 → 写审计 → 返回产物 ID。发现要求已有 Capability Profile（`require_probe`）；无产物时报 `DHIS2_PROFILE_STALE`，不按打包的基线猜端点。

### 6.3 DHIS2_VALIDATE（Run R3）

1. 读取载荷与 manifest、校验哈希 → 生成执行计划（DX-EXE-010～013；环境未做连接测试或范围权限缺失即 BLOCKING）→ 漂移检查 → Snapshot → VALIDATE（元数据）→ 服务端表达式校验（可校验部分）→ 保存 ImportReport、校验记录与 EXECUTION_REPORT → 返回计划、Snapshot、ImportReport 产物 ID、漂移结果与标准化问题。计划阻断或漂移时不进入 VALIDATE，但仍返回失败报告与已产出的 Snapshot。

### 6.4 COMMIT（Run R4）

1. 校验载荷哈希与最近一次成功 VALIDATE（含参数与状态哈希）→ 紧急锁定与时间窗检查 → 前序执行器顺序守卫 → 获取环境锁（与执行行同事务）→ 再次漂移检查 → Snapshot 与状态哈希复核 → 提交 → 立即持久化任务 ID/查询地址 → 轮询 → 保存 ImportReport 与报告 → 释放锁 → 返回结果。
2. 结果不明（无响应、轮询超时、同步无响应）→ NEEDS_MANUAL_CHECK（锁保持）→ 调用方调用 `reconcile` 取得证据 → 人工给出 PASSED/FAILED → `resolve_manual_check` 记录终态并释放锁。
3. 同一幂等键重试：有任务引用则继续轮询原任务；已终态则回放；绝不第二次提交。

### 6.5 POST_VERIFY 与 Fixture（Run R5、R6）

1. POST_VERIFY：读回 CREATE/UPDATE 对象逐字段比对 → 引用、共享与权限核对 → 延后表达式校验 → 保存 POST_VERIFICATION_REPORT 与 POST_COMMIT Snapshot；失败只报告，不修改实例。
2. Fixture（M3，0.1.0 不可用）：检查非 PRODUCTION → 获取锁 → Tracker VALIDATE → COMMIT → 创建演示用户 → 保存报告 → 释放锁。当前 `import_fixtures` 在入口直接拒绝，不创建任何东西。

### 6.6 定向删除

1. 读取删除候选集（manifest `deletionCandidates` 或 `@DELETE`）→ 校验可寻址 → 紧急锁定与删除策略检查 → 取锁 → 计划（`importStrategy=DELETE`）→ Snapshot → VALIDATE → 逐个候选关联数据检查 → 提交 `metadata.delete` → 保存 ImportReport 与报告 → 释放锁。
2. 任一安全检查失败：整批拒绝、释放锁、返回 FAILED 并列出被拒对象与依据；不删一半。

### 6.7 维护

1. 读取执行计划中已列明的动作 → 校验环境一致 → 紧急锁定与 PRODUCTION 时间窗 → 取锁 → 用 maintenance 范围逐个执行（第一个失败即停）→ 保存报告 → 释放锁；结果不明时锁保持并进入 NEEDS_MANUAL_CHECK。

### 6.8 离线只读视图

1. 聚合视图按环境读取最近连接测试、最新能力索引、发现历史与执行状态；这些函数只读 `dx`/产物，对 DHIS2 的调用次数必须为 0（DX-AC-11）。`diff_environments` 同样只读已持久化的成功执行与 Snapshot；`search_discovery_objects` 只读发现产物。

---

## 7. 公共库 API（`factory_dhis2.api`）

“事务内”指可在调用方工作单元中执行；“外部 I/O”指必须在事务外调用。

### 7.1 环境

| 函数 | 类别 | 输出 |
| --- | --- | --- |
| `DX_MIGRATION_SCRIPT_LOCATION` / `MIGRATION_CONFIG` | 常量 | `dx` schema 的迁移脚本位置与迁移配置 |
| `initialize(*, session=None)` | 启动 | 登记产物类型、审计事件、错误码、ID 前缀，加载内置版本 Profile，并让 `FACTORY_DX_*` 覆盖生效 |
| `create_environment(spec, *, operator=None, session=None)` / `update_environment(key, patch, *, operator=None, session=None)` / `disable_environment(key, *, operator=None, session=None)` | 事务内 | 环境档案（disable 返回 None） |
| `get_environment(key, *, session=None)` / `list_environments(*, session=None)` | 只读、离线 | 环境档案（含 `credentials` 引用与 `secretRefs` 映射，不含凭证值；`version`/`profileVersion` 来自最近能力索引） |
| `get_protection(key, *, now=None, session=None)` | 只读、离线 | 环境类别、时区、时间窗、`withinCommitWindow`、紧急锁定及原因/时间/人、删除策略、`dataExportPolicy`、`allowsFixtureImport`、状态 |
| `set_emergency_lock(key, locked, reason=None, operator=None, *, session=None)` | 事务内 | `{environmentKey, locked, reason, changedAt}` |
| `list_expiring_credentials(within_days=None, *, session=None)` | 只读、离线 | `ExpiringCredential` 列表（含 environmentKey、scope、secretRef、expiresAt、daysLeft） |
| `get_last_connection_test(key, *, session=None)` / `last_connection_check(key, *, session=None)` | 只读、离线 | 最近一次连接测试结果；从未测试返回 `None`（DX-CON-005） |
| `test_connection(key, required_scopes=None, *, idempotency_key, operator=None)` | 外部 I/O、在线 | 总体结论、逐项检查、DHIS2 版本、按范围分组的实际/缺失权限、CAPABILITY_PROFILE 产物 ID |

### 7.2 能力与发现

| 函数 | 类别 | 输出 |
| --- | --- | --- |
| `get_capability_profile_index(key, *, session=None)` | 只读、离线 | 最新 Capability Profile 索引（DX-CAP-009；未知环境报错，从未探测返回 `None`） |
| `check_capability_freshness(key)` | 外部 I/O、在线 | `VALID` / `EXPIRED` / `VERSION_CHANGED`、原因、Profile 版本、索引元数据 |
| `probe_capabilities(key, *, idempotency_key, operator=None, schema_types=None)` | 外部 I/O、在线 | 新 CAPABILITY_PROFILE **产物 ID 字符串** |
| `discover(key, project_id, scope=None, explicit_refs=None, *, idempotency_key, progress=None, cancel=None)` | 外部 I/O、在线 | DISCOVERY_RESULT 产物 ID、能力产物 ID、阻塞问题、default 对象 UID、对象数、内容哈希 |
| `latest_discovery_result(key, project_id=None, *, session=None)` | 只读、离线 | 该环境（可限定项目）最近一次发现产物 ID 或 `None` |
| `list_discovery_results(key, project_id=None, scope=None, *, limit=50, offset=0, session=None)` | 只读、离线 | 发现索引列表（DX-DSC-010；`scope` 过滤的是范围 kind，不返回总数） |
| `search_discovery_objects(discovery_artifact_id, dhis2_type=None, query=None, *, limit=50, offset=0)` | 外部 I/O（读产物）、离线 | 候选对象页与总数（DX-DSC-011，不访问 DHIS2） |

### 7.3 只读查询（供 S2）

| 函数 | 类别 | 输出 |
| --- | --- | --- |
| `generate_uids(key, count)` | 外部 I/O、在线 | UID 列表（`count<=0` 返回空且不发请求） |
| `uid_exists(key, uids)` | 外部 I/O、在线 | `{uid: {exists, dhis2Type, code, name, basis}}`，真值性等于 `exists` |
| `code_exists(key, dhis2_type, codes)` | 外部 I/O、在线 | `{code: {exists, uid, name, basis}}`，真值性等于 `exists` |
| `has_associated_data(key, dhis2_type, uid, *, program=None)` | 外部 I/O、在线 | `{hasData, basis, evidence}`，真值性等于 `hasData`；判不出来为 `True` |

### 7.4 执行

| 函数 | 类别 | 输出 |
| --- | --- | --- |
| `prepare_and_validate(key, project_id, package_artifact_id, expected_hash, *, import_options=None, maintenance_actions=None, operator, idempotency_key, progress=None, cancel=None)` | 外部 I/O、在线 | EXECUTION_PLAN、SNAPSHOT、DHIS2_IMPORT_REPORT、EXECUTION_REPORT 产物 ID；漂移结果；标准化问题（含来源阶段）；是否通过；`expected_hash` 可为 `None` |
| `check_drift(key, package_artifact_id, *, project_id=None, cancel=None)` | 外部 I/O、在线 | 是否漂移、差异对象与 `checkedAt` |
| `commit(key, project_id, package_artifact_id, expected_hash, *, operator, idempotency_key, progress=None, cancel=None)` | 外部 I/O、在线 | 执行 ID、状态（COMPLETED / FAILED / NEEDS_MANUAL_CHECK）、ImportReport 与报告产物 ID、`applied` |
| `get_execution_by_key(idempotency_key, *, session=None)` / `get_execution(execution_id, *, session=None)` / `get_execution_status(execution_id, *, session=None)` | 只读、离线 | 执行状态、步骤、是否已生效、各产物 ID、异步任务引用 |
| `reconcile(execution_id)` | 外部 I/O、在线 | `APPLIED` / `NOT_APPLIED` / `PARTIAL_MISMATCH` / `UNKNOWN` 与逐对象证据；只读，不改锁 |
| `resolve_manual_check(execution_id, outcome, note, operator, *, session=None)` | 事务内 | 终态、是否释放锁、结论与操作者 |
| `post_verify(key, project_id, package_artifact_id, execution_id, *, operator, idempotency_key, progress=None, cancel=None)` | 外部 I/O、在线 | POST_VERIFICATION_REPORT 产物 ID、是否一致、比对对象数、问题；`execution_id` 可为 `None` |
| `import_fixtures(key, project_id, fixture_artifact_id, *, operator, idempotency_key, progress=None, cancel=None)` | 外部 I/O | **0.1.0 抛 `DX_FEATURE_NOT_ENABLED`（M3），不发请求** |
| `run_maintenance(key, execution_id, actions, *, operator, idempotency_key)` | 外部 I/O、在线 | 执行结果、报告产物 ID、是否生效 |
| `delete_objects(key, project_id, deletion_artifact_id, expected_hash, *, operator, idempotency_key, progress=None, cancel=None)` | 外部 I/O、在线 | 执行结果、ImportReport 与报告产物 ID、`applied` |
| `diff_snapshot_vs_current(execution_id)` | 外部 I/O；有 POST_COMMIT 时离线，否则在线 | DIFF_REPORT 产物 ID 或 `None`、逐对象行、摘要 |
| `diff_environments(project_id, key_a, key_b, *, blueprint_version=None)` | 外部 I/O、离线 | DIFF_REPORT 产物 ID、逐对象行、摘要（不访问 DHIS2） |
| `delete_project_data(project_id, *, session=None)` | 事务内 | 无；只删发现索引，不写 S4 审计 |
| `content_format_schema(artifact_type)` | 纯函数 | CAPABILITY_PROFILE / DISCOVERY_RESULT 的 JSON Schema；未知类型抛 `DX_FEATURE_NOT_ENABLED` |

- **DX-API-001**【新增】`factory_dhis2.testing.FakeDhis2Api` 必须提供上述除启动与标记函数外的全部公共操作（内存模拟实例：可预设版本、对象、导入结果、漂移、锁定、权限缺失、超时与结果不明），供 S1、S2 独立开发与测试；`content_format_schema` 直接复用真实实现，`import_fixtures` 可在非 PRODUCTION 环境演练编排。理由：00 文档 6.1。
- **DX-API-002**【新增】`get_last_connection_test` 是正式名称，`last_connection_check` 是向后兼容别名；两者返回同一结果。S1 契约 1.19.0 的 `ExecutionAdapter.METHOD_MAP` 已使用正式名称。理由：命名分歧必须收敛，同时不破坏已落地的调用方。
- **DX-API-003**【新增】公共 API 的类型与内容格式模型也从 `factory_dhis2.api` 导出：`EnvironmentSpec`、`EnvironmentPatch`、`EnvironmentInfo`、`EnvironmentProtection`、`ExpiringCredential`、`ConnectionCheck`、`CapabilityFreshnessInfo`、`CapabilityProfileIndexInfo`、`DiscoveryScope`、`DiscoveryOutcome`、`DiscoveryResultInfo`、`DiscoveryObjectSearch`、`DiscoveryObjectCandidate`、`Dhis2ValidationOutcome`、`DriftResult`、`ExecutionOutcome`、`ExecutionStatusInfo`、`ReconcileResult`、`PostVerifyOutcome`、`DiffReportOutcome`、`Issue`、`ExecutionStatus`、`DataClassification`、`CapabilityProfileDocument`、`DiscoveryResultDocument`、`DiscoveredObjectDocument`、`DiscoveredSchema`、`OrgUnitDocument`、`ResourceEntryDocument`。调用方不得深导入 `factory_dhis2.contract` 或 `factory_dhis2.dto`。

---

## 8. 与其他子系统的边界

| 提供方 / 调用方 | 能力 | 用途 |
| --- | --- | --- |
| S0（依赖） | 产物读取与登记、审计、幂等（作用域 `dx.execution` 不过期）、Secret 解析与写入（M3）、错误、ID、规范化 JSON 与哈希、进度与取消、外部 I/O 标记、对象存储与保留/清除 | 全部 |
| S2（仅内容格式） | METADATA_PACKAGE 与 manifest（BP-CMP-027a，含 `deletionCandidates`）、FIXTURE_PACKAGE 的内容格式定义 | 执行输入 |
| S1（调用方，REST 契约 1.19.0 / 147 条路径） | 把环境、发现、执行与删除投影为控制平面端点；`POST /environments/{key}/delete-objects` 自 1.9.0 起存在，`Idempotency-Key` 必填；1.13.0 的 `contentPurged` 回执与 1.14.0 的 CR `deletionCandidates` / 外发上限修复影响 S4 产物的消费方式 | S4 被 S1 调用 |

S4 不调用 S1、S2、S3 的任何函数；依赖方向是 S4 → S0（00 文档 4.2）。S1 的 `ExecutionAdapter.METHOD_MAP` 当前映射了环境 CRUD/保护/连接测试、新鲜度/探测、发现与检索、`prepare_and_validate`、漂移、COMMIT、执行查询、对账、人工核实、部署后验证、Fixture、删除与项目清理；`get_capability_profile_index`、`latest_discovery_result`、`get_execution_status`、`run_maintenance`、`diff_snapshot_vs_current`、`diff_environments`、`content_format_schema` 尚未出现在该映射中，只能由库调用方直接使用（见 13 章）。

---

## 9. 非功能需求

| 编号 | 要求 | 来源 |
| --- | --- | --- |
| DX-NFR-01 | DHIS2 请求默认超时 60 秒，可按环境配置 | 【直引】FR-IF-006 |
| DX-NFR-02 | 1,000 个对象的漂移检查与 Snapshot 各 < 60 秒（不含 DHIS2 响应慢于 1 秒 / 请求的情形） | 【新增】 |
| DX-NFR-03 | 5,000 个对象规模的元数据包可作为单一导入执行（必要时使用异步导入，默认阈值 500） | 【调整】NFR-CAP-001 |
| DX-NFR-04 | 恢复时不重复执行已完成的不可逆操作 | 【直引】NFR-REL-003 |
| DX-NFR-05 | 契约测试在对应版本的 DHIS2 官方在线演示实例上运行 VALIDATE 与 COMMIT；0.1.0 仅 2.41 已交付，2.40/2.42/2.43 随 M4，规则见 3.11 | 【直引】25.1、25.4、FR-CAP-004 |
| DX-NFR-06 | 单元测试行覆盖率 ≥ 85%；执行管线与锁 ≥ 90% | 【调整】NFR-MNT-004 |
| DX-NFR-07 | 新增 DHIS2 版本只需新增版本 Profile | 【直引】NFR-MNT-002 |
| DX-NFR-08 | 运行 S4 的工作进程必须能以 HTTPS 访问目标实例 | 【直引】2.8 |
| DX-NFR-09 | 离线只读视图（最近连接测试、能力索引、发现列表、执行状态、内容格式、`diff_environments`、`search_discovery_objects`）对 DHIS2 的调用次数为 0 | 【新增】DX-AC-11、DX-CON-005、DX-CAP-009、DX-DSC-010/011 |
| DX-NFR-10 | 日志、异常、审计、产物与 API 返回值不得出现任何已解析的凭证明文、主密钥、密钥派生检查值或掩码片段 | 【新增】DX-CLI-002/004、DX-BR-13 |
| DX-NFR-11 | 本版只保证 `Dhis241Profile` 的行为；其他版本必须在连接测试中明确判失败，不得以降级 Profile 冒充 | 【新增】DX-CAP-002 |

---

## 10. 跨系统假设

| 编号 | 假设 | 不成立时的影响 |
| --- | --- | --- |
| DX-ASM-01 | S1 在调用提交类函数前已完成 Gate 判定，并传入 Gate 绑定的载荷哈希 | S4 只能以哈希一致性兜底 |
| DX-ASM-02 | S2 的 manifest 包含 BP-CMP-027a 列出的全部信息（含 `uidToBusinessKey`、`objects`、`changeSummary`、`controlledFields`、`expressionValidation`、`deletionCandidates`） | 无法漂移检查、Snapshot、映射问题、删除与验证 |
| DX-ASM-03 | S1 在环境绑定编译前的发现中传入由 Registry 得到的明确对象清单 | 发现范围不足，绑定无法解析 |
| DX-ASM-04 | 目标实例管理员已创建所需服务账户并授予权限（总体 SRS ASM-04） | 连接测试失败 |
| DX-ASM-05 | 生产实例数据库备份由实例运维负责（总体 SRS ASM-05） | DEPLOY_GATE 备份确认无法勾选 |
| DX-ASM-06 | S1 在部署启动时注册 `db://` Secret 后端；主密钥在部署环境变量中且在进程生命周期内不变 | `db://` 引用无法解析（`keyMismatch`/`missing`），连接测试与执行失败 |
| DX-ASM-07 | S1 调用 `test_connection` 时声明执行实际需要的凭证范围（至少 `discovery` 与 `import`） | 计划因“最近连接测试未覆盖该范围”BLOCKING |
| DX-ASM-08 | S2 在删除编译中只把经 Change Request 授权、且当前处置为 `DEPRECATED` 的对象放进 `deletionCandidates` | S4 仍会按可寻址性与关联数据保守拒绝，但删除范围可能不符预期 |

---

## 11. 验收标准

| 编号 | 给定 / 当 | 则 | 里程碑 | 来源 |
| --- | --- | --- | --- | --- |
| AC-VAL-02 | 服务端 VALIDATE 返回错误 | 保存 ImportReport，错误映射到 businessKey 与来源阶段，结果为未通过，并留下 EXECUTION_REPORT | M2（已交付） | 【调整】 |
| AC-EXE-01 | 执行 VALIDATE | 目标环境无任何变化 | M2（已交付） | 【直引】 |
| AC-EXE-02（S4 部分） | 以不同于最近一次成功 VALIDATE 的载荷哈希或已变化的环境状态调用提交 | 拒绝提交（`DX_PAYLOAD_HASH_MISMATCH` / `DX_VALIDATION_STALE`） | M2（已交付） | 【调整】 |
| AC-EXE-03 | VALIDATE 后 REUSE 对象被他人修改 | 漂移检查返回 `DRIFT_DETECTED` 与对象清单 | M2（已交付） | 【直引】 |
| AC-EXE-04 | 两个请求同时对同一环境提交 | 一个获得锁执行，另一个返回 `CONCURRENCY_ERROR` | M2（已交付） | 【直引】 |
| AC-EXE-05（S4 部分） | 提交期间网络中断，按幂等键查询 | 返回持久化状态；无任务 ID 时为 NEEDS_MANUAL_CHECK，对账给出判断 | M2（已交付） | 【调整】 |
| AC-EXE-06 | 提交完成后部署后验证 | 逐对象比对受控字段、引用与共享设置，生成报告；不修改实例 | M2（已交付） | 【直引】 |
| AC-CAP-01（S4 部分） | 连接 v2.42 实例 | 0.1.0 无 `Dhis242Profile`，连接测试失败并报告版本不受支持；M4 交付后才能生成对应能力 | M4（未交付） | 【调整】 |
| AC-CAP-02（S4 部分） | 连接 v2.39 实例或无对应 Profile 的版本 | 连接测试结论为失败并报告版本不受支持 | M2（已交付） | 【直引】 |
| DX-AC-15 | 在官方在线演示实例上完成一次 Golden Project 回归 | 本次创建的对象全部带运行前缀并在结束后删除；既有演示对象的内容哈希在运行前后一致 | M2（2.41 已交付） | 【新增】 |
| DX-AC-16 | 回归运行中实例不可达 | 结果记为“无法判定”并重试，不记为失败 | M2（已交付） | 【新增】 |
| AC-ENV-01（S4 部分） | 对 UAT 执行发现并传入明确对象清单 | 发现结果包含这些对象的 UID 与当前内容 | M2（已交付） | 【调整】 |
| DX-AC-01 | 连接测试账户缺少元数据导入权限 | 报告列出缺失权限，未对实例做任何写入 | M1（已交付） | 【新增】 |
| DX-AC-02 | 保存 PRODUCTION 环境时 discovery 与 import 引用同一 Secret | 拒绝保存（更新时同样拒绝） | M1（已交付） | 【新增】 |
| DX-AC-03 | 等待异步导入时进程崩溃后以同一幂等键重试 | 继续轮询原任务，不重新提交 | M2（已交付） | 【新增】 |
| DX-AC-04 | 执行进入 NEEDS_MANUAL_CHECK 后另一项目对同一环境提交 | 返回 `CONCURRENCY_ERROR`，`details.reason=MANUAL_CHECK_PENDING` | M2（已交付） | 【新增】 |
| DX-AC-05 | 紧急锁定开启时提交、删除或维护 | 返回 `ENVIRONMENT_LOCKED` | M2（已交付） | 【新增】 |
| DX-AC-06 | 实例 major.minor 升级后调用新鲜度检查 | 返回“版本已变化”，旧 Capability Profile 标记过期；仅补丁号变化不标记过期 | M2（已交付） | 【新增】 |
| DX-AC-07 | 仅 `lastUpdated` 变化的对象 | 不报告漂移（`UPDATED_ONLY`） | M2（已交付） | 【新增】 |
| DX-AC-08 | 生成 500 个 UID | 全部符合 DHIS2 UID 格式且互不相同 | M2（已交付） | 【新增】 |
| DX-AC-09 | 对 PRODUCTION 环境导入 Fixture | 拒绝执行；0.1.0 的拒绝来自 `DX_FEATURE_NOT_ENABLED`（M3），不创建任何账户 | M3（未交付） | 【新增】 |
| DX-AC-10 | 延后校验的表达式在部署后验证中失败 | 报告失败，不修改实例 | M2（已交付） | 【新增】 |
| DX-AC-11 | 调用最近连接测试结果、最新能力档案索引、发现结果列表、发现对象检索、最近发现 ID、`diff_environments` | 均返回结果且对 DHIS2 的调用次数为 0 | M2（已交付） | 【新增】 |
| DX-AC-12 | 在含 5,000 个对象的发现结果中按类型与关键字检索 | 分页返回匹配对象与总数 | M2（已交付） | 【新增】 |
| DX-AC-13 | 连接测试账户缺少元数据导入权限 | 逐项检查齐备，`import` 范围下列出缺失权限，总体结论为失败 | M1（已交付） | 【新增】 |
| DX-AC-14 | ImportReport 标准化后的问题 | 使用统一 Issue 结构，级别字段为 `level` | M2（已交付） | 【新增】 |
| DX-AC-17 | 环境有 RUNNING/NEEDS_MANUAL_CHECK 执行时更新 baseUrl 或 Secret 引用，或禁用环境 | 拒绝并报 `DX_ENVIRONMENT_IN_USE`；其余字段更新可进行并写审计 | M2（已交付） | 【新增】DX-ENV-008/009 |
| DX-AC-18 | 环境从未做过连接测试就生成执行计划 | 计划 BLOCKING，提示先 `test_connection` 并覆盖所需范围 | M2（已交付） | 【新增】DX-EXE-012 |
| DX-AC-19 | `db://` 记录由另一主密钥写入后连接测试 | 报 `DHIS2_AUTH_ERROR`，消息含“different master key”，`details` 只有引用，不含明文/密钥派生值，且不重试 | M2（已交付） | 【新增】DX-ENV-003 |
| DX-AC-20 | 以 `deletionPolicy=FORBID` 或含关联数据的候选调用 `delete_objects` | 整批拒绝、返回 `DX_DELETION_FORBIDDEN` 与依据，实例无删除发生 | M2（已交付） | 【新增】DX-EXE-038/043 |
| DX-AC-21 | 用未经 VALIDATE 的删除载荷、或不可寻址的候选提交删除 | 拒绝（哈希/校验/输入错误），不提交任何删除 | M2（已交付） | 【新增】DX-EXE-038/043/044 |
| DX-AC-22 | 调用 `content_format_schema("CAPABILITY_PROFILE"/"DISCOVERY_RESULT")` | 返回可校验的 JSON Schema；未知类型抛 `DX_FEATURE_NOT_ENABLED` | M2（已交付） | 【新增】DX-CAP-008 |

---

## 12. 里程碑分工

| 里程碑 | 交付内容 | 0.1.0 状态 |
| --- | --- | --- |
| M1 | 环境档案与保护设置、DHIS2 Client、连接测试、Capability Profile 框架、Fake 实现 | 已交付 |
| M2（MVP） | `Dhis241Profile` 及其在官方在线演示实例上的回归、回归测试环境（3.11）、发现、只读查询（UID 生成、存在性、关联数据）、Metadata Executor 全管线（计划、漂移、Snapshot、VALIDATE、COMMIT、异步、对账、部署后验证、表达式校验、删除）、环境锁、新鲜度检查、环境侧 Diff | 已交付（离线视图、删除、维护与两类 Diff 均可用） |
| M3 | Tracker Data Executor、演示用户、Fixture 导入 | **未交付**：执行器、Fixtures 与演示用户均注册但拒绝 `DX_FEATURE_NOT_ENABLED`；`dx.fixture.imported` 审计事件已预留 |
| M4 | `Dhis240Profile`、`Dhis242Profile`、`Dhis243Profile` 及其在官方在线演示实例上的回归、生产保护中的审批人数与操作员角色、多环境晋级回归 | **未交付**：三份 Profile 数据与回归均缺失；`requiredApprovals`/`operatorRole` 保存时拒绝非空值 |
| M5 | DataStore Executor、App Upload Executor | **未交付**：执行器注册但四方法拒绝；资源目录条目已存在 |

---

## 13. 未决问题

以下为 0.1.0 反向校准时确认的差异，**未通过改代码解决**，按“事实已写入文档、待后续收敛”处理：

1. **S1 契约接线不完整。** S1 1.19.0 的 `ExecutionAdapter.METHOD_MAP` 未包含 `get_capability_profile_index`、`latest_discovery_result`、`get_execution_status`、`run_maintenance`、`diff_snapshot_vs_current`、`diff_environments`、`content_format_schema`。这些函数在 S4 可用并有测试，但还不能经 S1 的 REST 面到达；由 S1 与 S4 在下一轮决定是否补映射，S4 不擅自删除公共函数。S1 的 `GET /environments/{key}/capability-profile` 目前走 `check_capability_freshness`（会访问实例），而不是离线的 `get_capability_profile_index`。
2. **`last_connection_check` 别名。** 正式名为 `get_last_connection_test`，S1 1.19.0 已改用正式名；别名保留以免破坏旧调用方，但长期应只保留一个入口。
3. **`update_environment` 可绕过禁用的活动执行检查。** `disable_environment` 会拒绝有 RUNNING/NEEDS_MANUAL_CHECK 执行的环境，但 `update_environment(patch={status:"DISABLED"})` 目前不检查。文档按 DX-ENV-009 要求“禁用前必须无进行中执行”，实现需要在下一版补齐；在此之前调用方应使用 `disable_environment`。
4. **发现范围的两处限制。** `programs` 只作为 `program` 类型的 `id:in` 过滤，未实现跨类型的 Program 归属过滤；单独给 `codePrefixes` 而不给 `dhis2Types`/`explicitRefs`/`orgUnitDepth` 时不会选中任何对象（会报 `DX_DISCOVERY_SCOPE_INVALID`）。调用方必须同时给出类型或明确清单。
5. **`diff_snapshot_vs_current` 用不到部署后验证的 POST_COMMIT Snapshot。** 部署后验证把 POST_COMMIT 挂在它自己创建的 POST_VERIFY 执行上，而 `diff_snapshot_vs_current` 只在传入的 execution_id 下查找；对 COMMIT 执行调用时因此走 discovery 当场读回（在线），不是“优先使用 POST_COMMIT”。`diff_environments` 会扫描成功执行，能找到那份 POST_COMMIT。修正方式是把被验证执行 ID 关联到 Snapshot 索引，或让查询按载荷/执行链查找；文档按现状写明。
6. **Diff 的证据不足语义。** `diff_snapshot_vs_current` 在既无 POST_COMMIT Snapshot 又无可回读载荷时返回 `diffReportArtifactId=null` 与原因，而不是空 DIFF_REPORT；这与“总是产出产物”的旧措辞不同，但避免了把“无法知道”说成“全部删除”。
7. **内部进行态的对外映射。** `get_execution_status` 把 `PLANNED` / `RUNNING` 报成 `NEEDS_MANUAL_CHECK`，因为 S1 的 `ExecutionStatus` 只有三个终态。语义上“进行中”与“结果不明”不同，S1 需要靠步骤与 `detail` 区分。
8. **Profile 与执行器缺口。** 2.40/2.42/2.43 Profile、Tracker/DataStore/App Upload 执行器、Fixture 与演示用户均未交付（M3/M4/M5）；相关资源目录与注册表已就位，启用它们是数据与实现补齐，不是重构。
9. **`delete_project_data` 不写审计。** 附录 A 没有项目删除事件，项目删除由 S1 审计；S4 只删除发现索引，执行、步骤、验证、锁与 Snapshot 索引保留。

---

## 附录 A：S4 产生的审计事件

共 23 个已登记事件：`dx.environment.created` / `updated` / `disabled`、`dx.environment.lock_changed`、`dx.credential_ref.changed`、`dx.connection.tested`、`dx.capability.probed`、`dx.capability.stale`、`dx.discovery.completed`、`dx.snapshot.created`、`dx.validate.started` / `completed`、`dx.commit.started` / `completed` / `failed` / `needs_manual_check`、`dx.manual_check.resolved`、`dx.maintenance.executed`、`dx.post_verify.completed`、`dx.fixture.imported`、`dx.delete.executed`、`dx.env_lock.acquired` / `released`。

其中 `dx.fixture.imported` 已登记但 0.1.0 不会产生（M3 未交付）；`delete_project_data` 不写 S4 审计事件。

## 附录 B：源文档引用表

| # | 文档 | 类型 | 引用范围 | 处理方式 |
| --- | --- | --- | --- | --- |
| 1 | 总体 SRS V1.4.0 | 总体需求规格 | 2.8～2.10、9.2、13.5、14、15、19.3、21.2～21.3、22.2、25.1 | 抽取改写 |
| 2 | 总体 SRS V1.4.0 | 总体需求规格 | 19.3 DHIS2 Web API 逻辑名清单、附录 C 参考资料 | 章节引用 |
| 3 | 00 总体架构与集成约定 V1.3.0 | 共享知识库 | 全文（尤其 0.2 权威顺序、6.1～6.3） | 全文引用 |
| 4 | S0 公共基础库 SRS V1.0.2 | 子系统需求规格 | 公共 API 概要、Secret 适配器、产物与审计 | 章节引用 |
| 5 | S2 Blueprint 与构建 SRS V1.1.5 | 子系统需求规格 | BP-CMP-021、BP-CMP-026、BP-CMP-027a（载荷、manifest 与删除候选） | 章节引用 |
| 6 | S1 Factory 控制平面 SRS V1.4.0 / REST 契约 1.19.0（147 路径） | 调用方契约 | 环境、发现、执行、删除端点与 `contentPurged`/`deletionCandidates`/外发上限的接线 | 章节引用 |
