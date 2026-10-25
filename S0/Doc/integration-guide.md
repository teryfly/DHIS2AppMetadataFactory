# 集成指南（S1～S5 → S0）

> 对照 `factory-foundation 0.1.2`。

对应《00 总体架构与集成约定》第 6 章。S0 是全部后端子系统的唯一共同依赖，
自身不依赖任何业务子系统（D-17，依赖无环）。

## 1. 依赖与导入

- S0 以 Python 包 `factory-foundation` 发布，S1 按固定版本组装后端运行时。
- 唯一公共入口：`from factory_foundation.api import ...`（00 doc 6.1）。
- 测试替身与夹具：`from factory_foundation.testing import ...`。
- 禁止 `import` 任何其他 `factory_foundation.*` 子模块（如 `.artifacts.service`），
  这些是实现细节，不保证语义化版本稳定。

```toml
# 子系统 pyproject.toml
dependencies = [
  "factory-foundation==0.1.*",
]
[project.optional-dependencies]
test = ["factory-foundation[dev]"]
```

可选后端：
- `factory-foundation[s3]` → 安装 boto3（S3 兼容对象存储）。
- `factory-foundation[otel]` → 安装 OpenTelemetry（真实追踪；缺省时 S0 用 no-op tracer）。

## 2. 进程启动顺序（00 doc 6.10）

```text
1. 日志与追踪初始化        configure_logging(); configure_tracing(endpoint=...)
2. 迁移（仅 factory-api）  run_migrations()  # 默认 core；S1 传入全部子系统配置，按 core→dx→bp→ai→cp
3. S0 初始化               initialize_foundation()
4. 其他子系统 initialize()  S4、S2、S3 的 initialize()（各自登记产物类型/审计事件/错误码/ID 前缀）
5. S1 自身登记
```

每个子系统在自己的包内导出：
- `initialize()`：登记本子系统的**产物类型、审计事件类型、错误码、ID 前缀**（FD-ASM-01）。
- `MIGRATION_CONFIG`：本 schema 的迁移配置（`MigrationConfig`）。

S0 提供登记接口（在子系统 `initialize()` 中调用）：

```python
import factory_foundation.api as s0

def initialize():  # 例如 S2
    s0.register_artifact_types({"BLUEPRINT": ["S2"], "DIFF_REPORT": ["S2"]})
    s0.register_audit_event_types({"bp.patch.submitted": "S2", "bp.version.promoted": "S2"})
    s0.register_error_codes({"BP_COMPILE_ERROR": ("S2", False), "UID_POOL_EMPTY": ("S2", False)})
    s0.register_id_prefixes({"bpv": "S2", "patch": "S2"})
```

- **产物类型**登记会**合并所有者**：`DIFF_REPORT` 由 S2 与 S4 分别登记后，所有者为 `{S2,S4}`。
- **审计事件类型**登记后即可写入；写入未登记类型会报错（FD-AUD-010）。
- **错误码**登记用于 S0 判定可重试性与 REST 映射（FD-COM-001）。
- **ID 前缀**登记后 `new_id("bpv")` 才可用（FD-COM-002）。

## 3. 配置项（FD-COM-008）

从环境变量与可选 `.env` 读取，环境变量优先：

| 键 | 必需 | 说明 |
| --- | --- | --- |
| `APP_ENV` | 否（默认 development） | `development`/`production` 等；决定前向迁移与 env Secret 后端可用性 |
| `DATABASE_URL` | 是 | 形如 `postgresql+psycopg://user:pass@host:5432/db` |
| `OBJECT_STORE_URL` | 是 | `memory://` / `file://<root>` / `local://<root>` / `s3://<bucket>[/<prefix>][?endpoint=...]` / `http(s)://<endpoint>/<bucket>[/<prefix>]` |
| `SECRET_BACKEND` | 否（默认 env） | **仅被读取并暴露**；S0 不据此自动装配后端，需显式 `install_development_backends()` / `register_secret_backend()` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 否 | 设置后 `configure_tracing()` 接入 OTLP |

生产环境（`APP_ENV=production`）的 Secret 规则：
- 构造 `EnvSecretBackend` 或调用 `install_development_backends()` 立即抛
  `SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION`（0.1.2 起强制）。
- 必须显式注册 Vault 或云 Secret Manager 后端；只读传输只能解析，`store_secret` 会抛
  `SECRET_STORE_UNSUPPORTED`。
- `SECRET_BACKEND` 只是配置值，不能代替注册。

子系统可声明自己的配置项（启动时校验必填）：

```python
s0.register_settings_schema("LLM_API_BASE", owner_subsystem="S3", required=True)
settings = s0.load_settings()          # 校验必填并返回 Settings
```

## 4. 事务与外部 I/O（最关键约定）

### 4.1 工作单元 `unit_of_work()`
- 调用链上首次开启创建事务，**嵌套调用自动加入同一事务**（同一 Session）。
- 只有**最外层**提交；任一层抛出未处理异常 → **整体回滚**（FD-DB-003）。
- `after_commit(callback)` 登记提交后动作（如写事件流通知），仅在最外层提交成功后执行；回滚则不执行。

```python
with s0.unit_of_work() as outer:
    do_db_work(outer)
    with s0.unit_of_work() as inner:   # 同一 Session
        assert inner is outer
    s0.after_commit(lambda: notify(...))
```

### 4.2 外部 I/O 函数 `@external_io`
- 调用 LLM、DHIS2、上传/下载大文件到对象存储等耗时操作必须用 `@external_io` 标记。
- **在已开启的工作单元内调用被标记函数会立即抛 `TRANSACTION_MISUSE`**（FD-DB-004、FD-AC-03）。
- 这类函数内部自行管理短事务，且必须在调用方事务之外发起。

产物创建因此是**两步**（FD-ART-005）：
```text
stage_content(...)   # @external_io：写对象存储+回读校验哈希，事务外，返回暂存句柄
register_artifact(...)  # 事务内：登记元数据 + 状态变更 + 审计，同一 UoW 提交
```

`@external_io` 的实际清单：`stage_content`、`open_content`、`purge_content`、`diff`、`pack`、
`unpack`。`run_housekeeping` 未加装饰器但自行检测 UoW 并抛 `TRANSACTION_MISUSE`。
`apply_retention` 也自行检测，但抛的是 `ARTIFACT_ERROR`；`cleanup_expired_staging` 不检测，
只应由 `run_housekeeping` 调度。`pack`/`unpack` 当前全量缓冲归档，不是流式实现（SRS §11）。

0.1.2 的 `unpack` 在事务外逐对象写入，**回读校验哈希后才**在单一事务内登记；失败会删除
本次已写对象，删除不掉的键在 `details.objectKeys`。对象存储没有事务，所以"整体失败"
同时依赖对象清理。

### 4.3 乐观并发（FD-DB-005）
聚合加 `VersionedAggregate` 混入（提供 `version` 列）。更新时：
```python
from factory_foundation.api import bump_version
new_v = bump_version(aggregate, expected_version)   # 不匹配 → CONCURRENCY_ERROR
```
校验只比较**已加载实体**的 `version` 并自增，不生成 `WHERE version = ?` 的原子更新；
调用方须在同一 UoW 内完成“读取—校验—写入”，并在需要时自行加行锁/版本条件。
S0 自身模型未使用该混入（SRS §11）。

## 5. 迁移（FD-DB-007）

- 每个子系统维护本 schema 的 Alembic 迁移与**独立版本表**（如 `core.core_alembic_version`）。
- S0 导出 `CORE_MIGRATION_CONFIG`；运行器 `run_migrations([...])` 按 `core→dx→bp→ai→cp` 顺序执行。
- 生产只允许**前向**迁移。降级必须显式给目标：
  `run_migrations(..., downgrade_to="<rev>", forward_only=False)`；默认
  `forward_only=True` 时指定目标抛 `MIGRATION_ERROR`，生产即使显式请求也拒绝。
  `forward_only=False` 但未给 `downgrade_to` 只是普通前向升级，不会降级（0.1.2）。
- S0 初始迁移直接由 ORM 模型 `create_all` 生成 DDL，并安装审计 append-only 触发器，
  因此**改模型必须同步升级 core 迁移**（见 `database-schema.md`）。

```python
from factory_foundation.api import run_migrations, CORE_MIGRATION_CONFIG
# factory-api 启动时（S1 组装全部配置）：
run_migrations([CORE_MIGRATION_CONFIG, S4.MIGRATION_CONFIG, S2.MIGRATION_CONFIG, ...])
```

## 6. 测试替身与夹具（FD-TST-001）

子系统 CI 应使用真实 PostgreSQL 容器运行自身测试（00 doc 6.10），并使用 S0 的测试夹具：

```python
# tests/conftest.py
from factory_foundation.testing import foundation  # pytest fixture
pytest_plugins = ["factory_foundation.testing.fixtures"]
```

`foundation` 夹具每个用例提供：重建的 `core` schema（DROP + 重新迁移）、预先建好的
`cp/bp/ai/dx` 空 schema、内存对象存储、固定时钟、内存 Secret 后端、已登记的 S0 内置目录。
0.1.2 起 `FoundationHarness.reset()` 建立 `core/cp/bp/ai/dx` 全部 schema（只 drop/重建
`core`），因此子系统可直接对自己的 schema 跑迁移。用法：

```python
def test_my_flow(foundation):
    from factory_foundation.api import unit_of_work, ...
    ...
```

其他替身（无需数据库即可测试调用方）：

```python
from factory_foundation.testing import (
    InMemoryObjectStore, MemorySecretBackend, FixedClock,
    RecordingProgressReporter, TriggerableCancellationToken,
)
```
`FixedClock` 也从 `factory_foundation.testing` 导出，`advance(seconds)` 可推进时间。

设 `FACTORY_TEST_DATABASE_URL` 指向测试库；未设时夹具使用 `testcontainers[postgres]`
（安装 `factory-foundation[testing]` 或 `[dev]`），两者都没有时明确报错。

## 7. 进度与取消（FD-PRG）

长任务函数（S3 Agent、S4 发现/执行）签名须接受：
```python
def run(reporter: ProgressReporter, token: CancellationToken, ...):
    for step in steps:
        token.raise_if_cancelled()          # 安全点：取消 → CANCELLED
        reporter.report(step=step, completed_fraction=..., message=..., data={...})
```
S1 实现 `ProgressReporter`（写事件流 + Temporal 心跳）并在取消时翻转 `CancellationToken`。
测试用 `RecordingProgressReporter` 与 `TriggerableCancellationToken()`。

## 8. 幂等（FD-IDEM）

会产生副作用的公共函数须接受幂等键：
```python
res = s0.idempotency.begin(scope, key, digest, expires_at=...)
if res.already_completed:
    return res.first_result          # 直接返回首次结果（同键同摘要）
# ... 执行副作用 ...
s0.idempotency.complete(scope, key, {"executionId": ex})   # 或 .fail(scope, key, error_code=...)
```
- 同键不同摘要 → `IDEMPOTENCY_CONFLICT`（FD-AC-06）。
- `scope == "dx.execution"` 的记录**永不过期删除**（FD-BR-08），防止重复 COMMIT。
- `digest` 用 `structure_hash(request_payload)` 计算。

## 9. 操作上下文与可观测性（FD-COM-005/006/007）

在入口（如 S1 REST/SSE 处理）绑定上下文，日志/审计/产物自动携带：
```python
with s0.bind_operation_context(operator_type="USER", operator_name="alice",
                               project_id="p1", workflow_id="wf1", stage_id="stg1"):
    ...
```
- 结构化 JSON 日志自动附加 `correlationId/operator/projectId/workflowId/stageId` 等。
- 日志与审计写入前**自动脱敏** Authorization/Cookie/token/password/client_secret（FD-COM-007）。
- `tracer()` / `trace_span()` 提供 OTel 追踪；缺省 no-op。

## 10. 已知限制（0.1.2，集成前必读）

1. **错误码仍以粗码为主**：31 个 S0 内置码只有 11 个由 S0 实际抛出；非所有者/超大小/分级下降/
   哈希不符/包完整性多为 `ARTIFACT_ERROR`，不存在为 `NOT_FOUND`，未登记审计类型为
   `VALIDATION_ERROR`，缺配置为 `INTERNAL_ERROR`。按具体码分支前先读 `error-codes.md`。
2. **状态与清除校验**：`set_status` 不校验迁移；`purge_content` 不限制 PHI_PII。
3. **打包**：清单自身未认证，`archive_hash` 需调用方保存比对；`pack`/`unpack` 全量缓冲。
4. **版本唯一性**：`artifact` 无 `(prev_version_id, version)` 唯一约束；乐观并发靠调用方自觉。
5. **Secret 序列化**：`dataclasses.asdict(secret)` 会泄漏 `_value`，禁止使用；`for_json()` 未接线。
6. **事务边界**：S0 的保留期/暂存清理与待删除重试在短事务内做对象存储 I/O，与 FD-BR-05 有偏差。
7. **`ENVIRONMENT_LOCKED` 归属**：S1 与 S4 仍以不同所有者登记，后初始化者冲突，待裁定。

以上条目在 `../CHANGELOG.md` 的“仍待裁决/仍开放”与 SRS §11 中保留，不应视为已实现。
