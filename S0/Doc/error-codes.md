# 错误码目录（FD-COM-001）

全部公共函数以 `FactoryError` 层次抛错，字段：`errorCode / message / retryable / stage /
artifactId / details / correlationId`（对应总体 SRS 19.2.1；S1 负责映射为 REST 错误响应）。
可重试判定遵循总体 SRS 19.2.2（LLM 超时、网络、HTTP 429/502/503/504 可自动退避重试；
Schema/UID/策略拒绝/审批/校验类禁止自动重试）。

> 本文档对照 `factory-foundation 0.1.2`。"可重试"是目录属性，不代表 S0 会重试；
> S0 自身不按可重试性分支，实际重试由 S1 的 Temporal 活动策略决定。

## 0.1.2 现状：登记 ≠ 抛出

`BUILTIN_ERROR_CODES` 共 **31** 个 S0 内置码；0.1.2 的 S0 代码实际抛出其中 **11** 个：

`TRANSACTION_MISUSE`、`CONCURRENCY_ERROR`、`ARTIFACT_ERROR`、`ARTIFACT_PURGED`、
`IDEMPOTENCY_CONFLICT`、`SECRET_UNAVAILABLE`、`SECRET_STORE_UNSUPPORTED`、
`SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION`、`REGISTRY_CONFLICT`、`CANCELLED`、`MIGRATION_ERROR`。

其余 **20** 个仅完成登记，语义对应的抛出点仍使用粗码：

- 非所有者 / 类型未登记 / 超大小 / 分级下降 / 暂存句柄无效 → `ARTIFACT_ERROR`；
- 期望哈希不符 / 回读不符 / 打包完整性失败 → `ARTIFACT_ERROR`；
- 产物或对象不存在 → `NOT_FOUND`；
- 未登记审计事件类型 → `VALIDATION_ERROR`；
- 必需配置缺失 → `INTERNAL_ERROR`；
- 状态迁移不校验，`INVALID_STATUS_TRANSITION` 不可达；`verify_chain` 返回结果而不抛
  `CHAIN_INTEGRITY_ERROR`。

切换到具体码会改变 S1/S2/S4 观测到的 `errorCode` 与 REST 映射，属跨子系统协调项，
明确保留为未决（SRS §11）。

## S0 内置码（FD-COM-001a，所有者 S0）

下表 31 个码由 S0 拥有，进程启动即可用，`initialize_foundation()` 把它们连同可重试属性
写入 `core.error_code` 表；子系统**不得**再以别的所有者登记这些码（否则抛
`REGISTRY_CONFLICT`）。除 `CONCURRENCY_ERROR` 与 `ARTIFACT_READBACK_MISMATCH`
外均不可重试。清单即 `factory_foundation.api.BUILTIN_ERROR_CODES`。

| 错误码 | 可重试 | 0.1.2 实际抛出 | 典型触发 / 当前现状 |
| --- | --- | --- | --- |
| `TRANSACTION_MISUSE` | 否 | 是 | 在 UoW 内调用外部 I/O；无 UoW 时取会话（FD-DB-004、FD-AC-03） |
| `CONCURRENCY_ERROR` | **是** | 是 | 乐观版本冲突、环境提交锁占用，退避后可重试（FD-DB-005、NFR-CON-002） |
| `ARTIFACT_ERROR` | 否 | 是 | 产物操作失败的总类（总体 SRS 19.2.1）；多数具体产物错误的当前落点 |
| `ARTIFACT_NOT_FOUND` | 否 | 否 | 按 ID 取产物不存在。**仅登记**：`get_artifact` 抛 `NOT_FOUND` |
| `ARTIFACT_PURGED` | 否 | 是 | 读取内容已清除的产物（FD-ART-010） |
| `ARTIFACT_HASH_MISMATCH` | 否 | 否 | 调用方期望哈希与内容不符。**仅登记**：`stage_content` 抛 `ARTIFACT_ERROR` |
| `ARTIFACT_READBACK_MISMATCH` | **是** | 否 | 写入后回读哈希不符。**仅登记**：`stage_content`/`unpack` 抛 `ARTIFACT_ERROR`；可重试属性保留 |
| `ARTIFACT_TOO_LARGE` | 否 | 否 | 超过类型大小上限。**仅登记**：抛 `ARTIFACT_ERROR` |
| `ARTIFACT_CLASSIFICATION_DOWNGRADE` | 否 | 否 | 分级低于来源最高级且无脱敏引用。**仅登记**：抛 `ARTIFACT_ERROR` |
| `ARTIFACT_OWNERSHIP_VIOLATION` | 否 | 否 | 非类型所有者创建。**仅登记**：抛 `ARTIFACT_ERROR` |
| `ARTIFACT_TYPE_UNKNOWN` | 否 | 否 | 类型未登记。**仅登记**：抛 `ARTIFACT_ERROR` |
| `ARTIFACT_TYPE_MISMATCH` | 否 | 否（子系统可抛） | 产物存在但类型不符合调用要求；任何子系统可直接抛出，`details` 由抛出方定义 |
| `ARTIFACT_NO_CONTENT` | 否 | 否 | 要读内容而产物无内容。**仅登记**，当前无抛出点 |
| `ARTIFACT_NOT_PURGEABLE` | 否 | 否 | 非 PHI_PII 产物清除。**仅登记**：`purge_content` 当前不按分级限制 |
| `ARTIFACT_VERSION_CONFLICT` | 否 | 否 | 前一版本/版本号推进冲突。**仅登记**；`artifact` 无 `(prev_version_id, version)` 唯一约束 |
| `ARTIFACT_STAGING_NOT_FOUND` | 否 | 否 | 暂存句柄不存在或已清理。**仅登记**：抛 `ARTIFACT_ERROR` |
| `ARTIFACT_STAGING_MISMATCH` | 否 | 否 | 暂存句柄与项目/类型/哈希不符。**仅登记**，当前无交叉核对与抛出点 |
| `INVALID_STATUS_TRANSITION` | 否 | 否 | 状态机不允许的迁移。**仅登记**：`set_status` 接受任意状态值 |
| `IDEMPOTENCY_CONFLICT` | 否 | 是 | 同幂等键不同请求摘要（FD-IDEM-002、FD-AC-06） |
| `SECRET_UNAVAILABLE` | 否 | 是 | Secret 引用无法解析，信息只含引用不含值（FD-SEC-003） |
| `SECRET_STORE_UNSUPPORTED` | 否 | 是 | 后端不可写（env、只读 vault/cloud 传输）或无法唯一推断写后端（FD-SEC-004；0.1.2 起启用） |
| `SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION` | 否 | 是 | 生产环境构造 `EnvSecretBackend` 或 `install_development_backends()`（FD-SEC-001，0.1.2 起强制） |
| `CANCELLED` | 否 | 是 | 长任务在安全点检测到取消（FD-PRG-002） |
| `CHAIN_INTEGRITY_ERROR` | 否 | 否 | 审计哈希链断裂。**仅登记**：`verify_chain` 返回 `VerifyResult`，不抛此码 |
| `PACK_INTEGRITY_ERROR` | 否 | 否 | 包内清单或单产物哈希失败。**仅登记**：`pack`/`unpack` 抛 `ARTIFACT_ERROR` |
| `OBJECT_NOT_FOUND` | 否 | 否 | 对象存储路径不存在。**仅登记**：三个适配器均抛 `NOT_FOUND` |
| `CONFIGURATION_ERROR` | 否 | 否 | 必需配置缺失或非法。**仅登记**：抛 `INTERNAL_ERROR` |
| `MIGRATION_ERROR` | 否 | 是 | 迁移失败、未知 schema、前向模式下请求降级、生产降级（FD-DB-007） |
| `AUDIT_OUTSIDE_TRANSACTION` | 否 | 否 | 审计写入不在业务事务内。**仅登记**：`audit()` 无 UoW 时抛 `TRANSACTION_MISUSE` |
| `AUDIT_EVENT_TYPE_UNKNOWN` | 否 | 否 | 写入未登记事件类型。**仅登记**：抛 `VALIDATION_ERROR` |
| `REGISTRY_CONFLICT` | 否 | 是 | 同一错误码或 ID 前缀以不同所有者重复登记（0.1.2 起错误码与 ID 前缀两处登记共用本码；`details` 含 `existingOwner`/`requestedOwner`） |

> `CONCURRENCY_ERROR` 的可重试性自 0.1.1 起按 FD-COM-001a 定为**是**（此前目录记为否）：
> 版本冲突与环境锁占用都是退避后重试探锁，不改变任何业务状态；是否重试由调用方（S1 的
> Temporal 活动策略）决定，S0 只提供判定依据。`ARTIFACT_READBACK_MISMATCH` 同理为可重试，
> 但当前回读失败实际抛不可重试的 `ARTIFACT_ERROR`。

## 其余预置码

S0 也预置下列码以便跨子系统识别与重试判定，但语义所有者在对方。`error_catalog` 中
`SCHEMA_ERROR` 归 S2，但 S0 的跨 schema 外键检查器会实际抛出它（FD-DB-006、FD-AC-09）。

| 错误码 | 所有者 | 可重试 | 典型触发 |
| --- | --- | --- | --- |
| `NOT_FOUND` | S0 | 否 | 产物/审计/幂等记录不存在（总体 SRS 19.2.1 的通用未找到） |
| `VALIDATION_ERROR` | S0 | 否 | 通用校验失败；未登记审计事件类型当前也落在此码 |
| `INTERNAL_ERROR` | S0 | 否 | 内部错误 / 未登记的错误码 / 必需配置缺失 |
| `SCHEMA_ERROR` | S2 | 否 | 结构/Schema 问题；含跨 schema 外键检查失败（S0 抛出，FD-DB-006、FD-AC-09） |
| `REGISTRY_ERROR` | S2 | 否 | 总体 SRS 19.2.1 |
| `GATE_NOT_APPROVED` | S1 | 否 | Gate 未满足审批 |
| `APPROVAL_INVALIDATED` | S1 | 否 | 审批已失效 |
| `LLM_ERROR` | S3 | 否 | 模型调用错误 |
| `LLM_TIMEOUT` | S3 | **是** | 模型超时（自动重试） |
| `POLICY_DENIED` | S3 | 否 | 数据分级策略拒绝（禁止重试） |
| `DHIS2_AUTH_ERROR` | S4 | 否 | DHIS2 认证失败 |
| `DHIS2_PERMISSION_ERROR` | S4 | 否 | 权限不足 |
| `DHIS2_NOT_FOUND` | S4 | 否 | 对象不存在 |
| `DHIS2_IMPORT_ERROR` | S4 | 否 | 导入校验错误（禁止自动重试） |
| `DHIS2_TIMEOUT` | S4 | **是** | DHIS2 请求超时（自动重试） |
| `DRIFT_DETECTED` | S4 | 否 | 漂移检测（人工处理） |

> `owner` 表示语义归属方。COMMIT 网络失败后的重投需先查执行状态与目标环境实际状态
> （总体 SRS 19.2.2），不在自动重试范畴。

## 0.1.2 契约可见变化（调用方需知）

- `store_secret()` 打到只读后端：`errorCode` 由 `SECRET_UNAVAILABLE` 变为
  `SECRET_STORE_UNSUPPORTED`。
- 生产环境装配 env 后端：由"静默可用"变为启动即失败
  （`SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION`）。
- ID 前缀被其他所有者重复登记：由 `INTERNAL_ERROR` 变为 `REGISTRY_CONFLICT`。
- 跨 schema 外键检查器现在能抓到各 schema 独立 `MetaData` 中的跨 schema 声明；子系统
  若真有此类外键，测试会从通过变为失败（FD-BR-06 的预期效果）。

## 登记新错误码（子系统 `initialize()`）

```python
import factory_foundation.api as s0
s0.register_error_codes({
    "BP_COMPILE_ERROR": ("S2", False),
    "UID_POOL_EMPTY":   ("S2", False),
})
```
- 只登记本子系统自有的码；上表 S0 内置码已由 S0 唯一登记，重复登记会抛
  `REGISTRY_CONFLICT`（`details` 给出既有所有者与请求所有者）。同一所有者重复登记按合并处理，
  因此 `initialize()` 可安全重入。
- 未登记的码经 `raise_for_code` 会转为 `INTERNAL_ERROR`（fail-safe）。
- 未登记码默认可重试性 = `False`（绝不盲目重试）。
- `initialize_foundation()` 会把当前进程错误码目录持久化到 `core.error_code` 并覆写陈旧行。
  S0 启动时目录为 47 个（31 个内置 + 13 个上游归属 + 3 个共享）；调用前子系统已登记的码
  也会一并写入。

## 抛出与转换

```python
raise s0.ConcurrencyError("版本冲突", stage="COMMIT", artifact_id="art-...")
# 或按码抛出：
s0.raise_for_code("DHIS2_TIMEOUT", "导入超时", details={"job": "..."} )
try: ...
except s0.FactoryError as e:
    payload = e.to_dict()   # 交给 S1 映射为 REST 错误体
```
