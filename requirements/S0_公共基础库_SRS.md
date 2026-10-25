# DHIS2 App Studio / Metadata Factory
# S0 公共基础库（Foundation）子系统需求规格说明书

## 0. 文档信息

| 项目 | 内容 |
| --- | --- |
| 子系统 | S0 公共基础库 |
| 文档版本 | V1.0.2 |
| 上位文档 | 总体 SRS V1.4.0；《00 总体架构与集成约定》V1.3.0（下称“00 文档”） |
| 交付形式 | Python 3.12+ 库（包名建议 `factory_foundation`），无独立进程 |
| 数据库 schema | `core` |
| 需求编号前缀 | FD- |
| 来源标记 | 【直引】【裁剪】【调整】【新增】，定义见 00 文档 0.3 |

---

## 1. 目的与范围

### 1.1 目的

S0 为 S1～S4 提供共同使用、且不属于任何业务子系统的基础能力，使各子系统能独立开发，同时保证依赖无环（00 文档 D-17）。S0 是全部后端子系统的唯一共同依赖，必须最先交付可用版本。

### 1.2 范围内

1. 数据库连接、工作单元（跨子系统共享事务）、schema 约定、迁移运行器；
2. 产物存储（总体 SRS 的 Artifact Service）；
3. 审计服务（哈希链，总体 SRS 的 Audit Service）；
4. 幂等存储；
5. 对象存储适配器；
6. Secret 适配器；
7. 公共工具：错误模型、ID、规范化 JSON 与哈希、时钟、操作上下文、日志与追踪、配置加载、日志脱敏；
8. 长任务进度与取消协议；
9. 测试套件（测试替身与测试夹具）。

### 1.3 范围外

S0 禁止包含：工作流与阶段逻辑、Gate 逻辑、Blueprint 语义、编译与校验、LLM 调用与策略判定、DHIS2 访问、任何通知发送。S0 禁止依赖 S1～S4 的任何包。

---

## 2. 领域概念与定义

| 概念 | 定义 |
| --- | --- |
| 工作单元（Unit of Work） | 一个数据库事务上下文；同一调用链上各子系统的数据库操作可加入同一工作单元，由最外层提交或回滚 |
| 外部 I/O 函数 | 调用 LLM、DHIS2 或上传大文件等耗时外部操作的函数；禁止在已开启的工作单元中执行 |
| 产物（Artifact） | 内容不可变、带 SHA-256 哈希与数据分级的持久化文件及其元数据 |
| 产物类型目录 | 产物类型及其所有者子系统的登记表 |
| 数据分级 | PUBLIC、INTERNAL、CONFIDENTIAL、RESTRICTED、PHI_PII（总体 SRS 20.1），按此顺序从低到高 |
| 审计事件 | 只追加、以哈希链串联的关键操作记录 |
| 操作上下文 | 当前调用链的 correlationId、操作者、项目 ID 等上下文，以上下文变量传递 |
| 幂等记录 | 以（作用域、幂等键）标识的一次副作用调用及其结果 |

---

## 3. 功能需求

### 3.1 数据库与工作单元（FD-DB）

- **FD-DB-001**【新增】S0 必须根据配置（`DATABASE_URL`）提供 PostgreSQL 连接与会话工厂，供全部子系统使用。理由：D-08 共用数据库。
- **FD-DB-002**【新增】S0 必须为每个子系统提供绑定其 schema（`core`、`cp`、`bp`、`ai`、`dx`）的 ORM 基类，使各子系统的表定义自动落入自己的 schema。理由：D-08 schema 划分。
- **FD-DB-003**【新增】S0 必须提供工作单元：
  1. 调用链上首次开启时创建事务，嵌套调用自动加入同一事务；
  2. 只有最外层可以提交；任一层抛出未处理异常时整体回滚；
  3. 提供“事务提交后执行”的回调登记，供需在提交后才执行的动作使用（如写事件流通知）。
  理由：D-11 要求状态变更与审计同事务；跨子系统调用需共享事务。
- **FD-DB-004**【新增】S0 必须提供外部 I/O 函数标记；被标记函数在已开启的工作单元中调用时，必须立即抛出 `TRANSACTION_MISUSE` 错误。理由：防止长时间持有数据库事务（00 文档 6.2）。
- **FD-DB-005**【新增】S0 必须提供乐观并发控制工具：带版本号的聚合在更新时校验版本，不一致时抛出 `CONCURRENCY_ERROR`。校验对象是调用方已加载的实体：`bump_version` 只比较内存中的 `version` 并自增，不生成 `WHERE version = ?` 的原子更新语句；调用方必须在同一工作单元内完成“读取—校验—写入”，或自行在 SQL 层加版本条件。理由：总体 SRS NFR-CON-001 的实现基础（例如同一基准版本只能批准一个 Patch）。
- **FD-DB-006**【新增】S0 必须提供一个检查工具（测试时调用），扫描已登记的表定义，发现跨 schema 外键即失败。检查器必须按外键声明的点分目标名解析目标 schema（如 `core.artifact.id`）与目标表，使各 schema 独立 `MetaData` 的跨 schema 声明也能被发现；未带 schema 的两段名按声明表所在 schema 解释。理由：00 文档 4.2 的可验证性。
- **FD-DB-007**【调整】（FR-DATA-003）S0 必须提供迁移运行器：每个子系统维护本 schema 的 Alembic 迁移与独立版本表；运行器按 `core → dx → bp → ai → cp` 顺序执行；生产环境只允许前向迁移。开发环境可在显式给出 `downgrade_to` 且 `forward_only=False` 时降级；生产环境即使给出目标也拒绝降级。

### 3.2 产物存储（FD-ART）

- **FD-ART-001**【调整】（4.3、23.1）产物元数据字段：ID、项目 ID、工作流 ID（可空）、类型、版本号、状态、内容位置、内容哈希、大小、媒体类型、数据分级、构建记录（JSON）、来源产物 ID 列表、前一版本 ID、创建方（子系统代码 + 操作者）、创建时间、内容是否已清除、脱敏记录引用（可空）。
- **FD-ART-002**【调整】（FR-DATA-001）S0 维护产物类型目录；各子系统在初始化时登记其拥有的类型。一个类型可以有多个允许的所有者子系统（如 DIFF_REPORT 属于 S2 与 S4），多个子系统登记同一类型时合并其所有者列表。创建某类型的产物时，调用方声明的子系统代码必须属于该类型允许的所有者，否则抛出 `ARTIFACT_ERROR`。初始类型目录：

| 类型 | 所有者 | 类型 | 所有者 |
| --- | --- | --- | --- |
| BUSINESS_INPUT | S1 | DHIS2_IMPORT_REPORT | S4 |
| NRR | S3 | SNAPSHOT | S4 |
| AGENT_OUTPUT | S3 | EXECUTION_PLAN | S4 |
| BLUEPRINT | S2 | EXECUTION_REPORT | S4 |
| METADATA_PACKAGE | S2 | POST_VERIFICATION_REPORT | S4 |
| DATASTORE_CHANGESET | S2 | CAPABILITY_PROFILE | S4 |
| OPENAPI | S2 | DISCOVERY_RESULT | S4 |
| UI_SPECIFICATION | S2 | DIFF_REPORT | S2、S4 |
| FIXTURE_PACKAGE | S2 | CHANGE_IMPACT_REPORT | S1 |
| VALIDATION_REPORT | S2 | PROJECT_EXPORT | S1 |
| FRONTEND_PACKAGE | S2 | HANDOFF_CHECK_REPORT | S2 |
| MANIFEST | S2 | | |

  （新增类型 AGENT_OUTPUT、EXECUTION_PLAN、DIFF_REPORT、HANDOFF_CHECK_REPORT、CHANGE_IMPACT_REPORT 的理由：它们是 Gate 绑定对象或审计依据，必须以不可变产物存在。）
- **FD-ART-003**【新增】产物内容不可变；“修改”一律创建新产物并以前一版本 ID 关联，版本号自动递增。理由：总体 SRS P-05 与 FR-DATA-002 的统一实现。
- **FD-ART-004**【直引】（NFR-REL-004）创建产物时，内容必须先写入对象存储并回读校验 SHA-256，校验通过后才登记元数据；调用方可提供期望哈希，不一致时拒绝。
- **FD-ART-005**【新增】产物创建分两步：内容写入（外部 I/O 函数，事务外执行，返回暂存句柄）与元数据登记（可在调用方工作单元内执行）。暂存超过 24 小时未登记的内容由清理任务删除。理由：满足 FD-DB-004，同时让“产物登记 + 状态变更 + 审计”处于同一事务。
- **FD-ART-006**【直引】（NFR-PERF-007、NFR-CAP-003）内容以流式写入与读取，不在内存中完整缓冲；产物大小默认不受限；可按产物类型配置可选的上限（未配置即不限）。用户上传输入的大小限制由 S1 在上传入口执行（总体 SRS NFR-CAP-003）。
- **FD-ART-007**【直引】（FR-DATA-002）产物状态：DRAFT、PROPOSED、APPROVED、SUPERSEDED。S0 提供状态变更函数；以前一版本 ID 创建新产物时，前一版本自动置为 SUPERSEDED。
- **FD-ART-008**【调整】（FR-POL-001）创建产物必须声明数据分级；声明了来源产物时，分级不得低于来源中的最高级别，除非提供脱敏记录引用（由 S3 生成；S0 只记录引用，不校验其内容）。违反时抛出 `ARTIFACT_ERROR`。
- **FD-ART-009**【新增】读取产物内容时必须同时返回其数据分级。S0 不判断内容能否发送给 LLM。理由：S3 策略判定需要分级信息。
- **FD-ART-010**【直引】（FR-DATA-007）PHI_PII 分级的产物支持清除内容：删除对象存储中的内容，保留元数据与哈希，标记已清除，写审计。已清除产物被读取内容时抛出 `ARTIFACT_PURGED`。
- **FD-ART-011**【直引】（FR-DATA-004、FR-DATA-005）产物默认永久保留；支持按项目执行保留期清理（只删除内容，保留元数据）；保留期规则由调用方（S1）传入。
- **FD-ART-012**【调整】（19.2 `/artifacts/{id}/diff`）提供同类型两个产物之间的通用 Diff：文本按行、JSON 按结构路径。对象级语义 Diff 不在 S0。
- **FD-ART-013**【新增】提供来源关系查询：向上追溯来源产物链、向下查询派生产物。理由：总体 SRS FR-UI-004 与 AC-BP-01 的追溯需求。
- **FD-ART-014**【新增】提供按条件查询产物元数据：项目、工作流、类型、状态、创建方、时间区间，支持分页与排序。理由：S1 列表接口的基础（总体 SRS FR-IF-004）。
- **FD-ART-015**【调整】（FR-BP-009）提供产物打包与解包：把一组产物（元数据与内容）打包为单一归档文件，附清单与每个产物的哈希；每个产物的元数据与内容分别带哈希；解包时先逐一校验清单内全部哈希，任一不一致即整体失败且不登记任何产物；通过校验后仍须逐个对象写入并回读校验哈希，回读不符同样整体失败；失败时删除本次已写入的对象，删除不掉的键随错误 `details` 返回。可选择排除 PHI_PII 内容（只保留元数据与哈希）。【新增】解包以“导入”方式登记到目标项目：保留原创建方、分级、状态、构建记录与版本号，按包内映射恢复来源关系与前一版本关系，并返回“原产物 ID → 新产物 ID”映射（构建记录中引用的产物 ID 由调用方按映射改写）。理由：S1 项目导入与 S2 重新编译哈希一致（CP-PRJ-009、BP-EXP-002、AC-DATA-01）。

### 3.3 审计服务（FD-AUD）

- **FD-AUD-001**【直引】（C-11、NFR-REL-005）审计记录必须在调用方的工作单元内写入；写入失败时整个工作单元回滚。
- **FD-AUD-002**【调整】（FR-SEC-012）审计记录字段：ID、全局序号、发生时间、操作者类型（USER / SYSTEM）、操作者名称、来源子系统、事件类型、对象类型、对象 ID、项目 ID、变更前摘要、变更后摘要、correlationId、是否经过脱敏、`prevHash`、`hash`。
- **FD-AUD-003**【直引】（FR-SEC-012）审计记录只追加；S0 必须在数据库层阻止对审计表的更新与删除（链接字段的首次填写除外）。
- **FD-AUD-004**【调整】（FR-SEC-013）审计记录以单一全局哈希链串联：`hash = SHA-256(prevHash ‖ 规范化记录内容)`，按全局序号链接；序号在链接时按当时已提交且可见的待链接记录的 (发生时间, ID) 顺序分配。链接可以在事务提交后由链接任务完成，从提交到完成链接的延迟不得超过 10 秒。理由：若在业务事务内直接计算哈希链，所有事务将在链头上串行，影响并发。
- **FD-AUD-005**【直引】（AC-SEC-01）提供完整性校验：支持全链与序号区间校验，报告第一处断裂的序号与原因（内容被改、记录缺失、链接缺失）。
- **FD-AUD-006**【新增】提供定时全链校验能力（由 S1 按日调度），结果写入审计。理由：主动发现篡改。
- **FD-AUD-007**【新增】写入前检测凭证特征（Authorization 头、Cookie、令牌、密码字段、客户端密钥）；检测到时以脱敏后的内容写入并标记已脱敏，同时输出告警日志。理由：总体 SRS FR-SEC-008 需统一兜底。
- **FD-AUD-008**【新增】提供审计查询：按项目、对象（类型 + ID）、事件类型、操作者、correlationId、时间区间，支持分页。理由：总体 SRS 19.2 审计接口与 FR-SEC-014 追溯查询的基础。
- **FD-AUD-009**【直引】（FR-DATA-005）审计记录不随项目删除而删除；S0 不提供删除审计记录的函数。
- **FD-AUD-010**【新增】事件类型以“子系统.对象.动作”命名（如 `cp.gate.approved`），各子系统在初始化时登记自己的事件类型；写入未登记的类型时报错。理由：保证总体 SRS 21.4 列出的必需审计事件可被检查。

### 3.4 幂等存储（FD-IDEM）

- **FD-IDEM-001**【新增】提供幂等记录的获取与完成：以（作用域、幂等键）标识；记录请求摘要哈希、状态（IN_PROGRESS、COMPLETED、FAILED）、结果引用与过期时间。理由：总体 SRS FR-IF-001、NFR-CON-003 需要统一实现，S1 与 S4 都要使用。
- **FD-IDEM-002**【新增】同键同摘要：若已完成则返回首次结果，若进行中则返回“进行中”；同键不同摘要抛出 `IDEMPOTENCY_CONFLICT`。
- **FD-IDEM-003**【新增】过期时间由调用方指定；作用域为 DHIS2 执行类（`dx.execution`）的记录禁止过期删除。理由：防止重复 COMMIT（总体 SRS FR-EXE-009）。

### 3.5 对象存储适配器（FD-OBJ）

- **FD-OBJ-001**【直引】（19.5、24.1）提供 S3 兼容对象存储适配器与本地文件系统适配器（开发用），两者实现同一接口，由配置选择。0.1.2 起明确 location/key 往返规则：本地后端以绝对路径为根，写入产生的 location 形如 `file:///abs/root/<key>`（不得把绝对路径剥成相对根）；S3 后端的前缀属于桶侧，location 形如 `s3://bucket/<prefix>/<key>`，回读时不得再加一次前缀；内存后端为 `memory://<key>`。S0 提供 `key_from_location(location, store)` 按产生该 location 的存储反推 key，读取、清除、保留期清理与暂存清理必须经它还原 key。
- **FD-OBJ-002**【新增】对象路径由 S0 按“项目 / 产物类型 / 产物 ID”生成；子系统不得直接操作对象存储。理由：D-12 统一托管。

### 3.6 Secret 适配器（FD-SEC）

- **FD-SEC-001**【调整】（FR-SEC-006、19.5）提供 Secret 引用解析接口，支持 Vault、云 Secret Manager 与（仅开发环境的）环境变量后端；引用格式如 `vault://path`。环境变量后端必须在生产环境不可用：`APP_ENV=production` 时构造 `EnvSecretBackend` 或调用 `install_development_backends()` 立即抛出 `SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION`；生产必须显式注册 Vault/云后端。
- **FD-SEC-002**【直引】（FR-SEC-008）解析得到的值禁止写入日志、异常信息与审计；S0 以专用类型包装 Secret 值，其字符串表示恒为掩码。
- **FD-SEC-003**【新增】引用解析失败时抛出 `SECRET_UNAVAILABLE`，错误信息只含引用，不含任何值。理由：可诊断且不泄露。
- **FD-SEC-004**【新增】（M3）提供 Secret 写入：在指定路径写入一次性生成的值并返回引用。后端不可写（环境变量后端、只读 Vault/云传输）或无法唯一推断可写后端时抛出 `SECRET_STORE_UNSUPPORTED`；`SECRET_UNAVAILABLE` 只表示引用解析不到。理由：总体 SRS FR-COV-008 要求演示用户凭证一次性生成并存入 Secret Manager（由 S4 在 Fixture 导入时使用）。

### 3.7 公共工具（FD-COM）

- **FD-COM-001**【调整】（19.2.1）错误模型：`FactoryError` 基类，字段为错误码、消息、是否可重试、来源阶段、关联产物 ID、详情；错误码目录包含总体 SRS 19.2.1 的全部错误码，并允许子系统登记新错误码。可重试判定遵循总体 SRS 19.2.2。
- **FD-COM-001a**【新增】S0 自身的内置错误码（所有者 S0，进程启动时即可用，并由 S0 初始化时写入错误码目录）至少包括：`TRANSACTION_MISUSE`、`CONCURRENCY_ERROR`（可重试）、`ARTIFACT_ERROR`、`ARTIFACT_NOT_FOUND`、`ARTIFACT_PURGED`、`ARTIFACT_HASH_MISMATCH`、`ARTIFACT_READBACK_MISMATCH`（可重试）、`ARTIFACT_TOO_LARGE`、`ARTIFACT_CLASSIFICATION_DOWNGRADE`、`ARTIFACT_OWNERSHIP_VIOLATION`、`ARTIFACT_TYPE_UNKNOWN`、`ARTIFACT_TYPE_MISMATCH`、`ARTIFACT_NO_CONTENT`、`ARTIFACT_NOT_PURGEABLE`、`ARTIFACT_VERSION_CONFLICT`、`ARTIFACT_STAGING_NOT_FOUND`、`ARTIFACT_STAGING_MISMATCH`、`INVALID_STATUS_TRANSITION`、`IDEMPOTENCY_CONFLICT`、`SECRET_UNAVAILABLE`、`SECRET_STORE_UNSUPPORTED`、`SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION`、`CANCELLED`、`CHAIN_INTEGRITY_ERROR`、`PACK_INTEGRITY_ERROR`、`OBJECT_NOT_FOUND`、`CONFIGURATION_ERROR`、`MIGRATION_ERROR`、`AUDIT_OUTSIDE_TRANSACTION`、`AUDIT_EVENT_TYPE_UNKNOWN`、`REGISTRY_CONFLICT`；除标注者外均不可重试。其中 `ARTIFACT_TYPE_MISMATCH` 表示“产物存在但类型不符合调用要求”，其他子系统可直接抛出，`details` 由抛出方定义。同一错误码或同一 ID 前缀以不同所有者重复登记时，必须抛出 `REGISTRY_CONFLICT`（`details` 含既有所有者与请求所有者）。理由：其他子系统依赖这些码的所有权与可重试属性，必须在 S0 中有唯一的登记。
- **FD-COM-002**【新增】ID 生成：带类型前缀的 ULID；前缀由各子系统登记。前缀不得改属：以不同所有者重复登记时抛出 `REGISTRY_CONFLICT`；未登记的前缀生成 ID 时报错。理由：00 文档 6.7。
- **FD-COM-003**【调整】（FR-CMP-002）提供两种确定性 JSON 序列化：① 规范化形式（键排序、紧凑无空白、UTF-8），用于对数据结构计算哈希；② 文件形式（键排序、固定 2 空格缩进、UTF-8、LF、末尾换行），用于写入 JSON 文件类产物。哈希输出格式 `sha256:<hex>`。对数据结构求哈希一律使用规范化形式；产物的内容哈希是其文件字节的哈希。全部子系统必须使用这两个工具，不得自行序列化后求哈希。
- **FD-COM-004**【调整】（FR-CMP-001）时钟接口：系统时钟与固定时钟两种实现；需要确定性的代码只能使用调用方传入的时钟或构建上下文中的时间。
- **FD-COM-005**【新增】操作上下文：以上下文变量保存 correlationId、操作者、项目 ID、工作流 ID、阶段 ID 等，日志、审计与产物创建自动读取。0.1.2 的实际读取范围是：日志与审计读取上下文缺省值；产物创建读取操作者，并在 `workflow_id` 未显式提供时回退到上下文；`project_id` 仍由调用方显式传入。理由：总体 SRS NFR-OBS-001 的字段在库调用链上的传递方式。
- **FD-COM-006**【直引】（NFR-OBS-001、NFR-OBS-002）结构化 JSON 日志与 OpenTelemetry 追踪工具；日志自动附加操作上下文字段。
- **FD-COM-007**【直引】（FR-SEC-008）日志脱敏：自动对 Authorization 头、Cookie、令牌、密码、客户端密钥字段进行掩码。
- **FD-COM-008**【直引】（FR-DEP-002）配置加载：从环境变量与配置文件读取，至少支持 `APP_ENV`、`DATABASE_URL`、`OBJECT_STORE_URL`、`SECRET_BACKEND`、`OTEL_EXPORTER_OTLP_ENDPOINT`；各子系统可声明自己的配置项。

### 3.8 进度与取消协议（FD-PRG）

- **FD-PRG-001**【新增】定义 `ProgressReporter` 协议：报告当前步骤、完成比例（可空）、消息与结构化数据；S3 / S4 的长任务函数调用它，S1 实现它（写入事件流并发送 Temporal 心跳）。理由：子系统之间不使用消息通讯，Agent 与执行的实时状态通过进度回调提供（对应总体 SRS 的 `agent.*`、`execution.progress` 事件）。
- **FD-PRG-002**【新增】定义 `CancellationToken` 协议：长任务在安全点检查取消状态，检测到取消时抛出 `CANCELLED`。理由：总体 SRS FR-WF-004 取消语义的实现基础。

### 3.9 测试套件（FD-TST）

- **FD-TST-001**【新增】提供：PostgreSQL 测试容器夹具（自动建立全部 schema 并执行迁移）、内存对象存储、固定时钟、内存 Secret 后端、记录型 `ProgressReporter`、可触发的 `CancellationToken`。0.1.2 起夹具建立 `core/cp/bp/ai/dx` 五个 schema，只重建 `core`（其余 schema 归各子系统），并执行 `core` 迁移；`FixedClock` 同时从 `factory_foundation.testing` 导出；未设置 `FACTORY_TEST_DATABASE_URL` 时使用 `testcontainers[postgres]`（由 `testing` 可选依赖提供）。理由：各子系统独立开发与测试的共同前提（00 文档 6.1）。

---

## 4. 业务规则汇总

| 编号 | 规则 | 来源 |
| --- | --- | --- |
| FD-BR-01 | 产物内容不可变；分级不低于来源最高级别（除非有脱敏记录引用） | 【调整】FR-DATA-002、FR-POL-001 |
| FD-BR-02 | 产物内容回读校验哈希后才能登记（普通创建与包解包写入两条路径都适用） | 【直引】NFR-REL-004 |
| FD-BR-03 | 只有类型所有者子系统可以创建该类型产物 | 【新增】 |
| FD-BR-04 | 审计与业务同事务；只追加；哈希链；不随项目删除 | 【直引】NFR-REL-005、FR-SEC-012/013、FR-DATA-005 |
| FD-BR-05 | 外部 I/O 函数禁止在工作单元中调用 | 【新增】 |
| FD-BR-06 | 禁止跨 schema 外键；检查器须能识别各 schema 独立 `MetaData` 中的点分目标名 | 【新增】D-08 |
| FD-BR-07 | Secret 值不得出现在日志、异常、审计中 | 【直引】FR-SEC-008 |
| FD-BR-08 | DHIS2 执行类幂等记录不得过期删除 | 【新增】 |

---

## 5. 数据需求（schema `core`）

| 实体 | 最少字段 | 来源 |
| --- | --- | --- |
| ArtifactType | type、ownerSubsystems（允许的所有者子系统列表）、maxSize（可空，空表示不限） | 【新增】FD-ART-002 |
| Artifact | 见 FD-ART-001 | 【调整】23.1 |
| ArtifactSource | artifactId、sourceArtifactId | 【新增】FD-ART-013 |
| ArtifactStaging | handle、contentLocation、contentHash、size、createdAt | 【新增】FD-ART-005 |
| AuditEventType | eventType、ownerSubsystem | 【新增】FD-AUD-010 |
| AuditEvent | 见 FD-AUD-002 | 【调整】23.1 |
| AuditChainState | lastLinkedSeq、lastHash | 【新增】FD-AUD-004 |
| IdempotencyRecord | scope、key、requestDigest、status、resultRef、expiresAt | 【新增】FD-IDEM-001 |
| ErrorCode | code、ownerSubsystem、retryable | 【新增】FD-COM-001 |
| IdPrefix | prefix、ownerSubsystem | 【新增】FD-COM-002 |

---

## 6. 公共 API 概要（`factory_foundation.api`）

| 分组 | 函数（概要） |
| --- | --- |
| 启动与清理 | `initialize_foundation()`（登记 S0 内置的错误码、ID 前缀与审计事件类型并加载各登记表）、`run_housekeeping()`（过期暂存、待删除对象重试、过期幂等记录）、`CORE_MIGRATION_CONFIG` |
| 事务 | `configure_engine()`、`get_engine()`、`new_session()`、`unit_of_work()`、`after_commit()`、`current_session()`、`has_active_uow()`、`external_io` 标记、`VersionedAggregate`/`check_version()`/`bump_version()` |
| 产物 | `register_artifact_types()`、`list_artifact_types()`、`stage_content(stream, expected_hash)`、`register_artifact(staging_handle, meta)`、`get_artifact()`、`open_content()`、`read_content_bytes()`、`query_artifacts()`、`set_status()`、`lineage()`、`version_history()`、`purge_content()`、`apply_retention()`、`cleanup_expired_staging()`、`diff()`、`pack()`、`unpack()` |
| 审计 | `register_audit_event_types()`、`list_audit_event_types()`、`is_registered_event_type()`、`audit(event)`、`get_audit()`、`query_audit()`、`verify_chain(range)`（别名 `verify_audit_chain`）、`run_full_chain_verify()`、`link_pending()` |
| 幂等 | `idempotency.begin(scope, key, digest)`、`idempotency.complete()`、`idempotency.fail()`、`idempotency.get()`、`cleanup_expired_idempotency()`、`IdempotencyStatus`、`DHIS2_EXECUTION_SCOPE` |
| Secret | `resolve_secret(ref)`、`store_secret(path, value)`（M3）、`install_development_backends()`、`register_secret_backend()`、`Secret`/`SecretBackend`/`SecretResolver`、`MemorySecretBackend` |
| 对象存储 | `get_object_store()`、`configure_object_store()`、`configure_object_store_from_url()`、`build_object_path()`、`ObjectStore`；key 还原 `key_from_location(location, store)`（实现模块 `factory_foundation.objectstore.base`） |
| 工具 | `new_id(prefix)`、`new_correlation_id()`、`canonical_json()`、`file_json()`、`content_hash()`、`structure_hash()`、`normalize_hash()`、`Clock`/`SystemClock`/`FixedClock`、`utcnow()`、`OperationContext`、`bind_operation_context()`、`get_logger()`、`configure_logging()`、`tracer()`/`trace_span()`/`configure_tracing()`、`load_settings()`/`register_settings_schema()`、`redact_structure()`、`register_id_prefixes()` |
| 进度与取消 | `ProgressReporter`、`ProgressEvent`、`NullProgressReporter`、`CancellationToken`、`ManualCancellationToken` |
| 错误 | `FactoryError` 及子类、`ErrorMeta`、`error_catalog`、`register_error_codes()`、`raise_for_code()`、`BUILTIN_ERROR_CODES` |
| 迁移 | `run_migrations()`（含 `forward_only`/`downgrade_to`）、`current_revision()`、`check_no_cross_schema_fk()`、`find_cross_schema_fk()` |
| 测试 | `factory_foundation.testing` 中的 `FoundationHarness`、`harness`、`foundation` 夹具、`resolve_test_database_url()`、`FixedClock` 与各替身 |

---

## 7. 非功能需求

| 编号 | 要求 | 来源 |
| --- | --- | --- |
| FD-NFR-01 | 产物元数据查询 P95 < 100 ms（单项目 5,000 产物规模） | 【调整】NFR-PERF-002 |
| FD-NFR-02 | 审计写入对业务事务增加的耗时 P95 < 10 ms | 【新增】 |
| FD-NFR-03 | 审计链接延迟 ≤ 10 秒；全链校验 100 万条记录 < 10 分钟 | 【新增】 |
| FD-NFR-04 | 流式处理 100 MB 文件时进程内存增量 < 32 MB | 【调整】NFR-PERF-007 |
| FD-NFR-05 | 单元测试行覆盖率 ≥ 90% | 【调整】NFR-MNT-004 |
| FD-NFR-06 | 不依赖 S1～S4 的任何包；公共 API 语义化版本管理 | 【新增】D-17 |
| FD-NFR-07 | `core` schema 数据纳入 RPO ≤ 15 分钟、RTO ≤ 4 小时的备份范围 | 【直引】NFR-REL-007 |

---

## 8. 跨系统假设

| 编号 | 假设 |
| --- | --- |
| FD-ASM-01 | 各子系统在进程启动时完成产物类型、审计事件类型、错误码、ID 前缀的登记 |
| FD-ASM-02 | S1 负责调度定时任务（审计链接、全链校验、日常清理〔过期暂存、待删除对象重试、过期幂等记录〕、保留期清理），S0 只提供被调度的函数 |
| FD-ASM-03 | S3 在提供脱敏记录引用时保证其有效 |

---

## 9. 验收标准

| 编号 | 给定 / 当 | 则 | 里程碑 | 来源 |
| --- | --- | --- | --- | --- |
| AC-SEC-01 | 人为修改一条审计记录 | 完整性校验报告断裂位置 | M2 | 【直引】（提前至 M2，因 S0 最先交付） |
| FD-AC-01 | 内容回读哈希与期望哈希不一致 | 产物不登记，抛出错误 | M1 | 【新增】 |
| FD-AC-02 | 业务操作与审计写入同一工作单元，审计写入失败 | 业务变更全部回滚 | M1 | 【新增】 |
| FD-AC-03 | 在工作单元中调用外部 I/O 函数 | 立即抛出 `TRANSACTION_MISUSE` | M1 | 【新增】 |
| FD-AC-04 | 以 CONFIDENTIAL 来源创建 INTERNAL 派生产物且无脱敏记录 | 拒绝创建 | M1 | 【新增】 |
| FD-AC-05 | 非所有者子系统创建某类型产物 | 拒绝创建 | M1 | 【新增】 |
| FD-AC-06 | 同一幂等键不同请求摘要 | 抛出 `IDEMPOTENCY_CONFLICT` | M1 | 【新增】 |
| FD-AC-07 | 审计包含 Authorization 头 | 以掩码写入并标记已脱敏 | M1 | 【新增】 |
| FD-AC-08 | 打包后篡改归档中的一个产物再解包（或对象写入后回读哈希不符） | 解包整体失败，不登记任何产物，并清理本次已写对象 | M2 | 【新增】 |
| FD-AC-09 | 表定义中存在跨 schema 外键（含各 schema 独立 `MetaData` 的点分目标名） | 检查工具失败 | M1 | 【新增】 |

---

## 10. 里程碑

| 里程碑 | 交付内容 |
| --- | --- |
| M1（最先） | 事务、产物、审计写入与链接、幂等、对象存储、Secret、公共工具、进度与取消、迁移运行器、测试套件 |
| M2 | 审计完整性校验与定时校验、产物打包与解包、保留期清理 |
| M3 | Secret 写入（FD-SEC-004） |

---

## 11. 未决问题

以下为 0.1.2 实现核对后仍存在的差距，按 0.1.2 CHANGELOG 的“仍待裁决/仍开放”原样保留，不应在文档中当作已实现：

1. **粗码到具体码的切换未完成**：31 个 S0 内置码中，0.1.2 的 S0 代码实际抛出 11 个（`TRANSACTION_MISUSE`、`CONCURRENCY_ERROR`、`ARTIFACT_ERROR`、`ARTIFACT_PURGED`、`IDEMPOTENCY_CONFLICT`、`SECRET_UNAVAILABLE`、`SECRET_STORE_UNSUPPORTED`、`SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION`、`REGISTRY_CONFLICT`、`CANCELLED`、`MIGRATION_ERROR`），其余仅登记。分级降级/非所有者/超大小/暂存不一致、包完整性仍抛 `ARTIFACT_ERROR`；产物与对象不存在抛 `NOT_FOUND`；未登记审计事件类型抛 `VALIDATION_ERROR`；必需配置缺失抛 `INTERNAL_ERROR`。状态迁移不校验，`INVALID_STATUS_TRANSITION` 不可达；`ARTIFACT_READBACK_MISMATCH`、`PACK_INTEGRITY_ERROR`、`CONFIGURATION_ERROR`、`AUDIT_OUTSIDE_TRANSACTION`、`AUDIT_EVENT_TYPE_UNKNOWN`、`CHAIN_INTEGRITY_ERROR` 等均未启用。切换会改变 S1/S2/S4 观测到的 `errorCode` 与 REST 映射，需跨子系统协调。
2. **`ENVIRONMENT_LOCKED` 归属冲突**：按 S0→S1→S2→S3→S4 顺序初始化时，S1 与 S4 以不同所有者登记同一码，后者抛 `REGISTRY_CONFLICT`，归属待裁定。
3. **打包/解包加固**：归档清单 `manifest.json` 自身未被认证（`archive_hash` 由 `pack` 返回，需调用方另行保存并比对）；`pack`/`unpack` 仍全量缓冲归档，与 FD-ART-006/FD-NFR-04 的流式要求不符。
4. **数据约束与并发**：`artifact` 缺 `(prev_version_id, version)` 唯一约束；`VersionedAggregate` 无模型使用，乐观并发依赖调用方自觉（见 FD-DB-005）。
5. **Secret 细节**：`Secret` 的 `str()`/`repr()`/`for_json()` 为掩码，但 `dataclasses.asdict(secret)` 仍可取到 `_value`；`for_json()` 未接线到序列化器。`SECRET_BACKEND` 配置项当前只被读取，S0 不按它自动装配后端。
6. **事务边界**：S0 自身的保留期清理、暂存清理与待删除重试在打开的工作单元内执行对象存储 I/O，与 FD-BR-05 相悖；`apply_retention` 由自身检查拒绝 UoW，`cleanup_expired_staging` 无此检查。
7. **未覆盖验证**：FD-AUD-004 的 10 秒链接预算无测试；FD-COM-005 的 `projectId` 不从操作上下文自动读取（改签名属破坏性变更）。

---

## 版本变更记录

- **V1.0.2**（对照 0.1.2 实现反向校准：补充 object store location/key 往返规则与 `key_from_location`；明确解包写入后回读校验与失败清理；补全生产环境 Secret 后端限制与 `SECRET_STORE_UNSUPPORTED` 语义；`REGISTRY_CONFLICT` 覆盖错误码与 ID 前缀两类登记；明确跨 schema 外键检查器按点分目标名跨 `MetaData` 解析；明确迁移降级条件、测试夹具建立全部 schema、乐观并发与操作上下文的实际边界；§6 API 清单与 §11 未决问题同步 0.1.2 现状）。
- **V1.0.1**：新增 FD-COM-001a（S0 内置错误码目录）。

---

## 附录：源文档引用表

| # | 文档 | 类型 | 引用范围 | 处理方式 |
| --- | --- | --- | --- | --- |
| 1 | 总体 SRS V1.4.0 | 总体需求规格 | 4.3（Artifact Service、Audit Service）、19.2.1～19.2.2、19.5、21.2、21.4、22.1、22.5、23、24.1 | 抽取改写 |
| 2 | 总体 SRS V1.4.0 | 总体需求规格 | 20.1 数据分级 | 章节引用 |
| 3 | 00 总体架构与集成约定 V1.3.0 | 共享知识库 | 全文 | 全文引用 |
