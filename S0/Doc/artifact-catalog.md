# 产物类型目录（FD-ART-002）

> 对照 `factory-foundation 0.1.2`。

产物内容由 S0 统一托管（D-12）。常规创建经 `stage_content` + `register_artifact`；项目导入由
`unpack` 直接写入对象存储并在回读校验后登记。
每个类型登记其**允许的所有者子系统**；创建方声明的子系统必须属于该类型所有者，
否则 `ARTIFACT_ERROR`（FD-BR-03、FD-AC-05；`ARTIFACT_OWNERSHIP_VIOLATION` 尚未启用）。
一个类型可有多个所有者，多方登记时**合并**。

## 初始目录

| 类型 | 所有者 | 类型 | 所有者 |
| --- | --- | --- | --- |
| BUSINESS_INPUT | S1 | DHIS2_IMPORT_REPORT | S4 |
| NRR | S3 | SNAPSHOT | S4 |
| AGENT_OUTPUT | S3 | EXECUTION_PLAN | S4 |
| BLUEPRINT | S2 | EXECUTION_REPORT | S4 |
| METADATA_PACKAGE | S2 | POST_VERIFICATION_REPORT | S4 |
| DATASTORE_CHANGESET | S2 | CAPABILITY_PROFILE | S4 |
| OPENAPI | S2 | DISCOVERY_RESULT | S4 |
| UI_SPECIFICATION | S2 | DIFF_REPORT | **S2、S4** |
| FIXTURE_PACKAGE | S2 | CHANGE_IMPACT_REPORT | S1 |
| VALIDATION_REPORT | S2 | PROJECT_EXPORT | S1 |
| FRONTEND_PACKAGE | S2 | HANDOFF_CHECK_REPORT | S2 |
| MANIFEST | S2 | | |

> 来源：S0 SRS FD-ART-002 表 + 总体 SRS FR-DATA-001。新增 `AGENT_OUTPUT / EXECUTION_PLAN /
> DIFF_REPORT / HANDOFF_CHECK_REPORT / CHANGE_IMPACT_REPORT` 的理由：它们是 Gate 绑定对象
> 或审计依据，必须以不可变产物存在。

## 产物状态（FR-DATA-002）

`DRAFT → PROPOSED → APPROVED`，被新版本取代时自动 `SUPERSEDED`。
- 内容一律**不可变**；"修改"= 以 `prev_version_id` 创建新产物，版本自增，前驱自动置 `SUPERSEDED`（FD-ART-003/007）。
- `set_status(artifact_id, new_status, session=...)` 在调用方事务内变更状态。
  0.1.2 只写入给定状态值，**不校验状态机迁移**；`INVALID_STATUS_TRANSITION` 已登记但不会抛出。
- `artifact` 表当前没有 `(prev_version_id, version)` 唯一约束（FD-ART-003 的已知缺口，SRS §11）。

## 数据分级（FD-ART-008 / 总体 SRS 20.1）

`PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED < PHI_PII`（由低到高）。
创建产物必须声明分级；声明了来源产物时，分级不得低于来源中的最高级，**除非**提供
`redaction_ref`（由 S3 生成的脱敏记录引用；S0 只记录引用、不校验内容）——违反抛 `ARTIFACT_ERROR`（FD-AC-04；
`ARTIFACT_CLASSIFICATION_DOWNGRADE` 尚未启用）。
读取内容时**必须同时返回分级**（FD-ART-009，供 S3 策略判定）。

## 内容大小与清除（FD-ART-006/010/011）

- 内容流式读写，不在内存整块缓冲；默认无大小上限，可按类型配 `max_size`（空=不限）。
  超上限当前抛 `ARTIFACT_ERROR`（`ARTIFACT_TOO_LARGE` 未启用）。
- `PHI_PII` 产物支持 `purge_content(id)`：删除对象存储内容，保留元数据与哈希，置
  `content_purged=True` 并写审计；此后 `open_content` 抛 `ARTIFACT_PURGED`。
  0.1.2 **不按分级限制**清除范围，`ARTIFACT_NOT_PURGEABLE` 不会抛出。
- `apply_retention(project, before=...)` 按调用方（S1）传入的规则只删内容、保留元数据；
  它自行检测活动 UoW 并在其中拒绝运行（抛 `ARTIFACT_ERROR`），但内部仍在短事务中执行对象删除。
- 默认永久保留（FR-DATA-004）。

## 追溯与派生（FD-ART-013）

- `lineage(id)`：`sources`（向上来源链，传递闭包）、`derived`（向下派生链）。
- `version_history(id)`：同版本链全部产物按版本升序。
- `pack(ids, include_phi_content=True)` / `unpack(archive, target_project)`：项目导出/导入；
  导入保留创建方/分级/状态/构建记录/版本并按映射恢复来源与前版本关系，返回"旧→新 ID"映射
  （构建记录内的产物 ID 由调用方按映射改写）（CP-PRJ-009、BP-EXP-002、AC-DATA-01）。
- 0.1.2 的解包在写入每个对象后**回读重算哈希**再登记；任一哈希不符整体失败，并删除本次已写
  对象（删不掉的键在 `details.objectKeys`）。失败后不留下"无行引用"的对象。
- 已知限制：清单文件本身未认证，`archive_hash` 需调用方保存比对；`pack`/`unpack` 全量缓冲归档
  （SRS §11）。

## 子系统初始化登记示例

各子系统在自己的 `initialize()` 中登记拥有的类型（与初始目录合并）：
```python
s0.register_artifact_types({"BLUEPRINT": ["S2"], "METADATA_PACKAGE": ["S2"]})
# 新增某子系统私有类型：
s0.register_artifact_types({"S2_CUSTOM": ["S2"]}, max_sizes={"S2_CUSTOM": 50_000_000})
```
