# 调试与排障指南

面向 S1～S5 开发者定位与 S0 交互时的问题。对照 `factory-foundation 0.1.2`。

## 0. 先定位错误类别

所有异常都是 `factory_foundation.api.FactoryError`，读 `error_code`：
```python
from factory_foundation.api import FactoryError
try:
    ...
except FactoryError as e:
    log.warning("foundation error", extra={"extra_fields": e.to_dict()})
    # e.error_code / e.retryable / e.correlation_id / e.details
```
错误码语义与可重试性见 `error-codes.md`。

## 1. `TRANSACTION_MISUSE`

**现象**：调用 `stage_content / open_content / diff / pack / unpack / purge_content /
run_housekeeping` 或任何被 `@external_io` 标记的函数时抛 `TRANSACTION_MISUSE`。

**原因**：这些是外部 I/O，禁止在 `unit_of_work()` 内运行（FD-DB-004、FD-AC-03）。

**修复**：把内容写入/读取放到事务之外：
```python
handle = stage_content(...)           # ① 事务外：写对象存储+校验哈希
with unit_of_work() as s:             # ② 事务内：登记+状态+审计
    register_artifact(handle, ..., session=s)
    audit(..., session=s)
```
`current_session()` 抛此错则表示当前不在事务中——用 `unit_of_work()` 包裹，或用
`get_artifact()/query_artifacts()` 这类只读函数（它们可自开只读会话）。

## 2. 审计相关

- **写未登记事件类型** → `VALIDATION_ERROR`（`AUDIT_EVENT_TYPE_UNKNOWN` 已登记但未启用）。
  先在 `initialize()` 调 `register_audit_event_types({"cp.gate.approved": "S1"})`（FD-AUD-010）。
- **在事务外写审计** → `TRANSACTION_MISUSE`（`audit()` 经 `current_session()` 取会话），
  不是 `AUDIT_OUTSIDE_TRANSACTION`。
- **尝试 UPDATE/DELETE/TRUNCATE `core.audit_event`** → 数据库触发器拒绝（FD-AUD-003）。
  这是设计；测试清库用 `DROP SCHEMA core CASCADE`（夹具已如此）。
- **`verify_chain().ok == False`**：看 `first_break_seq` 与 `reason`：
  - `content_changed`：记录内容被改（含被绕过触发器的篡改）→ AC-SEC-01。
  - `record_missing`：序号有缺口（记录丢失/未链接且区间内缺号）。
  - `link_missing`：链接字段为空（尚未链接或异常）。
  未链接记录属正常中间态：调用 `link_pending()` 或等待 S1 调度（FD-AUD-004，延迟 ≤10s）。
- **审计里出现明文凭证**：不应发生——写入前会掩码并置 `redacted=True` 并告警（FD-AUD-007）。
  若命中，检查脱敏键集合（`common/redaction.py`）。

## 3. 产物相关

- **`ARTIFACT_ERROR` 非所有者**：`creator_subsystem` 不在类型 `owner_subsystems` 内。
  确认调用方子系统身份，或该类型由正确所有者在 `initialize()` 登记（FD-AC-05）。
- **`ARTIFACT_ERROR` 哈希不一致**：`stage_content(expected_hash=...)` 与回读内容不符（FD-AC-01）。
  调用方应先用 `content_hash(file_bytes)`/`structure_hash(obj)` 计算期望值，勿手算。
- **`ARTIFACT_ERROR` 分级下降**：派生产物分级低于来源最高级且无 `redaction_ref`（FD-AC-04）。
- **`ARTIFACT_PURGED`**：读取 `content_purged=True` 的产物内容（FD-ART-010）。元数据仍可读，用 `get_artifact`。
- **`ARTIFACT_ERROR` 暂存/解包**：暂存句柄不存在、超大小、包哈希或回读不符当前都抛
  `ARTIFACT_ERROR`。`ARTIFACT_STAGING_*`、`PACK_INTEGRITY_ERROR`、`ARTIFACT_READBACK_MISMATCH`
  尚未启用；排障看 `details`（`handle`/`size`/`max`/`objectKeys`/`cause`）。
- **`NOT_FOUND` 而非 `ARTIFACT_NOT_FOUND`**：`get_artifact` 与对象存储适配器当前抛通用
  `NOT_FOUND`；按具体码分支的调用方需同时兼容。
- **大文件内存占用**：`open_content` 返回的是分块迭代器（1MiB/块），**消费迭代器**而非 `read_content_bytes`
  （后者会整块读入，仅供小产物/测试）。

### 3.1 解包完整性

- 篡改归档中任一产物的元数据或内容 → `ARTIFACT_ERROR`（"hash mismatch ... import aborted"），
  不登记任何产物。
- 存储写入后回读不符 → `ARTIFACT_ERROR`（消息含 "readback"），本次已写对象会被删除；
  若删除失败，`details.objectKeys` 列出残留键，`details.cause` 给出原始异常类型。
- 归档清单自身未签名，`PackResult.archive_hash` 需导入方另行保存并比对；否则一致地改写
  清单与内容可绕过逐条哈希（SRS §11）。

## 4. 对象存储路径

产物内容位置由 S0 生成：`<project_id>/<type>/<artifact_id>`（FD-OBJ-002）。子系统不得直接操作对象存储，
一律经产物服务。排查内容缺失时：
```sql
SELECT id, type, content_location, content_hash, size, content_purged
FROM core.artifact WHERE id = :id;
```
`content_location` 前缀反映后端：`memory://`、`file://`、`s3://bucket/...`、清除后为 `purged://`。
0.1.2 的往返规则：
- 本地后端：`file:///abs/root/<key>`，根是配置的绝对路径，**不能**当相对根处理；
- S3 前缀后端：`s3://<bucket>/<prefix>/<key>`，读回时先剥掉桶与前缀，不能再加一次；
- 产物行只存 location，读取/清除/清理统一用 `key_from_location(location, store)` 还原 key。
  若读到"object not found"但 location 看起来正确，先检查后端根/前缀是否与写入时一致。
删除失败的对象内容会在 `run_housekeeping()` 的"待删除对象重试"中清理（`deletions_retried`）。

### 4.1 跨 schema 外键（FD-DB-006）

`check_no_cross_schema_fk()` 失败抛 `SCHEMA_ERROR`，`details.violations` 为
`"<源 schema>.<表>.<列> -> <目标 schema>.<表>"`。0.1.2 起按外键声明的点分目标名解析，
因此各 schema 独立 `MetaData` 中的 `ForeignKey("core.artifact.id")` 也能被抓到。
子系统升级后若测试突然在此失败，说明模型里确有跨 schema 外键，应改为同 schema 引用 +
应用层校验（D-08），而不是回退检查器。

## 5. Secret

- 解析失败 → `SECRET_UNAVAILABLE`，`details.ref` 只含引用（绝不含值，FD-SEC-003）。
  检查后端是否已在启动注册：开发 `install_development_backends()`（env/memory），
  生产需注册 vault/cloud 后端。
- **生产环境启动即失败**：`APP_ENV=production` 时构造 `EnvSecretBackend` 或调用
  `install_development_backends()` 抛 `SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION`。
  这是 0.1.2 起的强制限制；检查部署是否仍带开发期装配。
- **写入失败**：`store_secret()` 打到只读后端（env、只读 vault/cloud 传输）或省略 `scheme`
  时存在多个/零个可写后端 → `SECRET_STORE_UNSUPPORTED`，不再报 `SECRET_UNAVAILABLE`。
  可用 `details.refScheme` / `details.candidates` 定位。
- `Secret` 对象 `str()`/`repr()`/`for_json()` 恒为掩码，取明文只能 `.reveal()`，且**禁止**写入日志/异常/审计/SSE（FD-SEC-002、6.9）。
  注意 `dataclasses.asdict(secret)` 会取到 `_value` 明文，禁止这样序列化。
- 一次性生成并存入（M3，如演示用户凭证）：`store_secret(path)` 省略 value 即生成并返回引用；
  `generate_secret_value()` 需从 `factory_foundation.secrets` 导入（`api` 未重导出）。
- `SECRET_BACKEND` 只是配置读取，S0 不据此自动装配后端；生产启动须显式注册。

## 6. 幂等

- `IDEMPOTENCY_CONFLICT`：同 `(scope,key)` 换了请求摘要。确认摘要稳定（用 `structure_hash`，勿用字典插入序）。
- "进行中"重复请求：`begin().status == IN_PROGRESS` 表示前一次仍在跑，应等待/复用，勿重复副作用。
- `dx.execution` 记录不会因过期被清理（FD-BR-08）——这是防止重复 COMMIT 的有意行为。

## 7. 配置与迁移

- 启动报"database/object store not configured"：设 `DATABASE_URL` / `OBJECT_STORE_URL`，或在测试设
  `FACTORY_TEST_DATABASE_URL`。
- 迁移报 `permission denied for schema core`：`core` 属主非连接角色，见 `database-schema.md §1`。
- 迁移顺序固定 `core→dx→bp→ai→cp`；某 schema 失败会中断，日志有 `MigrationError`。
- 降级必须显式给目标：`run_migrations(..., downgrade_to="<rev>", forward_only=False)`；
  默认 `forward_only=True` 或 `APP_ENV=production` 时请求降级都抛 `MIGRATION_ERROR`。
  `forward_only=False` 但未给 `downgrade_to` 只是普通前向升级（0.1.2）。
- 改了 ORM 模型但未升级迁移：`core` 表结构不会变（初始迁移来自模型快照，后续需追加增量迁移）。
- 启动时 `REGISTRY_CONFLICT`：某错误码或 ID 前缀被两个所有者登记。`details` 给出
  `existingOwner`/`requestedOwner`（前缀的 `code` 字段是前缀本身），移除重复登记或统一归属。

## 8. 可观测性

- 日志为单行 JSON；`correlationId/operator/projectId/workflowId/stageId` 来自 `OperationContext`。
  跨线程/异步请确认上下文已 `bind_operation_context(...)` 绑定，否则字段缺失。
- 关联一次调用链：用入口生成的 `correlationId` 过滤日志；产物创建/审计自动带上同一 id。
- 追踪：装 `factory-foundation[otel]` 且设 `OTEL_EXPORTER_OTLP_ENDPOINT`，`configure_tracing(endpoint)`
  后 `trace_span("op", ...)` 产 span；未安装时是 no-op（不影响功能）。

## 9. 最小可复现（本地）

```bash
export FACTORY_TEST_DATABASE_URL="postgresql+psycopg://factory:factory@localhost:5432/factory_test"
.venv/bin/python -c "
from factory_foundation.testing import FoundationHarness
h = FoundationHarness(url=__import__('os').environ['FACTORY_TEST_DATABASE_URL']); h.setup()
from factory_foundation.api import stage_content, register_artifact, ArtifactCreate, DataClassification, unit_of_work
sh = stage_content(b'{}', project_id='p', artifact_type='MANIFEST', media_type='application/json')
with unit_of_work() as s:
    info = register_artifact(sh, ArtifactCreate(project_id='p', type='MANIFEST', media_type='application/json',
                                               data_classification=DataClassification.INTERNAL, creator_subsystem='S2'), session=s)
print('artifact', info.id, info.status)
h.teardown()
"
```
