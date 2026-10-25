"""S4 到 S0 产物服务的网关。

S4 存在数据库之外的东西一律经这里出入，这让外部 I/O 边界保持显式：staging 与内容读取
是 external I/O（必须在事务外），登记是事务内写入，可由 S1 并入其事务（00 文档 6.2、
FD-ART-005）。分级由 S0 校验：派生产物不得低于任何来源（DX-DSC-005、DX-EXE-022 要求
发现结果与 Snapshot 为 CONFIDENTIAL）。
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterable
from typing import Any

from factory_foundation.api import (
    ArtifactCreate,
    ArtifactInfo,
    ArtifactQuery,
    DataClassification,
    NotFoundError,
    Page,
    StagingHandle,
    TransactionMisuseError,
    content_hash,
    external_io,
    file_json,
    get_artifact,
    query_artifacts,
    read_content_bytes,
    register_artifact,
    stage_content,
    structure_hash,
)
from sqlalchemy.orm import Session

from factory_dhis2.errors import InputArtifactInvalidError

JSON_MEDIA_TYPE = "application/json"
ZIP_MEDIA_TYPE = "application/zip"

#: S4 的产物一律由 S4 创建（FD-ART-002 的 owner 校验会拒绝其他值）。
CREATOR = "S4"

#: 目标实例的元数据导出属 CONFIDENTIAL（总体 SRS 20.1），因此 S4 的默认分级就是它。
DEFAULT_CLASSIFICATION = DataClassification.CONFIDENTIAL


@external_io
def stage_bytes(
    data: bytes,
    project_id: str,
    artifact_type: str,
    media_type: str = JSON_MEDIA_TYPE,
    expected_hash: str | None = None,
) -> StagingHandle:
    return stage_content(
        iter([data]),
        project_id=project_id,
        artifact_type=artifact_type,
        expected_hash=expected_hash,
        media_type=media_type,
    )


@external_io
def stage_json(document: Any, project_id: str, artifact_type: str) -> StagingHandle:
    """规范化 JSON（键排序、2 空格、LF）——同一内容永远得到同一哈希。"""
    return stage_bytes(file_json(document), project_id, artifact_type, JSON_MEDIA_TYPE)


@external_io
def stage_archive(files: dict[str, bytes], project_id: str, artifact_type: str) -> StagingHandle:
    """确定性 ZIP：文件名排序、固定时间戳。读取 S2 的 METADATA_PACKAGE 不需要它，但
    S4 自己的执行报告包（含 ImportReport 与 Snapshot 引用）用它。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.comment = b""
        for name in sorted(files):
            info = zipfile.ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0
            archive.writestr(info, files[name])
    return stage_bytes(buffer.getvalue(), project_id, artifact_type, ZIP_MEDIA_TYPE)


def register_document(
    handle: StagingHandle | str,
    *,
    project_id: str,
    artifact_type: str,
    data_classification: DataClassification = DEFAULT_CLASSIFICATION,
    source_artifact_ids: list[str] | None = None,
    workflow_id: str | None = None,
    build_record: dict[str, Any] | None = None,
    media_type: str | None = None,
    session: Session | None = None,
) -> ArtifactInfo:
    """把已 staged 的内容登记为产物（事务内，FD-ART-005）。"""
    resolved = media_type or (handle.media_type if isinstance(handle, StagingHandle) else None)
    create = ArtifactCreate(
        project_id=project_id,
        type=artifact_type,
        media_type=resolved or JSON_MEDIA_TYPE,
        data_classification=data_classification,
        creator_subsystem=CREATOR,
        workflow_id=workflow_id,
        build_record=build_record,
        source_artifact_ids=source_artifact_ids or [],
    )
    return register_artifact(handle, create, session=session)


@external_io
def stage_document(
    document: Any, *, project_id: str, artifact_type: str
) -> StagingHandle:
    """把文档暂存到对象存储（**事务外**），返回登记用的句柄。

    暂存是外部 I/O，登记是事务内写入，两者必须分开（00 文档 6.2）：在一个已经打开的事务里
    暂存会直接抛 ``TRANSACTION_MISUSE``。因此调用形态总是“先 :func:`stage_document`，
    再在自己的短事务里 :func:`register_staged`”——产物登记与依赖它的状态变更、审计记录
    因此仍在同一事务中（D-11）。
    """
    return stage_json(document, project_id, artifact_type)


def register_staged(
    handle: StagingHandle,
    *,
    project_id: str,
    artifact_type: str,
    data_classification: DataClassification = DEFAULT_CLASSIFICATION,
    source_artifact_ids: list[str] | None = None,
    workflow_id: str | None = None,
    build_record: dict[str, Any] | None = None,
    media_type: str | None = None,
    session: Session,
) -> ArtifactInfo:
    """在调用方的事务里把已暂存的内容登记为产物。"""
    return register_document(
        handle,
        project_id=project_id,
        artifact_type=artifact_type,
        data_classification=data_classification,
        source_artifact_ids=source_artifact_ids,
        workflow_id=workflow_id,
        build_record=build_record,
        media_type=media_type,
        session=session,
    )


def stage_and_register_json(
    document: Any,
    *,
    project_id: str,
    artifact_type: str,
    data_classification: DataClassification = DEFAULT_CLASSIFICATION,
    source_artifact_ids: list[str] | None = None,
    workflow_id: str | None = None,
    build_record: dict[str, Any] | None = None,
) -> ArtifactInfo:
    """“暂存 → 登记”一体，只可用于**没有打开事务**的调用点。

    需要把登记与状态变更、审计放进同一事务时，用 :func:`stage_document` +
    :func:`register_staged`，别在这里传 ``session``：传了就会在 S0 的守卫下失败，
    而那个失败说明调用点对事务边界的理解是错的。
    """
    from factory_foundation.api import has_active_uow

    if has_active_uow():
        raise TransactionMisuseError(
            "stage_and_register_json must run outside a unit of work: use "
            "stage_document() then register_staged(session=...) inside the transaction",
            details={"artifactType": artifact_type, "project_id": project_id},
        )
    handle = stage_json(document, project_id, artifact_type)
    return register_document(
        handle,
        project_id=project_id,
        artifact_type=artifact_type,
        data_classification=data_classification,
        source_artifact_ids=source_artifact_ids,
        workflow_id=workflow_id,
        build_record=build_record,
        media_type=None,
        session=None,
    )


def read_metadata(artifact_id: str, session: Session | None = None) -> ArtifactInfo:
    """只读元数据，因此在事务内安全。"""
    return get_artifact(artifact_id, session=session)


@external_io
def read_json(artifact_id: str) -> dict[str, Any]:
    data, _classification, _media = read_content_bytes(artifact_id)
    document: Any = json.loads(data)
    if not isinstance(document, dict):
        raise InputArtifactInvalidError(
            f"artifact {artifact_id} is not a JSON object",
            artifact_id=artifact_id,
            details={"artifactId": artifact_id},
        )
    result: dict[str, Any] = document
    return result


@external_io
def read_archive(artifact_id: str) -> dict[str, bytes]:
    """解出一个 ZIP 产物（S2 的 METADATA_PACKAGE）的全部内容，按名称排序。"""
    data, _classification, media = read_content_bytes(artifact_id)
    if media != ZIP_MEDIA_TYPE and not _looks_like_zip(data):
        raise InputArtifactInvalidError(
            f"artifact {artifact_id} is not a ZIP package",
            artifact_id=artifact_id,
            details={"artifactId": artifact_id, "mediaType": media},
        )
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in sorted(archive.namelist())}


def _looks_like_zip(data: bytes) -> bool:
    return data[:2] == b"PK"


@external_io
def read_json_archive_file(artifact_id: str, name: str) -> dict[str, Any]:
    files = read_archive(artifact_id)
    raw = files.get(name)
    if raw is None:
        raise InputArtifactInvalidError(
            f"package {artifact_id} has no {name!r}",
            artifact_id=artifact_id,
            details={"artifactId": artifact_id, "file": name, "present": sorted(files)},
        )
    document: Any = json.loads(raw)
    if not isinstance(document, dict):
        raise InputArtifactInvalidError(
            f"{name} of package {artifact_id} is not a JSON object",
            artifact_id=artifact_id,
            details={"artifactId": artifact_id, "file": name},
        )
    result: dict[str, Any] = document
    return result


def latest_of_type(
    project_id: str | None,
    artifact_type: str,
    session: Session | None = None,
) -> ArtifactInfo | None:
    """某项目某类型的最新产物（新鲜度检查与 Diff 用）。"""
    page: Page[ArtifactInfo] = query_artifacts(
        ArtifactQuery(project_id=project_id, type=artifact_type, limit=1, descending=True),
        session=session,
    )
    return page.items[0] if page.items else None


def classification_of_inputs(artifact_ids: Iterable[str]) -> DataClassification:
    """派生产物必须声明的分级（不低于任何来源，S0 会拒绝更低）。"""
    levels: list[DataClassification] = [DEFAULT_CLASSIFICATION]
    for artifact_id in sorted(set(artifact_ids)):
        try:
            levels.append(read_metadata(artifact_id).data_classification)
        except NotFoundError:
            continue
    return DataClassification.max_of(levels)


__all__ = [
    "CREATOR",
    "DEFAULT_CLASSIFICATION",
    "JSON_MEDIA_TYPE",
    "ZIP_MEDIA_TYPE",
    "classification_of_inputs",
    "content_hash",
    "latest_of_type",
    "read_archive",
    "read_json",
    "read_json_archive_file",
    "read_metadata",
    "register_document",
    "register_staged",
    "stage_and_register_json",
    "stage_archive",
    "stage_bytes",
    "stage_document",
    "stage_json",
    "structure_hash",
]
