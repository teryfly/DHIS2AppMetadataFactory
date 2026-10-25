# CHANGELOG — factory-foundation (S0 公共基础库)

版本号即 `pyproject.toml` 的 `project.version` 与 `factory_foundation.__version__`。
依 00 文档 6.1：不兼容变更提升主版本，本次为补丁发布。

## 0.1.2 — 全文对照 SRS 复核后补上的功能缺口

依用户要求，对照 S0 SRS V1.0.1 全文（FD-DB/ART/AUD/IDEM/OBJ/SEC/COM/PRG/TST、第 4/5/6/7/9 节）
逐条复核，而不是只核 FD-COM-001a。下列是复核中确认「条文要求而代码未兑现」的项，本轮全部
只改 S0，未新增迁移。

### 修复的功能缺口

- **FD-OBJ-001 本地与带前缀 S3 后端读不回来**：`file:///var/data` 曾被剥成相对根 `var/data`，
  而 `LocalFileObjectStore` 又拒绝绝对 key，于是本后端只能写不能读（`open_content`、
  `purge_content`、保留期清理、暂存清理四条路径全断）；`s3://bucket/prefix/key` 回读时被
  `_full_key` 再加一次前缀。现在 location→key 的还原交给 `objectstore.base.key_from_location
  (location, store)`，按产生该 location 的那个存储的 root/prefix 反推。新增本地后端端到端
  产物读写+清除测试与前缀还原测试。
- **FD-BR-02 / FD-ART-004 在解包路径上未兑现**：`unpack()` 写入对象后直接登记，从不回读校验，
  与「内容回读校验哈希后才能登记」矛盾。现在 `unpack()` 逐个对象流式回读并与清单哈希比对，
  不符即整体失败并回滚已写对象。
- **FD-DB-006 跨 schema 外键检查器在最主要的场景下是坏的**：每个 schema 有自己的 `MetaData`
  （FD-DB-002），声明成 `ForeignKey("core.artifact.id")` 的目标无法在本 metadata 里解析，
  `fk.referred_table` 直接抛 `NoReferencedTableError`，检查器因此崩溃而不是报告违规；退回到
  「按源 schema 判定」又会漏掉跨 schema 边。现在按点分目标名解析，跨 metadata 的情形被正确
  报告，新增该场景的测试。
- **FD-SEC-001 生产环境仍可用 env Secret 后端**：条文写明 env 后端「仅开发环境」，但没有任何
  守卫。现在构造 `EnvSecretBackend` 或调用 `install_development_backends()` 时若
  `APP_ENV=production` 即抛 `SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION`。
- **FD-SEC-004 的 `SECRET_STORE_UNSUPPORTED` 此前从不抛出**：不可写后端一律报
  `SECRET_UNAVAILABLE`（语义是「引用解析不到」）。env 后端、只读 vault/cloud 传输、以及
  无法推断唯一可写后端三种情形现在都报 `SECRET_STORE_UNSUPPORTED`。
- **FD-COM-001a 的 `REGISTRY_CONFLICT` 覆盖到 ID 前缀登记**：前缀被别的所有者重复登记时原报
  `INTERNAL_ERROR`，与错误码目录的同一语义不一致，现统一为 `REGISTRY_CONFLICT`。
- **FD-TST-001 夹具只建 `core`**：条文要求「自动建立全部 schema 并执行迁移」。`FoundationHarness
  .reset()` 现在把 `core/cp/bp/ai/dx` 全部建立（只 drop `core`，其余归各子系统），
  `testcontainers` 补进 `testing` 与 `dev` 可选依赖，`FixedClock` 从 `factory_foundation.testing`
  导出。

### 契约可见的变化（调用方需知）

- `store_secret()` 打到只读后端时 `errorCode` 由 `SECRET_UNAVAILABLE` 变为
  `SECRET_STORE_UNSUPPORTED`；生产环境装配 env 后端由「静默可用」变为启动即失败。
- ID 前缀所有权冲突的 `errorCode` 由 `INTERNAL_ERROR` 变为 `REGISTRY_CONFLICT`。
- 跨 schema 外键检查器现在能抓到此前漏掉的声明：子系统 schema 里若真有跨 schema 外键，
  其测试会从「通过」变成失败——这正是 FD-BR-06 想要的效果，但需要各子系统一次核对。

### 复核中确认为「已实现」的项

产物类型目录 23 项与所有者（含 `NRR`→S3、`DIFF_REPORT`→S2+S4）、`FD-ART-001` 的 18 个元数据
字段、`FD-ART-007` 自动 SUPERSEDED、`FD-ART-008/009/011/012/013/014`、`FD-AUD-002` 的 16 个
字段、`FD-AUD-003` 的数据库层触发器（只追加、仅允许首次填链接字段）、`FD-AUD-004/005/006`、
`FD-IDEM-001..003`（`dx.execution` 不过期）、`FD-OBJ-002` 路径生成、`FD-COM-003` 两种 JSON 形式
与 `sha256:` 格式、`FD-COM-004/006/007`、`FD-PRG-001/002`、§6 全部 41 个 API 名称可导入。
`FD-NFR-05` 覆盖率实测 **91%**（旧 `.coverage` 工件的 79.5% 是 9 月 28 日的过期数据）。

### 仍待你裁决（本轮未做）

1. **粗码→具体码的切换**：23 个内置码仍只有 8 个由 S0 抛出。分级降级/超大小/暂存不一致抛
   `ARTIFACT_ERROR`、产物与对象不存在抛 `NOT_FOUND`、未登记审计事件类型抛 `VALIDATION_ERROR`
   （条文对应 `AUDIT_EVENT_TYPE_UNKNOWN`）、状态迁移不合法抛 `ARTIFACT_ERROR`
   （`INVALID_STATUS_TRANSITION` 不可达）、打包完整性抛 `ARTIFACT_ERROR`
   （`PACK_INTEGRITY_ERROR` 从不抛出）、必需配置缺失抛 `INTERNAL_ERROR`
   （`CONFIGURATION_ERROR` 从不抛出）。这些改动改变调用方看到的 `errorCode` 与其 REST 映射，
   需要一轮协调。
2. **S1 与 S4 抢 `ENVIRONMENT_LOCKED`**：离线模拟 S0→S1→S2→S3→S4 的登记顺序，
   S1（owner S1、可重试 True）与 S4（owner S4、False）仍相撞，后初始化者抛
   `REGISTRY_CONFLICT`。环境锁归 `dx` schema（S4）更顺，S1 只应抛出不应登记；需你裁定归属。
3. **规格未强制但值得考虑的加固**：归档清单 `manifest.json` 自身未被认证（`archive_hash` 只在
   打包时算出，解包无法校验，改清单可绕过逐条哈希）；`pack`/`unpack` 全量缓冲内存，与
   FD-ART-006/FD-NFR-04 的流式要求不符；`FD-ART-003` 缺 `(prev_version_id, version)` 唯一约束；
   `VersionedAggregate` 没有任何模型使用，乐观并发是「调用方自觉」而非 `WHERE version=` 比较后写；
   `Secret` 值可经 `dataclasses.asdict` 出明文且 `for_json()` 未接线；S0 自己的保留期清理/暂存
   清理/待删除重试在打开的工作单元内做对象存储 I/O，与 FD-BR-05 相悖；`FD-AUD-004` 的 10 秒链接
   预算无测试；`FD-COM-005` 的 `projectId` 未从操作上下文自动读取（改签名即破坏性）。

### 门禁

192 项测试通过（0.1.1 是 181 项），行覆盖 91%，mypy strict 无问题，ruff check 干净。
本轮**未**运行 `ruff format`。

## 0.1.1 — S0 内置错误码目录补齐（FD-COM-001a，依据 S0 SRS V1.0.1）


FD-COM-001a 是 V1.0.1 新增条文：S0 自有的错误码必须由 S0 唯一登记（所有者 S0、
进程启动即可用、初始化时写入 `core` 的错误码目录），因为其他子系统依赖其所有者与
可重试属性。核对后原目录缺 23 个码，另有 1 个码的可重试属性与条文相反。

### 新增

- `BUILTIN_ERROR_CODES`（`common/errors.py`，经 `api` 导出）：条文列出的 31 个码与
  可重试属性的唯一来源。补齐的 23 个码为 13 个 `ARTIFACT_*`（`NOT_FOUND`、
  `HASH_MISMATCH`、`READBACK_MISMATCH`、`TOO_LARGE`、`CLASSIFICATION_DOWNGRADE`、
  `OWNERSHIP_VIOLATION`、`TYPE_UNKNOWN`、`TYPE_MISMATCH`、`NO_CONTENT`、
  `NOT_PURGEABLE`、`VERSION_CONFLICT`、`STAGING_NOT_FOUND`、`STAGING_MISMATCH`）、
  `INVALID_STATUS_TRANSITION`、`SECRET_STORE_UNSUPPORTED`、
  `SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION`、`CHAIN_INTEGRITY_ERROR`、
  `PACK_INTEGRITY_ERROR`、`OBJECT_NOT_FOUND`、`CONFIGURATION_ERROR`、
  `AUDIT_OUTSIDE_TRANSACTION`、`AUDIT_EVENT_TYPE_UNKNOWN`、`REGISTRY_CONFLICT`。
  其余 8 个（`TRANSACTION_MISUSE`、`CONCURRENCY_ERROR`、`ARTIFACT_ERROR`、
  `ARTIFACT_PURGED`、`IDEMPOTENCY_CONFLICT`、`SECRET_UNAVAILABLE`、`CANCELLED`、
  `MIGRATION_ERROR`）原本已在目录中。
- `RegistryConflictError`（码 `REGISTRY_CONFLICT`，经 `api` 导出）。

### 行为变更（无签名变化）

- **`CONCURRENCY_ERROR` 可重试性由「否」改为「是」**，`ARTIFACT_READBACK_MISMATCH`
  同为可重试；目录内其余码均不可重试。S0 自身代码不按可重试性分支，实际重试仍由
  S1 的 Temporal 活动策略决定。
- **`error_catalog.register()` / `register_error_codes()` 现在拒绝同一码的跨所有者
  重复登记**，抛 `REGISTRY_CONFLICT`（`details` 含 `code` / `existingOwner` /
  `requestedOwner`），且被拒登记不改动目录。同所有者重复登记仍是合并，
  `initialize_foundation()` 依旧可重入。
- `initialize_foundation()` 把 31 个码连同所有者与可重试属性写入 `core.error_code`
  并覆写陈旧行，使 S2 等曾临时登记 `ARTIFACT_TYPE_MISMATCH` 留下的行回到所有者 S0。
  持久化函数与表名均未改，本次未新增迁移。

### 本轮未做：把抛出点换成这些具体码

31 个码中只有 8 个是 S0 自身代码当前真正抛出的（`TRANSACTION_MISUSE`、
`CONCURRENCY_ERROR`、`ARTIFACT_ERROR`、`ARTIFACT_PURGED`、`IDEMPOTENCY_CONFLICT`、
`SECRET_UNAVAILABLE`、`CANCELLED`、`MIGRATION_ERROR`）。其余 23 个本次只完成登记：
S0 在语义对应的抛出点仍抛粗码——分级降级/非所有者/超大小/暂存不一致等抛
`ARTIFACT_ERROR`，产物与对象不存在抛 `NOT_FOUND`，未登记审计类型抛 `VALIDATION_ERROR`，
必需配置项缺失抛 `INTERNAL_ERROR`。改成具体码会改变 S1/S2/S4 观测到的 `errorCode` 与其 REST
映射，属跨子系统协调项，不在补丁版里悄悄做。`Doc/error-codes.md` 的 `ARTIFACT_NOT_FOUND`
与 `OBJECT_NOT_FOUND` 两行已标注这一现状。

### 对其他子系统的影响

- **S2**：`ARTIFACT_TYPE_MISMATCH` 归 S0 后，`BP_ERROR_CODES` 中的临时登记会让
  `initialize()` 抛 `REGISTRY_CONFLICT`。请删除 `registries.py` 中该条目，并把依赖的
  `retryable` 语义改为读取 S0 目录。抛出方仍可 `raise FactoryError(code=...)`，
  `details` 由抛出方定义。
- **S1**：`S1_ERROR_CODES` 以所有者 S1 重登了 23 个码（8 个属 FD-COM-001a 内置码，
  15 个属总体 SRS 19.2.1 中归 S0/S2/S3/S4 的码）。升级后 `initialize()` 会在第一个
  重登码上失败。需要收窄该表，只登记 S1 自有码（如 `OPERATOR_REQUIRED`、`INVALID_STATE`、
  `PERMISSION_DENIED` 等）；其余跨子系统码已由 S0 预置，无需重登即可识别与判定重试。

### 测试与追溯

`test_common_tools.py` 逐码参数化断言登记、所有者与可重试；新增冲突、同所有者合并、
`register_error_codes` 拒绝接管内置码、`ARTIFACT_TYPE_MISMATCH` 可由子系统抛出等用例。
`test_bootstrap.py` 新增 31 码逐个落库、可重试码只有两个、陈旧行被覆写回来等断言。
`Doc/error-codes.md` 重写目录（S0 内置码与其余预置码分列），`Doc/traceability.md`
补 FD-COM-001a 行。181 项测试、mypy strict、ruff 全部通过。

### 一处非本意的全仓库格式化

本轮误跑 `ruff format src tests`，重排了 31 个与需求无关文件的排版（仅换行与引号，
不改语义；仓库无 VCS，无法回退）。后续请对该仓库只跑 `ruff check`，或在独立提交中
确认这批格式化。
