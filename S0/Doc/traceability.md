# 需求可追溯性矩阵（SRS → 实现 → 测试）

逐项核对 `S0_公共基础库_SRS.md` V1.0.2 与 `factory-foundation 0.1.2`。
状态：✅ 已实现并有测试；🟡 已实现（跨子系统调度/环境相关）。0.1.2 新增或变化的
测试在行内以「0.1.2」标注。

## FD-DB 数据库与工作单元

| 需求 | 实现 | 测试 |
| --- | --- | --- |
| FD-DB-001 连接与会话工厂（`DATABASE_URL`） | `db/engine.py` `configure_engine/get_database` | `test_db_uow.py::db` 夹具；`test_more_branches.py::test_current_session_returns_active` |
| FD-DB-002 按 schema 绑定 ORM 基类 | `models/base.py` `CoreBase/CpBase/BpBase/AiBase/DxBase` | `test_bootstrap.py::test_migration_creates_core_tables` |
| FD-DB-003 工作单元：嵌套共享、仅外层提交、异常整体回滚、提交后回调 | `db/uow.py` `unit_of_work/after_commit/current_session` | `test_db_uow.py::test_nested_joins_same_transaction / test_inner_exception_rolls_back_whole / test_after_commit_*` |
| FD-DB-004 `external_io` 标记，事务内调用抛 `TRANSACTION_MISUSE` | `db/external_io.py` | `test_db_uow.py::test_external_io_inside_uow_raises`（FD-AC-03） |
| FD-DB-005 乐观并发（版本校验，冲突 `CONCURRENCY_ERROR`） | `db/optimistic.py` `VersionedAggregate/check_version/bump_version`（只比较已加载实体，不做 `WHERE version=` 原子更新；无 S0 模型使用） | `test_db_uow.py::test_optimistic_version_check_and_bump` |
| FD-DB-006 跨 schema 外键检查（按点分目标名跨 `MetaData` 解析） | `db/fk_check.py` `_resolve_target` | `test_db_uow.py::test_cross_schema_fk_detected_and_raises`（FD-AC-09）/ `test_cross_schema_fk_across_separate_metadata_is_caught`（0.1.2）/ `test_same_schema_fk_ok` |
| FD-DB-007 迁移运行器（各 schema 独立版本表、`core→dx→bp→ai→cp`、生产前向、显式目标才降级） | `db/migrations.py` + `migrations/env.py` + `migrations/versions/0001_core_initial.py` | `test_bootstrap.py::test_current_revision_matches / test_schema_order_canonical / test_a_forward_only_run_never_downgrades / test_production_refuses_a_downgrade_even_with_a_target / test_clearing_forward_only_without_a_target_is_a_plain_forward_run / test_a_development_downgrade_to_base_actually_runs` |

## FD-ART 产物存储

| 需求 | 实现 | 测试 |
| --- | --- | --- |
| FD-ART-001 元数据字段 | `models/artifact.py::ArtifactRow`、`artifacts/dto.py::ArtifactInfo` | `test_artifacts.py::test_register_and_get` |
| FD-ART-002 类型目录+所有者合并+创建方校验 | `artifacts/catalog.py` | `test_artifacts.py::test_register_artifact_type_merges_owners / test_non_owner_cannot_create`(FD-AC-05) / `test_unknown_type_rejected` |
| FD-ART-003 内容不可变，改=新产物+版本自增（无 `(prev_version_id, version)` 唯一约束） | `artifacts/service.py::register_artifact` | `test_artifacts.py::test_versioning_supersedes_previous / test_more_branches.py::test_version_history_three_versions` |
| FD-ART-004 写入后回读校验 SHA-256，期望哈希不符即拒（当前抛 `ARTIFACT_ERROR`，非 `ARTIFACT_*_MISMATCH`） | `service.py::stage_content` | `test_artifacts.py::test_stage_hash_mismatch_aborts`（FD-AC-01） |
| FD-ART-005 两步（暂存+登记）、>24h 清理 | `service.py::stage_content/register_artifact/cleanup_expired_staging` | `test_artifacts.py` 全流程；`test_housekeeping.py::test_expired_staging_is_cleaned` |
| FD-ART-006 流式读写、按类型可选上限（上限超限抛 `ARTIFACT_ERROR`） | `objectstore/*` 流式 + `ArtifactTypeRow.max_size` | `test_objectstore_secrets.py::test_local_store_streaming`；上限校验见 `register_artifact`（size cap） |
| FD-ART-007 状态值变更+前驱自动 SUPERSEDED（不校验迁移，`INVALID_STATUS_TRANSITION` 未启用） | `service.py::set_status/register_artifact` | `test_artifacts.py::test_set_status / test_versioning_supersedes_previous` |
| FD-ART-008 分级不低于来源（除有脱敏引用） | `service.py::register_artifact` | `test_artifacts.py::test_classification_downgrade_rejected`(FD-AC-04) / `test_classification_downgrade_allowed_with_redaction` |
| FD-ART-009 读内容同时返回分级 | `service.py::open_content` | `test_artifacts.py::test_open_content_returns_classification` |
| FD-ART-010 清除：删内容留元数据/哈希、标记、审计；读已清除→`ARTIFACT_PURGED`（不限制 PHI_PII，`ARTIFACT_NOT_PURGEABLE` 未启用） | `service.py::purge_content` | `test_artifacts.py::test_purge_then_read_raises` / `test_objectstore_secrets.py::test_artifact_read_and_purge_work_on_the_local_backend`（0.1.2） |
| FD-ART-011 永久保留+按项目保留期清理（规则由调用方传入；短事务内做对象 I/O，已知偏差） | `service.py::apply_retention` | `test_artifacts.py::test_apply_retention / test_apply_retention_audits_everything_it_clears / test_more_branches.py::test_set_status_and_apply_retention_filters` |
| FD-ART-012 通用 Diff（文本按行、JSON 按路径） | `artifacts/diff.py` | `test_artifacts.py::test_diff_json_and_line / test_diff_refuses_two_different_artifact_types / test_coverage_extra.py::test_diff_json_nested_paths` |
| FD-ART-013 来源链上溯/派生下溯 | `service.py::lineage/_walk` | `test_artifacts.py::test_lineage_sources_and_derived` |
| FD-ART-014 条件查询+分页+排序 | `service.py::query_artifacts` | `test_artifacts.py::test_query_pagination_and_filter / test_more_branches.py::test_query_each_filter` |
| FD-ART-015 打包/解包：清单+分别哈希、全验后原子导入、写入后回读校验、失败清理、排除 PHI 内容、保留字段并返回 ID 映射；清单自身未认证 | `artifacts/packing.py`（回读见 `unpack`，补偿删除见 `_discard`） | `test_artifacts.py::test_pack_unpack_roundtrip_and_hash_verify`(FD-AC-08) / `test_unpack_preserves_version_and_creator` / `test_an_aborted_unpack_leaves_no_object_behind` / `test_a_cleanup_that_fails_names_the_objects_left` / `test_unpack_fails_when_the_store_cannot_read_back_what_it_wrote`（0.1.2）/ `test_api_and_gaps.py::test_pack_excludes_phi_content` |

## FD-AUD 审计服务

| 需求 | 实现 | 测试 |
| --- | --- | --- |
| FD-AUD-001 调用方事务内写，失败回滚整事务 | `audit/service.py::audit`（用 `current_session`） | `test_audit.py::test_audit_failure_rolls_back_business`（FD-AC-02） |
| FD-AUD-002 记录字段 | `models/audit.py`、`audit/dto.py` | `test_audit.py::test_audit_write_and_read` |
| FD-AUD-003 DB 层禁止 UPDATE/DELETE（首链字段除外）+禁 TRUNCATE | `migrations/versions/0001_core_initial.py` 触发器 | `test_audit.py::test_append_only_update_and_delete_blocked / test_append_only_truncate_blocked` |
| FD-AUD-004 全局哈希链，(occurred_at,id) 分配序号，提交后链接 | `audit/chain.py`、`service.py::link_pending` | `test_audit.py::test_link_and_verify_full_chain` |
| FD-AUD-005 全链/区间校验，报告首个断裂与原因 | `chain.verify_chain`、`service.verify_chain` | `test_audit.py::test_verify_detects_tampering`(AC-SEC-01) / `test_verify_detects_missing_record` |
| FD-AUD-006 定时全链校验并写审计 | `service.py::run_full_chain_verify` | `test_audit.py::test_run_full_chain_verify_writes_audit` |
| FD-AUD-007 写入前凭证检测→掩码+标记+告警 | `service.py::_redact`、`common/redaction.py` | `test_audit.py::test_credential_redaction`（FD-AC-07） |
| FD-AUD-008 多维查询+分页 | `service.py::query_audit` | `test_audit.py::test_query_audit_filters` |
| FD-AUD-009 无删除函数、不随项目删除 | 无 delete API + 触发器 | `test_audit.py::test_append_only_update_and_delete_blocked` |
| FD-AUD-010 事件类型登记，写未登记类型报错 | `audit/registry.py` | `test_audit.py::test_unregistered_event_type_rejected` |

## FD-IDEM / FD-OBJ / FD-SEC / FD-COM / FD-PRG / FD-TST

| 需求 | 实现 | 测试 |
| --- | --- | --- |
| FD-IDEM-001 获取/完成，记录摘要/状态/结果/过期 | `idempotency/service.py` | `test_idempotency.py::test_begin_complete_and_replay` |
| FD-IDEM-002 同键同摘要返回首次/进行中；不同摘要冲突 | `IdempotencyStore.begin` | `test_idempotency.py::test_same_key_different_digest_conflict`(FD-AC-06) / `test_in_progress_is_reported` |
| FD-IDEM-003 `dx.execution` 禁止过期删除 | `NON_EXPIRING_SCOPES`、`cleanup_expired` | `test_idempotency.py::test_dhis2_execution_never_expires` |
| FD-OBJ-001 S3+本地（+内存）同接口，配置选择；location→key 往返（file 绝对根、s3 前缀） | `objectstore/{s3,local,memory,__init__}`、`base.key_from_location` | `test_objectstore_secrets.py`；`test_file_url_keeps_an_absolute_root`、`test_prefixed_s3_store_recovers_the_bare_key`、`test_memory_location_still_round_trips`、`test_artifact_read_and_purge_work_on_the_local_backend`（0.1.2）；S3 用注入 fake `test_optional_adapters.py::test_s3_store_roundtrip` |
| FD-OBJ-002 路径 项目/类型/ID，子系统不直接操作 | `objectstore/base.build_object_path`、`key_from_location`、D-12 provider | `test_objectstore_secrets.py::test_path_generation` |
| FD-SEC-001 vault/云/env 后端，`vault://` 引用；生产禁用 env | `secrets/{base,vault,cloud,env,memory}`、`install_development_backends` | `test_optional_adapters.py`（vault/cloud）、`test_objectstore_secrets.py`（env/memory）、`test_optional_adapters.py::test_env_backend_refused_in_production`（0.1.2） |
| FD-SEC-002 Secret 包装，字符串恒掩码 | `secrets/base.Secret` | `test_objectstore_secrets.py::test_secret_masked_always` |
| FD-SEC-003 解析失败 `SECRET_UNAVAILABLE`，只含引用 | `SecretResolver.resolve` | `test_objectstore_secrets.py::test_resolver_missing_backend_raises_without_value` |
| FD-SEC-004 Secret 写入（M3）；不可写/无法推断 → `SECRET_STORE_UNSUPPORTED` | `store_secret`、`SecretResolver.store`、后端 `store` | `test_objectstore_secrets.py::test_memory_backend_write_read` / `test_optional_adapters.py::test_vault_read_and_write / test_vault_read_only_store_raises / test_cloud_read_only_store_raises / test_env_backend_store_reports_unsupported`（0.1.2） |
| FD-COM-001 错误模型+目录+登记 | `common/errors.py` | `test_common_tools.py::test_error_catalog_retryability / test_coverage_extra.py::test_error_catalog_coerce_and_register_many` |
| FD-COM-001a S0 内置码 31 项：所有者 S0、两个可重试、初始化落库、不同所有者重复登记冲突（错误码与 ID 前缀同一规则） | `common/errors.py`（`BUILTIN_ERROR_CODES`、`ErrorCatalog.register`）、`common/ids.py`、`bootstrap.py` | `test_common_tools.py::test_fd_com_001a_code_is_registered_and_owned_by_s0`（逐码 31 项）、`::test_builtin_code_registered_under_another_owner_is_a_registry_conflict`、`::test_re_registering_under_the_same_owner_merges`、`test_bootstrap.py::test_initialize_foundation_persists_fd_com_001a_codes`、`::test_initialize_foundation_repairs_a_stale_row`、`test_coverage_extra.py::test_ids_registry_operations`（前缀冲突，0.1.2） |
| FD-COM-002 前缀化 ULID+前缀登记（改属抛 `REGISTRY_CONFLICT`） | `common/ids.py` | `test_common_tools.py::test_new_id_prefix_registered / test_coverage_extra.py::test_ids_registry_operations`（含改属冲突，0.1.2） |
| FD-COM-003 规范化/文件 JSON+`sha256:` 哈希 | `common/hashing.py` | `test_common_tools.py::test_canonical_json_* / test_hash_formats / test_structure_hash_uses_canonical_form` |
| FD-COM-004 系统/固定时钟 | `common/clock.py` | `test_common_tools.py::test_system_and_fixed_clock` |
| FD-COM-005 操作上下文（contextvar） | `common/context.py` | `test_common_tools.py::test_operation_context_binding / test_context_extend_existing` |
| FD-COM-006 结构化 JSON 日志+OTel | `common/logging.py`、`common/tracing.py` | `test_common_tools.py::test_logger_redacts_and_includes_context / test_optional_adapters.py::test_tracer_noop_and_span` |
| FD-COM-007 日志脱敏 | `common/redaction.py`、logging formatter | `test_common_tools.py::test_redaction_masks_sensitive_fields / test_redaction_inline_credential` |
| FD-COM-008 配置加载+子系统配置项 | `common/config.py` | `test_common_tools.py::test_config_load_from_environ / test_config_required_declared_key_missing` |
| FD-PRG-001 `ProgressReporter` 协议 | `progress/protocols.py` | `test_progress.py / test_coverage_extra.py::test_null_progress_reporter_consumes` |
| FD-PRG-002 `CancellationToken`+安全点 `CANCELLED` | `progress/protocols.py` | `test_progress.py::test_cancellation_*` |
| FD-TST-001 测试容器夹具+全部 schema+内存对象存储+固定时钟+内存 Secret+记录型 Reporter+可触发 Token；`FixedClock` 从 testing 导出 | `testing/{harness,fakes,fixtures}` | 全套件基于 `foundation` 夹具运行；`test_bootstrap.py::test_harness_stands_up_every_subsystem_schema / test_testing_package_exports_the_fixed_clock`（0.1.2） |

## 业务规则（FD-BR）

| 规则 | 实现 | 佐证测试 |
| --- | --- | --- |
| FD-BR-01 内容不可变；分级不低于来源（除脱敏） | `service.register_artifact` | FD-AC-04 |
| FD-BR-02 回读校验哈希后才能登记（普通创建 + 解包导入） | `stage_content`、`packing.unpack` | FD-AC-01、`test_unpack_fails_when_the_store_cannot_read_back_what_it_wrote`（0.1.2） |
| FD-BR-03 仅类型所有者可创建 | `catalog.assert_type_owner` | FD-AC-05 |
| FD-BR-04 审计与业务同事务；只追加；哈希链；不随项目删除 | `uow`+触发器+`chain` | FD-AC-02、AC-SEC-01 |
| FD-BR-05 外部 I/O 禁止在 UoW 中 | `external_io` | FD-AC-03 |
| FD-BR-06 禁止跨 schema 外键（含独立 `MetaData` 的点分目标名） | `fk_check` | FD-AC-09、`test_cross_schema_fk_across_separate_metadata_is_caught`（0.1.2） |
| FD-BR-07 Secret 值不入日志/异常/审计 | `Secret`/`redaction` | `test_secret_masked_always` |
| FD-BR-08 DHIS2 执行类幂等记录不得过期删除 | `NON_EXPIRING_SCOPES` | FD-IDEM-003 |

## 非功能（FD-NFR）

| 编号 | 状态 | 说明 |
| --- | --- | --- |
| FD-NFR-01 元数据查询 P95<100ms | 🟡 | 已加索引（project/type/status/created_at）；性能压测属集成阶段 |
| FD-NFR-02 审计写入耗时 P95<10ms | 🟡 | 审计仅 INSERT；性能基准待集成 |
| FD-NFR-03 链接≤10s；全链 100 万<10min | 🟡 | `link_pending` 锁定待链接记录；`verify_chain` 把区间记录装入字典后逐序号校验（非流式），百万级基准与 10 秒预算均待验证 |
| FD-NFR-04 100MB 流式内存增量<32MB | ✅(机制) | 本地/S3 适配器与 `open_content` 按 1MiB 分块；内存对象存储与 `pack`/`unpack` 会整块持有 |
| FD-NFR-05 行覆盖率≥90% | ✅ | 实测 91% 行覆盖（192 项测试，`pytest --cov=factory_foundation`，见 README 门禁） |
| FD-NFR-06 不依赖 S1~S4；语义化版本 | ✅ | 仅依赖第三方库；`pyproject` 版本管理 |
| FD-NFR-07 core 纳入 RPO/RTO 备份 | 🟡 | 属运维（S1 Helm/备份），S0 无独立进程数据可外置 |

## 跨系统假设（FD-ASM）

| 假设 | S0 提供 |
| --- | --- |
| FD-ASM-01 各子系统启动登记类型/事件/错误码/前缀 | `register_artifact_types/register_audit_event_types/register_error_codes/register_id_prefixes` |
| FD-ASM-02 S1 调度定时任务，S0 提供被调度函数 | `link_pending/run_full_chain_verify/run_housekeeping/apply_retention` |
| FD-ASM-03 S3 保证脱敏记录引用有效 | S0 仅记录 `redaction_ref`，不校验内容 |

## 0.1.1 缺口修复记录

审查发现并补齐的与 SRS §6 公共 API 清单的差距：
1. **`tracer()` / `configure_tracing()` 未在 `api` 导出** → 已补导出（FD-COM-006）。
2. **`cleanup_expired_idempotency()` 顶层函数缺失**（仅有方法）→ 已补顶层别名（SRS §6 明确列出）。
3. **`verify_chain` 精确名称**：SRS 用 `verify_chain(range)`，此前仅 `verify_audit_chain` → 已补 `verify_chain` 别名导出。

## 0.1.2 反向校准记录

对照 0.1.2 代码逐项核对后，本矩阵新增/修正：

| 项 | 变化 | 佐证 |
| --- | --- | --- |
| FD-OBJ-001 location→key | 新增 `key_from_location`；修正 `file://` 绝对根与带前缀 `s3://` 回读 | `test_file_url_keeps_an_absolute_root`、`test_prefixed_s3_store_recovers_the_bare_key`、`test_memory_location_still_round_trips`、`test_artifact_read_and_purge_work_on_the_local_backend` |
| FD-BR-02 / FD-ART-015 解包回读 | `unpack` 写入后回读校验，失败清理已写对象 | `test_unpack_fails_when_the_store_cannot_read_back_what_it_wrote`、`test_an_aborted_unpack_leaves_no_object_behind` |
| FD-DB-006 / FD-BR-06 跨 schema FK | 按点分目标名跨 `MetaData` 解析 | `test_cross_schema_fk_across_separate_metadata_is_caught` |
| FD-SEC-001 生产守卫 | env 后端与开发装配在生产环境抛 `SECRET_ENV_BACKEND_FORBIDDEN_IN_PRODUCTION` | `test_env_backend_refused_in_production` |
| FD-SEC-004 写不支持 | 只读后端/无法推断写后端抛 `SECRET_STORE_UNSUPPORTED` | `test_vault_read_only_store_raises`、`test_cloud_read_only_store_raises`、`test_env_backend_store_reports_unsupported` |
| FD-COM-001a / FD-COM-002 冲突 | ID 前缀改属统一为 `REGISTRY_CONFLICT` | `test_ids_registry_operations` |
| FD-TST-001 夹具 | 建立全部 schema、`FixedClock` 导出、testcontainers 可选依赖 | `test_harness_stands_up_every_subsystem_schema`、`test_testing_package_exports_the_fixed_clock` |

仍未闭合（不计为已实现，详见 SRS §11）：粗码到具体码的切换、`ENVIRONMENT_LOCKED` 归属、
清单自身认证、pack/unpack 流式化、`(prev_version_id, version)` 唯一约束、乐观并发原子性、
S0 清理路径的事务边界、`Secret.asdict` 泄漏、10 秒链接预算测试。

## 验收标准（SRS §9）

全部 AC 均有对应测试（见上表 FD-AC-01..09、AC-SEC-01）。运行：
```bash
FACTORY_TEST_DATABASE_URL=... .venv/bin/pytest -q
```
