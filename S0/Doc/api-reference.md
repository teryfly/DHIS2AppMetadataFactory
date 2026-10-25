# 公共 API 参考 — `factory_foundation.api`

> 这是其他子系统可导入的**唯一**入口。参数/返回均为 Pydantic v2 模型或标准类型，
> 全程通过 mypy strict。签名与实现一致，若发现出入以代码为准。
> 本文档对照 `factory-foundation 0.1.2`（`pyproject.toml`）校准；0.1.2 的契约变化见
> `../CHANGELOG.md` 与本文各节标注。

约定：
- **外部 I/O 函数**（`@external_io`）标注 ⛔ —— 不得在 `unit_of_work()` 内调用，否则立即 `TRANSACTION_MISUSE`。
- **事务函数**标注 🧾 —— 需要一个活动工作单元（用 `session` 或内部 `current_session()`），
  以便"状态变更 + 审计"同事务提交。
- `session` 省略时：读取类自动用活动 UoW 或开一个只读会话；写入类（🧾）要求处于 UoW 内。

## 1. 启动与清理

| 函数 | 说明 |
| --- | --- |
| `initialize_foundation(*, owner=Subsystem.S0) -> None` | 将 S0 内置错误码、ID 前缀、审计事件类型与产物类型目录加载/持久化到 `core`。幂等，可重复调用；会把当前进程错误码目录（含 S0 预置的跨子系统码）整体写入 `core.error_code` 并覆写陈旧行。 |
| `run_housekeeping(*, now=None) -> HousekeepingResult` | 过期暂存清理、待删除对象重试、过期幂等记录清理。⛔（不得在 UoW 内，否则 `TRANSACTION_MISUSE`）。由 S1 调度（FD-ASM-02）。内部各步骤会打开短事务并在其中做对象存储 I/O（已知与 FD-BR-05 的偏差，见 SRS §11）。 |
| `CORE_MIGRATION_CONFIG` | `core` schema 的迁移配置（`MigrationConfig`）。 |

`HousekeepingResult`：`staging_removed: list[str]`、`deletions_retried: list[str]`、
`idempotency_removed: list[tuple[str,str]]`、`warnings: list[str]`、`.as_dict()`。

## 2. 事务与工作单元

```python
unit_of_work(session=None) -> Iterator[Session]     # 共享事务上下文；嵌套自动并入
after_commit(callback: Callable[[], None]) -> None  # 最外层提交后执行
current_session() -> Session                        # 取活动会话；无活动 UoW → TRANSACTION_MISUSE
has_active_uow() -> bool
external_io(func) -> func                           # 标记外部 I/O 函数
is_external_io(func) -> bool
check_version(row, expected_version) -> None        # 不匹配 → CONCURRENCY_ERROR
bump_version(row, expected_version) -> int          # 校验并自增 version
class VersionedAggregate                            # ORM 混入：提供 version 列
```

> `bump_version`/`check_version` 只比较已加载实体的内存 `version` 并自增，不生成
> `WHERE version = ?` 的原子更新。调用方须在同一 UoW 内完成“读取—校验—写入”；S0 自身
> 的模型未使用该混入（SRS §11）。

引擎：
```python
configure_engine(url, *, echo=False, pool_pre_ping=True, future=True, **kw) -> Database
get_engine() -> Engine
new_session() -> Session
```

## 3. 迁移与 schema 检查

```python
run_migrations(configs=None, *, url=None, forward_only=True, downgrade_to=None) -> None
# 按 core→dx→bp→ai→cp；downgrade_to 为空即正常前向升级
# 指定 downgrade_to 时：forward_only=True → MIGRATION_ERROR；APP_ENV=production → MIGRATION_ERROR；
# 否则开发环境按逆序降级
current_revision(cfg, *, url=None) -> str | None
check_no_cross_schema_fk(metadata=None) -> None   # 存在跨 schema 外键 → SCHEMA_ERROR (FD-AC-09)
find_cross_schema_fk(metadata=None) -> list[CrossSchemaFk]
register_schema_base(schema, base) -> None
class MigrationConfig(schema, script_location, version_table="alembic_version")
```

`find_cross_schema_fk` 按外键声明的点分目标名解析 schema/表，因此每个 schema 独立
`MetaData` 时 `ForeignKey("core.artifact.id")` 也能被识别；未带 schema 的两段名按声明表
所在 schema 解释（0.1.2 修复，FD-DB-006）。

按 schema 绑定的 ORM 基类：`CoreBase` `CpBase` `BpBase` `AiBase` `DxBase`（子系统继承各自基类，
表自动落入对应 schema，FD-DB-002）。

## 4. 产物（FD-ART）

### 4.1 类型目录
```python
register_artifact_types(type_owners: Mapping[str, Sequence[str]], *, max_sizes=None, session=None) -> None
list_artifact_types(session=None) -> list[tuple[str, list[str], int|None]]
INITIAL_ARTIFACT_CATALOG: dict[str, list[str]]   # 见 artifact-catalog.md
```

### 4.2 创建（两步）
```python
# ⛔ 外部 I/O：写入对象存储 + 回读校验 SHA-256；不一致即拒绝且不留登记（FD-ART-004, FD-AC-01）
stage_content(stream, *, project_id, artifact_type, expected_hash=None,
              media_type="application/octet-stream", provisional_id=None, clock=None) -> StagingHandle

# 🧾 事务内登记（FD-ART-005/002/003/007/008）
register_artifact(staging_handle, create: ArtifactCreate, *, session=None, clock=None, content_id=None) -> ArtifactInfo
```
`register_artifact` 会：校验类型所有者（否则 `ARTIFACT_ERROR`, FD-AC-05）、类型大小上限
（FD-ART-006）、分级不低于来源最高级（否则 `ARTIFACT_ERROR`, FD-AC-04）、按 `prev_version_id`
自动递增版本并把前一版本置 `SUPERSEDED`（FD-ART-003/007）；同事务写 `core.artifact.created`
/ `core.artifact.superseded` 审计。

> 上述失败当前均抛 `ARTIFACT_ERROR`，未切换到 `ARTIFACT_OWNERSHIP_VIOLATION` /
> `ARTIFACT_TOO_LARGE` / `ARTIFACT_CLASSIFICATION_DOWNGRADE` 等具体码；这些码已登记但
> 0.1.2 不由 S0 抛出（见 `error-codes.md` 与 SRS §11）。

`ArtifactCreate` 字段：`project_id, type, media_type, data_classification, creator_subsystem,
workflow_id?, build_record?, source_artifact_ids?, prev_version_id?, status=DRAFT, redaction_ref?`。
创建者操作者取自操作上下文。

### 4.3 读取
```python
get_artifact(artifact_id, session=None) -> ArtifactInfo          # 不存在 → NOT_FOUND（ARTIFACT_NOT_FOUND 未启用）
open_content(artifact_id) -> (Iterator[bytes], DataClassification, str)  # ⛔ 流式；已清除 → ARTIFACT_PURGED (FD-ART-009/010)
read_content_bytes(artifact_id) -> (bytes, DataClassification, str)      # 小产物便捷读取；内部经 open_content，同样不能在 UoW 内且整块缓冲
query_artifacts(query: ArtifactQuery, session=None) -> Page[ArtifactInfo]  # FD-ART-014
lineage(artifact_id, session=None) -> LineageResult              # 来源链/派生链 (FD-ART-013)
version_history(artifact_id, session=None) -> list[ArtifactInfo] # 版本链按版本升序 (FD-ART-003)
```
`ArtifactQuery`：`project_id? workflow_id? type? status? creator_subsystem? created_after?
created_before? order_by="created_at" descending=True limit=100 offset=0`。
`Page[T]`：`items total limit offset`。

### 4.4 状态 / 清除 / 保留
```python
set_status(artifact_id, new_status: ArtifactStatus, *, session=None) -> ArtifactInfo  # 🧾 (FD-ART-007)
purge_content(artifact_id, session=None) -> ArtifactInfo        # ⛔ 删内容留元数据与哈希,置 purged,写审计 (FD-ART-010)
apply_retention(project_id, *, before: datetime, session=None) -> list[str]  # 只删内容,规则由调用方(S1)传入 (FD-ART-011)
cleanup_expired_staging(now, session=None) -> list[str]         # 清理 >24h 未登记的暂存 (FD-ART-005)
```

- `set_status` 只写入给定状态值，不校验 `DRAFT→PROPOSED→APPROVED` 迁移；
  `INVALID_STATUS_TRANSITION` 已登记但不可达。
- `purge_content` 当前对所有分级开放；`ARTIFACT_NOT_PURGEABLE` 已登记但不会抛出。
  它由 `@external_io` 拒绝在 UoW 内调用。
- `apply_retention` 不是 `@external_io` 装饰器，而是自行检测活动 UoW，若在 UoW 内则抛
  `ARTIFACT_ERROR`（不是 `TRANSACTION_MISUSE`）。其内部在短事务中执行对象删除，属已知的
  FD-BR-05 偏差。
- `cleanup_expired_staging` 不检测调用方 UoW，供 `run_housekeeping`（已检测）调度；直接在
  UoW 内调用会嵌套复用该事务，调用方应避免。

### 4.5 Diff / 打包 / 解包
```python
diff(a_id, b_id) -> DiffResult           # ⛔ 文本按行 / JSON 按结构路径 (FD-ART-012)
pack(artifact_ids, *, include_phi_content=True) -> PackResult    # ⛔ 单一归档+清单,每项分别带元数据/内容哈希 (FD-ART-015)
unpack(archive, target_project_id, *, session=None) -> UnpackResult  # ⛔ 先校验全部哈希,任一不一致整体失败;保留创建方/分级/状态/构建记录/版本,恢复来源与前版本关系,返回旧→新 ID 映射
```
`PackResult`：`archive bytes, archive_hash, artifact_ids, manifest`。
`UnpackResult`：`id_map dict[str,str], imported list[str]`。

0.1.2 的解包行为：
- 先校验清单内每个产物的 `metadataHash` 与（含内容时的）`contentHash`，任一不符整体失败。
- 通过校验后逐对象写入，并**立即回读重算 SHA-256** 与清单哈希比对；不符同样整体失败
  （FD-BR-02 适用于导入路径）。
- 失败时删除本次已写入的对象；删除失败的键放在错误的 `details.objectKeys` 中，绝不静默遗留。
- 已排除 PHI 内容或原本已清除的产物按 `content_purged=True`、`purged://<newId>` 导入。
- 已知限制：`manifest.json` 自身没有签名/认证，`archive_hash` 只在 `PackResult` 返回，
  解包不能校验它；`pack`/`unpack` 仍全量缓冲归档（SRS §11）。

## 5. 审计（FD-AUD）

```python
register_audit_event_types(event_types: dict[str,str] | Sequence[str], *, owner_subsystem=None, session=None) -> None
list_audit_event_types(session=None) -> list[tuple[str,str]]
is_registered_event_type(event_type, session=None) -> bool
BUILTIN_AUDIT_EVENT_TYPES: dict[str,str]

audit(event: AuditEventInput, *, session=None, clock=None) -> AuditRecord   # 🧾 调用方事务内写;失败回滚整事务 (FD-AUD-001/002)
get_audit(event_id, session=None) -> AuditRecord
query_audit(query: AuditQuery, session=None) -> Page[AuditRecord]           # FD-AUD-008
link_pending(*, limit=None) -> int                                          # 提交后哈希链 (FD-AUD-004)
verify_chain(*, start_seq=1, end_seq=None, session=None) -> VerifyResult    # 区间/全链校验 (FD-AUD-005, AC-SEC-01)
verify_audit_chain = verify_chain                                          # SRS §6 名称别名
run_full_chain_verify(*, now=None, session=None) -> VerifyResult            # 全链校验并写审计 (FD-AUD-006)
```
- 无删除/更新接口；数据库层触发器禁止 UPDATE/DELETE/TRUNCATE（首链字段首次填写除外，FD-AUD-003/009）。
- `AuditEventInput`：`event_type object_type? object_id? project_id? before? after? source_subsystem? operator_type? operator_name? correlation_id? occurred_at?`。缺省取自操作上下文。
- 写入前自动凭证脱敏并置 `redacted=True` + 告警日志（FD-AUD-007, FD-AC-07）。
- `VerifyResult`：`ok checked first_break_seq? reason? detail?`，`reason ∈ {content_changed, record_missing, link_missing}`。
- `verify_chain` **返回**校验结果，不抛 `CHAIN_INTEGRITY_ERROR`。`audit()` 在无活动 UoW 时
  经 `current_session()` 抛 `TRANSACTION_MISUSE`，不是 `AUDIT_OUTSIDE_TRANSACTION`；写入未
  登记事件类型抛 `VALIDATION_ERROR`，不是 `AUDIT_EVENT_TYPE_UNKNOWN`（两码仅登记，SRS §11）。
- `link_pending` 使用自己的短事务；`run_full_chain_verify` 在校验后写
  `core.audit.chain_verified` 审计。

## 6. 幂等（FD-IDEM）

```python
s0.idempotency.begin(scope, key, request_digest, *, expires_at=None, created_at=None, session=None) -> BeginResult
s0.idempotency.complete(scope, key, result_ref=None, *, session=None) -> IdemRecord
s0.idempotency.fail(scope, key, *, error_code=None, session=None) -> IdemRecord
s0.idempotency.get(scope, key, session=None) -> IdemRecord
cleanup_expired_idempotency(now, *, session=None) -> list[tuple[str,str]]
class IdempotencyStatus  # IN_PROGRESS | COMPLETED | FAILED
DHIS2_EXECUTION_SCOPE = "dx.execution"   # 该作用域记录永不过期 (FD-BR-08)
```
`BeginResult`：`created status record already_completed`；`.first_result` = 首次结果引用。
同键不同摘要 → `IDEMPOTENCY_CONFLICT`（FD-AC-06）。

## 7. Secret（FD-SEC）

```python
resolve_secret(ref) -> Secret      # ref 如 vault://path, env://VAR, gcp://…, memory://…
store_secret(path, value=None, *, scheme=None) -> str   # 写入并返回引用;省略 value 则一次性生成 (FD-SEC-004, M3)
install_development_backends(res=None) -> SecretResolver  # 注册 env+memory；生产环境抛 SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION
register_secret_backend(backend) -> SecretResolver        # 追加一个后端（只读后端不干扰写入推断）
class Secret: .reveal() -> str ; str()/repr()/for_json() 恒为掩码 (FD-SEC-002)
class SecretBackend ; class SecretResolver ; class MemorySecretBackend
```

- 解析失败 → `SECRET_UNAVAILABLE`，错误信息只含引用不含值（FD-SEC-003）。
- `store_secret`：目标后端不可写（env、只读 Vault/云传输）或省略 `scheme` 时无法唯一推断
  可写后端 → `SECRET_STORE_UNSUPPORTED`；`SECRET_UNAVAILABLE` 不再用于“不可写”。
- 生产环境（`APP_ENV=production`）构造 `EnvSecretBackend` 或调用
  `install_development_backends()` 即抛 `SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION`；
  生产须显式注册 Vault/云后端。`SECRET_BACKEND` 配置项只被读取，S0 不据此自动装配后端。
- `generate_secret_value(length=32)` 存在，但**不由 `factory_foundation.api` 重导出**；
  需要时从 `factory_foundation.secrets` 导入。
- `Secret` 是 `@dataclass(frozen=True)`；`str/repr/for_json` 为掩码，但
  `dataclasses.asdict(secret)` 会取到 `_value` 明文，禁止这样序列化（SRS §11）。

## 8. 对象存储（FD-OBJ）

子系统通常只经产物服务间接使用；如需直接句柄：
```python
get_object_store() -> ObjectStore
configure_object_store(store) -> None
configure_object_store_from_url(url) -> ObjectStore   # memory:// / file:// / local:// / s3:// / http(s)://
build_object_path(project_id, artifact_type, artifact_id) -> str   # "<project>/<type>/<id>" (FD-OBJ-002)
class ObjectStore  # put(key, stream)->PutResult; open_read(key)->ReadHandle; delete; exists（流式,不整块缓冲）
```

0.1.2 的 location/key 往返（FD-OBJ-001）：
- `memory://<key>`；`file:///abs/root/<key>`（本地后端以绝对路径为根，不再剥成相对根）；
  `s3://<bucket>/<prefix>/<key>`（前缀属于桶侧）。
- 产物行只存 `content_location`，读取/清除/保留期清理/暂存清理必须用
  `key_from_location(location, store)`（位于 `factory_foundation.objectstore.base`，非 `api`
  稳定导出）按产生该 location 的存储反推 key；`s3://` 回读时不得重复加前缀。
- 后端选择由 `OBJECT_STORE_URL` 决定：`memory://`、`file://`/`local://`、
  `s3://`（`?endpoint=` 支持 MinIO 等兼容服务）、`http(s)://`（首段为 bucket，其余为前缀）。
- `InMemoryObjectStore.open_read` 一次返回整块 bytes；本地与 S3 适配器按 1 MiB 流式读写。

## 9. 公共工具（FD-COM）

```python
# ID / 哈希 / 时钟
new_id(prefix) -> str                       # 前缀须已登记,否则 INTERNAL_ERROR (FD-COM-002)
new_correlation_id() -> str
register_id_prefixes({prefix: owner}) -> None  # 前缀改属 → REGISTRY_CONFLICT
canonical_json(value) -> bytes              # 键排序+紧凑+UTF-8,用于结构哈希 (FD-COM-003)
file_json(value) -> bytes                   # 键排序+2空格缩进+LF+末尾换行,用于写 JSON 文件产物
structure_hash(value) -> "sha256:<hex>"     # 对结构求哈希(用 canonical)
content_hash(data: bytes) -> "sha256:<hex>" # 对文件字节求哈希(产物内容哈希)
normalize_hash(v) -> "sha256:<hex>"         # 兼容裸 64-hex 或已带前缀
class Clock ; SystemClock ; FixedClock ; utcnow(clock=None) -> datetime   # FD-COM-004
# FixedClock(moment).advance(seconds) -> self：测试中显式推进时间

# 操作上下文
OperationContext ; bind_operation_context(**changes) ; get_operation_context() ; current_context_or_default()

# 日志 / 追踪 / 配置 / 脱敏
configure_logging(level=20, *, stream=None) ; get_logger(name)   # 结构化 JSON,自动附带上下文并脱敏 (FD-COM-006/007)
tracer(name="factory_foundation") ; trace_span(name, **attrs) ; configure_tracing(endpoint=None, *, service_name="factory")
load_settings(*, env_file=None, environ=None) -> Settings ; register_settings_schema(key, *, owner_subsystem, required=False)
redact_structure(value) -> RedactionResult  # 检测并掩码凭证 (FD-COM-007)
```

`Settings`：`app_env database_url object_store_url secret_backend otel_endpoint raw`，
`is_production`/`is_development` 属性；必需配置缺失当前抛 `INTERNAL_ERROR`
（`CONFIGURATION_ERROR` 已登记但未启用）。`SECRET_BACKEND` 只是读取并暴露，不自动装配后端。

其他导出类型：`OperatorType`（`USER`/`SYSTEM`）、`ErrorMeta`（错误码目录条目）、
`ContentStream`（流式内容 DTO；注意 `open_content` 实际返回 `(iterator, classification, media_type)` 三元组）。

## 10. 进度与取消（FD-PRG）

```python
class ProgressReporter   # report(*, step, completed_fraction=None, message=None, data=None)
class ProgressEvent      # step, completed_fraction, message, data
class NullProgressReporter
class CancellationToken  # cancelled / raise_if_cancelled()
class ManualCancellationToken  # cancel(reason=None) / check()
```

## 11. 错误模型（FD-COM-001）

```python
class FactoryError(Exception):
    error_code: str ; message: str ; retryable: bool ; stage: str|None ; artifact_id: str|None
    details: dict ; correlation_id: str|None ; to_dict() -> dict
# 子类：ArtifactError, ArtifactPurgedError, ConcurrencyError, TransactionMisuseError,
#      IdempotencyConflictError, SecretUnavailableError, CancelledError, NotFoundError,
#      ValidationError, SchemaError, InternalError, MigrationError, RegistryConflictError
error_catalog            # is_retryable/get/all/register
register_error_codes(codes: dict[str, tuple[str,bool]]) -> None   # {code:(owner,retryable)}
raise_for_code(code, message, **kw) -> NoReturn
BUILTIN_ERROR_CODES      # dict[str,bool]，FD-COM-001a 的 S0 内置码表（{code: retryable}，所有者一律 S0）
```
完整错误码目录见 `error-codes.md`。可重试判定遵循总体 SRS 19.2.2。
`error_catalog.register()` / `register_error_codes()` 以**不同所有者**重复登记同一个码时抛
`RegistryConflictError`（`REGISTRY_CONFLICT`）；同一所有者重复登记按合并处理，进程启动可重复执行。
`register_id_prefixes()` 对同一前缀改属同样抛 `REGISTRY_CONFLICT`（0.1.2）。
`SecretStoreUnsupportedError` / `SecretEnvBackendForbiddenInProductionError` 的异常类存在但
**未从 `api` 重导出**；调用方按 `error_code` 判定（两者均继承 `FactoryError`）。

## 12. 测试夹具（FD-TST-001）

`factory_foundation.api` 也重导出 `FoundationHarness`、`harness`、`resolve_test_database_url`；
`factory_foundation.testing` 额外提供 pytest 夹具与替身：

```python
from factory_foundation.testing import (
    foundation, FoundationHarness, harness, resolve_test_database_url,
    FixedClock, InMemoryObjectStore, MemorySecretBackend,
    RecordingProgressReporter, TriggerableCancellationToken,
)
```

- `FoundationHarness(url).setup()`：配置引擎/内存对象存储/内存 Secret，调用 `reset()`。
- `reset()`：`DROP SCHEMA core CASCADE`，建立 `core/cp/bp/ai/dx` 全部 schema（只重建 `core`），
  执行 `core` 迁移并 `initialize_foundation()`（0.1.2 起；此前只建 `core`）。
- `harness()` 与 `foundation` 夹具使用 `FACTORY_TEST_DATABASE_URL`；未设置时由
  `testcontainers[postgres]` 启动容器（`testing` 可选依赖），否则报明确错误。
- `FixedClock` 从 `factory_foundation.testing` 导出；默认时间为 2026-01-01T00:00:00Z。
