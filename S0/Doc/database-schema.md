# 数据库连接与 `core` schema

> 对照 `factory-foundation 0.1.2`。

S0 独占 schema `core`（00 doc 4.1，D-08）。全部后端子系统共用一个 PostgreSQL
实例，各自独占一个 schema，**禁止跨 schema 的外键、联表、视图**。

## 1. 连接（FD-DB-001）

- 连接串来自 `DATABASE_URL`，形如：
  ```
  postgresql+psycopg://<user>:<password>@<host>:<port>/<database>
  ```
  SQLAlchemy 2.x 同步引擎 + `psycopg`（v3）驱动。
- 会话工厂由 `factory_foundation.db.engine` 进程级持有；子系统通过 `unit_of_work()`
  取会话，不自行建连。
- 本项目开发/测试环境已就绪（本机 PostgreSQL 16）：

  ```bash
  # 角色/库（由运维用 postgres 超级用户创建一次）
  sudo -u postgres psql -c "CREATE ROLE factory LOGIN PASSWORD 'factory';"
  sudo -u postgres createdb -O factory factory_dev
  sudo -u postgres createdb -O factory factory_test
  ```

  ```text
  DATABASE_URL (dev)          = postgresql+psycopg://factory:factory@localhost:5432/factory_dev
  FACTORY_TEST_DATABASE_URL   = postgresql+psycopg://factory:factory@localhost:5432/factory_test
  ```

- 迁移运行要求连接角色对自己 schema 有建表/建触发器权限（`core` 属主为 `factory`）。
  若 `core` 由其他角色创建会触发 `permission denied for schema core`，请 `ALTER SCHEMA core OWNER TO factory` 或重建。

## 2. `core` 表结构（SRS §5）

### `artifact` — 产物元数据（FD-ART-001）
| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | varchar(64) PK | `art-` 前缀 ULID |
| `project_id` | text | 索引 |
| `workflow_id` | text NULL | 索引，可空 |
| `type` | text FK→`artifact_type.type` | 索引 |
| `version` | integer | 链内自动递增（FD-ART-003） |
| `status` | varchar(16) | `DRAFT/PROPOSED/APPROVED/SUPERSEDED`（FR-DATA-002） |
| `content_location` | text | 对象存储定位符 |
| `content_hash` | varchar(71) | `sha256:<hex>` |
| `size` | bigint | 字节数 |
| `media_type` | text | |
| `data_classification` | varchar(16) | 见分级表 |
| `build_record` | jsonb NULL | |
| `prev_version_id` | text FK→`artifact.id` NULL | 版本链前驱 |
| `creator_subsystem` | text | 子系统代码 |
| `creator_operator` | text | 操作者 |
| `created_at` | timestamptz | |
| `content_purged` | boolean | PHI 清除/保留期清除标记（FD-ART-010/011） |
| `redaction_ref` | text NULL | S3 脱敏记录引用（FD-ART-008） |

索引：`project_id`、`workflow_id`、`type`、`status`、`prev_version_id`。
> 当前**没有** `(prev_version_id, version)` 唯一约束；并发创建同版本不会被数据库拒绝
> （FD-ART-003 已知缺口，SRS §11）。

### `artifact_source` — 来源关系（FD-ART-013）
`(artifact_id, source_artifact_id)` 联合主键，均 FK→`artifact.id`。

### `artifact_staging` — 暂存句柄（FD-ART-005）
`handle` PK、`content_location`、`content_hash`、`size`、`media_type`、`created_at`。
> 暂存超过 24h 未登记由 `run_housekeeping`/`cleanup_expired_staging` 删除内容并清行。

### `artifact_type` — 产物类型目录（FD-ART-002）
`type` PK、`owner_subsystems`(json 列表)、`max_size`(int NULL，空=不限)。

### `audit_event` — 审计（FD-AUD-002）
| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | varchar(64) PK | `aud-` |
| `seq` | bigint NULL UNIQUE | 全局序号，链接时分配（FD-AUD-004） |
| `occurred_at` | timestamptz | |
| `operator_type` | varchar(16) | `USER`/`SYSTEM` |
| `operator_name` | text | |
| `source_subsystem` | text | |
| `event_type` | text | `子系统.对象.动作` |
| `object_type`/`object_id` | text NULL | |
| `project_id` | text NULL | |
| `before_summary`/`after_summary` | jsonb NULL | 变更前后摘要（可被脱敏） |
| `correlation_id` | text NULL | |
| `redacted` | boolean | 写入时检测到凭证并已掩码（FD-AUD-007） |
| `prev_hash` | varchar(71) NULL | 链接后填充 |
| `hash` | varchar(71) NULL | 链接后填充 |

索引：project、(object_type,object_id)、event_type、correlation_id、(occurred_at,id)（链接顺序）。

### `audit_chain_state` — 全局链头（FD-AUD-004）
单行（`key='global'`）：`last_linked_seq bigint`、`last_hash varchar(71) NULL`。

### `audit_event_type` — 事件类型登记（FD-AUD-010）
`event_type` PK、`owner_subsystem`。

### `idempotency_record` — 幂等（FD-IDEM-001）
`(scope, key)` 联合主键、`request_digest varchar(71)`、`status`、`result_ref jsonb`、
`error_code`、`created_at`、`expires_at NULL`。

### `error_code` — 错误码目录（持久化，FD-COM-001 / §5）
`code` PK、`owner_subsystem`、`retryable bool`。`initialize_foundation()` 写入当前进程
错误码目录（S0 预置 47 个：31 个内置 + 13 个上游归属 + 3 个共享），并覆写陈旧行的所有者
与可重试属性；调用前已由子系统登记的码也会一并落库。

### `id_prefix` — ID 前缀归属（§5 伴生）
`prefix` PK、`owner_subsystem`。S0 内置 `art/aud/stg/idem/corr`；子系统通过
`register_id_prefixes()` 登记，前缀改属抛 `REGISTRY_CONFLICT`（0.1.2）。

### `core_alembic_version` — S0 独立版本表（FD-DB-007）
`version_num`。各 schema 各用独立版本表名。

## 3. 数据分级（顺序由低到高）

`PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED < PHI_PII`（总体 SRS 20.1）。
派生产物分级不得低于其来源最高级，除非提供 `redaction_ref`（FD-ART-008）。

## 4. Append-only 触发器（FD-AUD-003）

`core.migration 0001_core_initial` 安装：
- `audit_event_guard`（`BEFORE UPDATE OR DELETE ... FOR EACH ROW`）：
  - DELETE 一律拒绝：`audit events are append-only: DELETE is forbidden`。
  - UPDATE：若 `OLD.seq IS NOT NULL`（已链接）一律拒绝；未链接时仅允许"首次链接"
    （只填 `seq/prev_hash/hash`），其余任何列变化都拒绝。
- `audit_event_no_truncate`（`BEFORE TRUNCATE ... FOR EACH STATEMENT`）：拒绝 TRUNCATE。

```sql
-- 查看触发器
SELECT tgname, tgtype FROM pg_trigger WHERE tgrelid='core.audit_event'::regclass AND NOT tgisinternal;
```

> 测试夹具清理数据用 `DROP SCHEMA core CASCADE` 重建，绕开触发器（DELETE/TRUNCATE 被禁）；
> 夹具同时建立 `core/cp/bp/ai/dx` 全部 schema，但只 drop/重建 `core`（0.1.2，FD-TST-001）。

## 5. 迁移（FD-DB-007）

- 环境：`src/factory_foundation/migrations/env.py`，版本表 `core.core_alembic_version`，
  `env` 在建版本表前 `CREATE SCHEMA IF NOT EXISTS core`。
- 初始迁移 `0001_core_initial` 由 ORM 元数据 `create_all` 生成全部 `core` 表 + 安装审计触发器。
  **改模型必须同步改迁移**（本项目采用"初始快照即模型"，后续增量迁移请在
  `migrations/versions/` 追加 `revision`）。
- 顺序：`core → dx → bp → ai → cp`；生产仅前向。
- 降级只能显式发起：`run_migrations(..., downgrade_to="<rev>", forward_only=False)`；仍保留
  `forward_only=True`（默认）时指定目标会抛 `MIGRATION_ERROR`，生产环境（`APP_ENV=production`）
  即使显式请求也拒绝降级。`forward_only=False` 而未给 `downgrade_to` 只是普通前向升级，
  不会降级（0.1.2 语义，见 `api-reference.md`）。

```bash
# CLI 方式（等价于 run_migrations([CORE_MIGRATION_CONFIG])）
.venv/bin/alembic -c <(echo) ... # 推荐用库 API:
```
```python
from factory_foundation.api import run_migrations, current_revision, CORE_MIGRATION_CONFIG
run_migrations([CORE_MIGRATION_CONFIG])
print(current_revision(CORE_MIGRATION_CONFIG))   # '0001_core_initial'
```

## 6. 常用核查 SQL（调试）

```sql
-- 产物概览
SELECT type, status, count(*), sum(size) FROM core.artifact GROUP BY 1,2 ORDER BY 1;

-- 未链接审计（应随链接任务趋近 0；FD-AUD-004 链接延迟 ≤10s）
SELECT count(*) FROM core.audit_event WHERE seq IS NULL;

-- 链头与最近序号
SELECT last_linked_seq, last_hash FROM core.audit_chain_state WHERE key='global';
SELECT seq, occurred_at, event_type, left(hash,18) FROM core.audit_event ORDER BY seq DESC LIMIT 10;

-- 检测断链（相邻记录 prev_hash 应等于前一条 hash）
SELECT a.seq AS broken_seq
FROM core.audit_event a JOIN core.audit_event b ON b.seq = a.seq-1
WHERE a.prev_hash <> b.hash AND a.seq > 1
ORDER BY a.seq LIMIT 1;

-- 幂等：dx.execution 永不清理（FD-BR-08）
SELECT scope, status, count(*) FROM core.idempotency_record GROUP BY 1,2;

-- 过期暂存候选
SELECT handle, created_at FROM core.artifact_staging WHERE created_at < now() - interval '24 hours';
```

程序化核查：
```python
from factory_foundation.api import verify_chain, link_pending
print(link_pending())            # 手动补链（S1 亦有调度）
print(verify_chain().ok)         # 全链校验
```
