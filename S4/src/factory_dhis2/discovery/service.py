"""元数据发现（DX-DSC-001…009、流程 6.2）。

发现是**观测**，不是拉取一切：产出的是“既有元数据索引 + 范围内对象的当前内容”，
带时间戳、带内容哈希，作为 DISCOVERY_RESULT 产物持久化（DX-DSC-005）。S2 之后所有
UPDATE 合并、Diff 与漂移基准都来自这里，所以：

* 只用 discovery 凭证范围，绝不写入（DX-DSC-004、DX-BR-04）——一次发现跑完，实例上
  什么都应该没变；
* 内容哈希在排除易变字段后计算（DX-DSC-007），否则 ``lastUpdated`` 会让漂移检查天天误报；
* 接受幂等键、进度与取消（DX-DSC-008）：大型实例的发现是分钟级的长任务，而它随时可能被
  上层工作流取消。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from factory_foundation.api import (
    CancellationToken,
    DataClassification,
    ProgressReporter,
    external_io,
    idempotency,
    new_id,
    structure_hash,
    unit_of_work,
)
from pydantic import ValidationError

from factory_dhis2 import audit
from factory_dhis2.artifacts import register_staged, stage_document
from factory_dhis2.capability.runtime_profile import apply_environment_capability
from factory_dhis2.client.session import Dhis2Client
from factory_dhis2.contract.discovery_result import (
    DiscoveredObjectDocument,
    as_artifact_content,
    build_discovery_result_document,
)
from factory_dhis2.discovery import defaults as _defaults
from factory_dhis2.discovery import orgunits as _orgunits
from factory_dhis2.discovery import scope as scope_module
from factory_dhis2.discovery.canonical import object_hash
from factory_dhis2.dto.discovery import (
    DiscoveryObjectCandidate,
    DiscoveryObjectSearch,
    DiscoveryOutcome,
    DiscoveryResultInfo,
    DiscoveryScope,
)
from factory_dhis2.dto.issue import Issue, make_issue
from factory_dhis2.enums import CredentialScope, IssueLevel
from factory_dhis2.environment.loader import build_target
from factory_dhis2.errors import Dhis2Error, InputArtifactInvalidError
from factory_dhis2.models.profile import DiscoveryIndexRow
from factory_dhis2.registries import IdemScope

#: 索引字段（不含内容）与内容字段的差别只在于 ``:all``：先取索引，再按明确清单取内容，
#  比一次全量拉取全部内容快一个量级。
INDEX_FIELDS = "id,code,name,created,lastUpdated"
CONTENT_FIELDS = "id,code,name,created,lastUpdated,:all"


@external_io
def discover(
    environment_key: str,
    project_id: str | None,
    scope: str | Mapping[str, Any] | DiscoveryScope | None = None,
    explicit_refs: Sequence[Mapping[str, Any]] | None = None,
    *,
    idempotency_key: str | None = None,
    progress: ProgressReporter | None = None,
    cancel: CancellationToken | None = None,
) -> DiscoveryOutcome:
    """发现一个环境的既有元数据（SRS 7.2）。"""
    digest = structure_hash(
        {
            "operation": "discover",
            "environmentKey": environment_key,
            "projectId": project_id,
            "scope": _scope_document(scope),
            "explicitRefs": [dict(item) for item in (explicit_refs or ())],
        }
    )
    begun = (
        idempotency.begin(IdemScope.DISCOVERY, idempotency_key, digest)
        if idempotency_key
        else None
    )
    if begun is not None and begun.already_completed and begun.first_result:
        return DiscoveryOutcome.model_validate(begun.first_result)

    with unit_of_work() as session:
        target = build_target(session, environment_key, require_probe=True)
    # 发现读的是"这个实例上到底有哪些端点"，因此同样以环境的能力产物为准（DX-CLI-005、DX-DSC-001）
    target = apply_environment_capability(target)
    client = Dhis2Client(target, scope=CredentialScope.DISCOVERY)
    resolved = scope_module.resolve(_scope_document(scope), explicit_refs, target.profile)

    objects: list[DiscoveredObjectDocument] = []
    issues: list[Issue] = []
    for dhis2_type in resolved.unsupported_types:
        issues.append(
            make_issue(
                "DX-DSC-002",
                IssueLevel.BLOCKING,
                f"要发现的对象类型 {dhis2_type!r} 不在 {target.profile.profile_version} 的"
                "资源目录里，无法读取",
                object_key=dhis2_type,
            )
        )

    if resolved.greenfield:
        uids, contents = _defaults.read_defaults(client)
        for dhis2_type, content in sorted(contents.items()):
            objects.append(
                _document_of(dhis2_type, content, target.profile.volatile_fields, with_content=True)
            )
    else:
        objects.extend(
            _read_types(
                client, resolved, progress=progress, cancel=cancel, issues=issues
            )
        )
        # DX-DSC-002's promise holds here: an explicit list reads only the objects it names.
        # The four `default` objects go into the result's `defaults` map, which is where a
        # compile looks for the CategoryCombo it falls back to (DX-DSC-003, FR-COV-007);
        # pushing documents for them into `objects` would make "read only what was asked for"
        # untestable, and S2 has the UID it needs either way.
        uids, _contents = _defaults.read_defaults(client)
    org_read = _orgunits.read_org_units(client, resolved.org_unit_depth, cancel=cancel)
    org_units = org_read.documents
    if org_read.truncated:
        # DX-DSC-006：静默截断会让 S2 引用到一个"目标环境里不存在"的 OrgUnit，而那个错误
        # 要到编译期才现身。宁可现在 BLOCKING。
        issues.append(
            make_issue(
                "DX-DSC-006",
                IssueLevel.BLOCKING,
                f"组织层级读取被截断：到深度 {resolved.org_unit_depth} 共 {org_read.total_seen} "
                f"个节点，只保留了 {_orgunits.MAX_NODES} 个",
                object_key="organisationUnit",
                suggested_action="缩小 orgUnitDepth，或按明确的组织单元分批发现",
            )
        )

    discovered_at = datetime.now(UTC).isoformat()
    document = build_discovery_result_document(
        environment_key=environment_key,
        discovered_at=discovered_at,
        version=target.dhis2_version,
        scope={
            "kind": resolved.kind,
            "dhis2Types": resolved.dhis2_types,
            "explicitRefs": {key: sorted(values) for key, values in sorted(resolved.explicit_uids.items())},
            "explicitCodes": {
                key: sorted(values) for key, values in sorted(resolved.explicit_codes.items())
            },
            "codePrefixes": list(resolved.code_prefixes),
            "orgUnitDepth": resolved.org_unit_depth,
            "greenfield": resolved.greenfield,
        },
        objects=objects,
        defaults=uids,
        org_units=org_units,
    )
    content = as_artifact_content(document)
    owner = project_id or f"env-{environment_key}"
    handle = stage_document(content, project_id=owner, artifact_type="DISCOVERY_RESULT")
    blocking = [issue for issue in issues if issue.is_blocking]

    with unit_of_work() as session:
        info = register_staged(
            handle,
            project_id=owner,
            artifact_type="DISCOVERY_RESULT",
            # 目标实例的元数据导出属 CONFIDENTIAL（总体 SRS 20.1、DX-DSC-005）
            data_classification=DataClassification.CONFIDENTIAL,
            source_artifact_ids=[target.capability_profile_artifact_id]
            if target.capability_profile_artifact_id
            else [],
            session=session,
        )
        row = DiscoveryIndexRow(
            id=new_id("dxdsc"),
            environment_key=environment_key,
            project_id=project_id,
            scope=dict(document.scope),
            artifact_id=info.id,
            content_hash=info.content_hash,
            discovered_at=datetime.now(UTC),
            idempotency_key=idempotency_key,
            object_count=len(document.objects),
            # DX-DSC-010：列表要能说出"这次发现是在哪个版本上看见的"
            dhis2_version=target.dhis2_version,
            blocking_issues=[issue.model_dump(mode="json") for issue in blocking],
        )
        session.add(row)
        audit.record(
            audit.DISCOVERY_COMPLETED,
            object_type="DiscoveryResult",
            object_id=row.id,
            project_id=project_id,
            after={
                "environmentKey": environment_key,
                "artifactId": info.id,
                "objects": row.object_count,
                "scopeKind": resolved.kind,
                "blocking": len(blocking),
                # 只记范围，不记内容：审计里不该出现实例的元数据
            },
            session=session,
        )
        session.flush()
        discovery_id = row.id

    outcome = DiscoveryOutcome(
        discovery_result_artifact_id=info.id,
        capability_profile_artifact_id=target.capability_profile_artifact_id,
        blocking_issues=blocking,
        version=target.dhis2_version,
        default_object_ids=uids,
        object_count=len(document.objects),
        discovery_id=discovery_id,
        content_hash=info.content_hash,
    )
    if idempotency_key and begun is not None:
        idempotency.complete(
            IdemScope.DISCOVERY, idempotency_key, outcome.model_dump(mode="json")
        )
    return outcome


def _read_types(
    client: Dhis2Client,
    resolved: scope_module.ResolvedScope,
    *,
    progress: ProgressReporter | None,
    cancel: CancellationToken | None,
    issues: list[Issue],
) -> list[DiscoveredObjectDocument]:
    """按类型读取；明确清单与 code 前缀各自成一次请求，避免全量拉取（DX-DSC-002）。"""
    out: list[DiscoveredObjectDocument] = []
    profile = client.target.profile
    types = resolved.types_to_read()
    for index, dhis2_type in enumerate(types):
        if cancel is not None:
            cancel.raise_if_cancelled()
        selections = resolved.selection_for(dhis2_type)
        rows: dict[str, Mapping[str, Any]] = {}
        for selection in selections:
            filters = [selection] if selection else []
            try:
                for row in client.list_all(
                    dhis2_type,
                    fields=CONTENT_FIELDS if resolved.with_content else INDEX_FIELDS,
                    filters=filters,
                    cancel=cancel,
                ):
                    uid = row.get("id")
                    if not isinstance(uid, str) or uid in rows:
                        continue
                    # code 前缀要在本地验证：ilike 是"包含"匹配，只有真的以它开头才算前缀
                    if resolved.code_prefixes and not _matches_a_prefix(row.get("code"), resolved.code_prefixes):
                        continue
                    rows[uid] = row
            except Dhis2Error as exc:
                issues.append(
                    make_issue(
                        "DX-DSC-001",
                        IssueLevel.BLOCKING,
                        f"读取 {dhis2_type} 失败：{exc.message}",
                        object_key=dhis2_type,
                    )
                )
        for found_row in rows.values():
            out.append(
                _document_of(
                    dhis2_type,
                    found_row,
                    profile.volatile_fields,
                    with_content=resolved.with_content,
                )
            )
        if progress is not None and types:
            progress.report(
                step="DISCOVERY",
                completed_fraction=(index + 1) / len(types),
                message=f"{dhis2_type}: {len(rows)}",
            )
    return out


def _matches_a_prefix(code: Any, prefixes: Sequence[str]) -> bool:
    value = code if isinstance(code, str) else ""
    return any(value.startswith(prefix) for prefix in prefixes)


def _document_of(
    dhis2_type: str,
    row: Mapping[str, Any],
    volatile_fields: Sequence[str],
    *,
    with_content: bool,
) -> DiscoveredObjectDocument:
    return DiscoveredObjectDocument(
        dhis2_type=dhis2_type,
        uid=str(row.get("id") or ""),
        code=row.get("code") if isinstance(row.get("code"), str) else None,
        name=row.get("name") if isinstance(row.get("name"), str) else None,
        last_updated=row.get("lastUpdated") if isinstance(row.get("lastUpdated"), str) else None,
        content_hash=object_hash(row, volatile_fields),
        content=dict(row) if with_content else None,
        references=_references_of(row),
    )


def _references_of(row: Mapping[str, Any]) -> dict[str, Any]:
    """关键引用关系：单对象引用、引用数组与 ``{"members": [...]}`` 包装都算。

    S2 的映射与影响分析需要知道"这个对象引用了谁"，而它不该为此再读一次实例。此前只收
    顶层 ``{"id": ...}`` 形态，``programStages``、``categories`` 这类**引用数组**全部丢失，
    于是"这个 Program 引用了哪些 DataElement"在发现结果里根本不存在。
    """
    out: dict[str, Any] = {}
    for key, value in row.items():
        if _is_reference(value):
            out[str(key)] = str(value["id"])
            continue
        collected = _reference_ids(value)
        if collected:
            out[str(key)] = collected
    return out


def _is_reference(value: Any) -> bool:
    return isinstance(value, Mapping) and bool(value.get("id"))


def _reference_ids(value: Any) -> list[str]:
    """一个引用集合：数组里的每个 ``{"id": …}``，以及数组包在 ``members`` 里的形态。"""
    if isinstance(value, list):
        return sorted(
            {
                str(item["id"])
                for item in value
                if isinstance(item, Mapping) and item.get("id")
            }
        )
    if isinstance(value, Mapping):
        inner = value.get("members")
        if isinstance(inner, list):
            return _reference_ids(inner)
    return []


def _scope_document(scope: str | Mapping[str, Any] | DiscoveryScope | None) -> str | dict[str, Any] | None:
    if isinstance(scope, DiscoveryScope):
        return scope.model_dump(mode="json", by_alias=True)
    if isinstance(scope, Mapping):
        return dict(scope)
    return scope


def discovery_document(session: Any, discovery_id: str) -> dict[str, Any]:
    """按索引行取发现结果文档（S2 与 Diff 都按产物 ID 读，索引只是入口）。"""
    row = session.get(DiscoveryIndexRow, discovery_id)
    if row is None:
        from factory_foundation.api import NotFoundError

        raise NotFoundError(
            f"unknown discovery {discovery_id!r}", details={"discoveryId": discovery_id}
        )
    from factory_dhis2.artifacts import read_json

    return read_json(row.artifact_id)


def latest_discovery(
    session: Any,
    environment_key: str,
    project_id: str | None = None,
) -> DiscoveryIndexRow | None:
    """该环境（可限定项目）最近一次发现索引。"""
    from sqlalchemy import select

    statement = (
        select(DiscoveryIndexRow)
        .where(DiscoveryIndexRow.environment_key == environment_key)
        .order_by(DiscoveryIndexRow.discovered_at.desc())
    )
    if project_id is not None:
        statement = statement.where(DiscoveryIndexRow.project_id == project_id)
    found: DiscoveryIndexRow | None = session.execute(statement.limit(1)).scalars().first()
    return found


def search_objects(
    discovery_artifact_id: str,
    dhis2_type: str | None = None,
    query: str | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
) -> DiscoveryObjectSearch:
    """在一份已完成的发现结果里按类型与 code / name 关键字检索对象（DX-DSC-011）。

    存在的理由是**不访问 DHIS2**：环境绑定的人工设置要从该环境的候选对象里选（S1
    CP-UI-005），若为此再跑一次发现，一来要 import 之外的凭证、二来在 5,000 对象规模上
    是分钟级的活儿。发现结果格式归 S4 所有，所以由 S4 解析 —— 调用方不必依赖它的内部结构。
    """
    from factory_dhis2.artifacts import read_json
    from factory_dhis2.contract.discovery_result import DiscoveryResultDocument

    try:
        document = DiscoveryResultDocument.model_validate(read_json(discovery_artifact_id))
    except (ValidationError, InputArtifactInvalidError) as exc:
        raise InputArtifactInvalidError(
            f"artifact {discovery_artifact_id!r} is not a DISCOVERY_RESULT document: {exc}",
            artifact_id=discovery_artifact_id,
            details={"artifactId": discovery_artifact_id},
        ) from exc

    needle = (query or "").strip().casefold()
    matched: list[DiscoveryObjectCandidate] = []
    for item in document.objects:
        if dhis2_type and item.dhis2_type != dhis2_type:
            continue
        if needle:
            haystack = " ".join(
                part for part in (item.code, item.name, item.uid) if part
            ).casefold()
            if needle not in haystack:
                continue
        matched.append(
            DiscoveryObjectCandidate(
                uid=item.uid,
                dhis2_type=item.dhis2_type,
                code=item.code,
                name=item.name,
                last_updated=item.last_updated,
                content_hash=item.content_hash,
            )
        )
    matched.sort(key=lambda item: (item.dhis2_type, item.uid))
    size = max(1, limit)
    start = max(0, offset)
    return DiscoveryObjectSearch(
        discovery_artifact_id=discovery_artifact_id,
        dhis2_type=dhis2_type,
        query=query,
        items=matched[start : start + size],
        total=len(matched),
        limit=size,
        offset=start,
    )


def list_discovery_results(
    environment_key: str,
    project_id: str | None = None,
    scope: str | None = None,
    *,
    limit: int = 50,
    offset: int = 0,
    session: Any = None,
) -> list[DiscoveryResultInfo]:
    """列出某环境已存储的发现索引（S5IF-ENV11）。"""
    from factory_foundation.api import unit_of_work
    from sqlalchemy import select

    def _read(tx: Any) -> list[DiscoveryResultInfo]:
        statement = (
            select(DiscoveryIndexRow)
            .where(DiscoveryIndexRow.environment_key == environment_key)
            .order_by(DiscoveryIndexRow.discovered_at.desc())
        )
        if project_id:
            statement = statement.where(DiscoveryIndexRow.project_id == project_id)
        if scope:
            # 过滤必须在 SQL 里做：放在 offset/limit 之后于内存中过滤，翻第二页时上一页的
            # 条目会被重复或漏掉，每页数量也不稳定（DX-DSC-010 的"分页返回"就是这个意思）
            statement = statement.where(DiscoveryIndexRow.scope["kind"].astext == scope)
        rows = list(tx.execute(statement.offset(offset).limit(max(1, limit))).scalars())
        return [
            DiscoveryResultInfo(
                artifact_id=row.artifact_id,
                project_id=row.project_id,
                scope=(str(row.scope.get("kind") or "") or None)
                if isinstance(row.scope, dict)
                else None,
                object_count=row.object_count,
                blocking_issues=[
                    Issue.model_validate(item)
                    for item in (row.blocking_issues or [])
                    if isinstance(item, dict)
                ],
                # DX-DSC-010：索引要给出"当时那个实例的版本"
                version=row.dhis2_version,
                discovered_at=row.discovered_at,
                content_hash=row.content_hash,
                discovery_id=row.id,
                environment_key=row.environment_key,
            )
            for row in rows
        ]

    if session is not None:
        return _read(session)
    with unit_of_work() as tx:
        return _read(tx)


__all__ = [
    "CONTENT_FIELDS",
    "INDEX_FIELDS",
    "discover",
    "discovery_document",
    "latest_discovery",
    "list_discovery_results",
    "search_objects",
]
