# S0 公共基础库 — 开发者与调试文档 (Doc)

面向 **S1～S5** 开发组的参考文档，说明如何依赖、调用、调试 `factory_foundation`
（S0 公共基础库），以及 `core` schema 的数据库连接与结构约定。
本文档对照 `factory-foundation 0.1.2`（`pyproject.toml` / `../CHANGELOG.md`）。

> 权威来源仍是 `../../requirements/` 下的 SRS 与《00 总体架构与集成约定》。
> 本目录是对已交付代码的**接口与运维说明**，若与代码不一致以代码为准并请反馈修正。

## 阅读顺序

| 文档 | 用途 | 主要读者 |
| --- | --- | --- |
| [`integration-guide.md`](integration-guide.md) | 如何依赖 S0、进程启动顺序、配置项、迁移、测试替身 | 全部后端组 |
| [`api-reference.md`](api-reference.md) | `factory_foundation.api` 全部公共函数/类型的签名、语义、异常 | 全部后端组 |
| [`database-schema.md`](database-schema.md) | `core` schema 表结构、连接串、迁移与 append-only 触发器 | S0/S1 运维、DBA |
| [`artifact-catalog.md`](artifact-catalog.md) | 产物类型→所有者、数据分级顺序、产物状态 | S1/S2/S3/S4 |
| [`error-codes.md`](error-codes.md) | 错误码目录、可重试性、登记方式 | 全部后端组 |
| [`debugging.md`](debugging.md) | 对象存储路径、Secret 掩码、审计哈希链核查、操作上下文、日志/追踪排障 | 全部后端组 |
| [`traceability.md`](traceability.md) | FD-* 需求 → 实现位置 → 测试用例 对照表 | 架构组、测试组、评审 |

## 30 秒上手

```python
from factory_foundation.api import (
    unit_of_work, stage_content, register_artifact, ArtifactCreate,
    DataClassification, audit, AuditEventInput, bind_operation_context,
    register_audit_event_types,
)

# 0) 登记本子系统要写的审计事件类型（未登记写入会抛 VALIDATION_ERROR）
register_audit_event_types({"s2.artifact.created": "S2"})

# 1) 外部 I/O：把内容写入对象存储并回读校验哈希（必须在任何事务之外）
handle = stage_content(b'{"hello":1}', project_id="p1",
                       artifact_type="MANIFEST", media_type="application/json")

# 2) 业务事务：登记产物 + 写审计，二者同一事务提交
with bind_operation_context(operator_name="alice", project_id="p1"), unit_of_work() as s:
    info = register_artifact(handle, ArtifactCreate(
        project_id="p1", type="MANIFEST", media_type="application/json",
        data_classification=DataClassification.INTERNAL, creator_subsystem="S2"), session=s)
    audit(AuditEventInput(event_type="s2.artifact.created", object_id=info.id), session=s)
```

跨子系统调用**只允许** `import factory_foundation.api`（或 `.testing` 中的替身）。
其余模块（`factory_foundation.artifacts.service` 等）为内部实现，非稳定接口。
