"""契约测试用的载荷装配。

S4 不能创建 ``METADATA_PACKAGE``（产物目录里它属于 S2，FD-ART-002 会在数据库层拒绝越权
创建者），所以这里按 S2 的包布局（``metadata.json`` + ``metadata_manifest.json`` 十二段，
见 BP-CMP-027a）用 S2 的名义登记——**测试装配**是这件事唯一合法的场合。
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import (
    ArtifactCreate,
    DataClassification,
    register_artifact,
    stage_content,
    structure_hash,
    unit_of_work,
)

#: S2 实际发出的段名。总体 SRS 12.4 把其中一段叫 ``hashes``，S2 的实现里叫
#: ``payloadFiles``（每文件的内容哈希）——S4 按产物的实际形状读，不按文档的字面猜。
MANIFEST_SECTIONS = (
    "buildRecord",
    "objects",
    "payloadFiles",
    "dispositions",
    "provenance",
    "managedScope",
    "observationBaselines",
    "uidToBusinessKey",
    "controlledFields",
    "environmentDependentObjects",
    "expressionValidation",
    "deletionCandidates",
)


def default_category_combo(environment_key: str) -> str:
    """在线实例的 default CategoryCombo UID。

    契约测试要提交一个真的能被实例接受的对象，而 ``dataElement`` 需要
    ``categoryCombo``；不同实例的 UID 不同，因此**问实例**而不是猜一个常量。
    """
    from factory_foundation.api import unit_of_work

    from factory_dhis2.client.session import Dhis2Client
    from factory_dhis2.enums import CredentialScope
    from factory_dhis2.environment.loader import build_target

    with unit_of_work() as session:
        target = build_target(session, environment_key, require_probe=False)
    client = Dhis2Client(target, scope=CredentialScope.DISCOVERY)
    response = client.call(
        "metadata.read",
        dhis2_type="categoryCombo",
        params={"fields": "id,name", "filter": "name:eq:default", "pageSize": "1"},
    )
    rows = response.payload.get("categoryCombos") if isinstance(response.payload, dict) else None
    for row in rows or []:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            return str(row["id"])
    raise AssertionError(f"实例上没有名为 default 的 categoryCombo：{response.payload!r}")


def new_uid(environment_key: str) -> str:
    from factory_dhis2.api import generate_uids

    return generate_uids(environment_key, 1)[0]


def build_single_object_package(
    *,
    environment_key: str,
    code: str,
    name: str,
    project_id: str = "prj-contract",
    blueprint_version: str = "0.1.0",
    uid: str | None = None,
    category_combo: str | None = None,
    disposition: str = "CREATE",
    observation_baselines: list[dict[str, Any]] | None = None,
) -> str:
    """生成并登记一个只含一个 DataElement 的 METADATA_PACKAGE，返回产物 ID。

    ``uid`` 与 ``category_combo`` 不给时向实例问：在线契约测试要提交真的能被实例接受的
    对象。离线集成测试两个都给，于是不需要任何网络。
    """
    import io
    import zipfile

    resolved_uid = uid or new_uid(environment_key)
    resolved_combo = category_combo or default_category_combo(environment_key)
    business_key = f"contract.{code}"

    payload: dict[str, Any] = {
        "dataElements": [
            {
                "id": resolved_uid,
                "code": code,
                "name": name,
                "shortName": name[:50],
                "displayFormName": name,
                "valueType": "NUMBER",
                "domainType": "AGGREGATE",
                "aggregationType": "SUM",
                "zeroStorageType": "value_if_zero",
                "categoryCombo": {"id": resolved_combo},
            }
        ]
    }
    manifest: dict[str, Any] = {
        "schemaVersion": 1,
        "projectId": project_id,
        "blueprintVersion": blueprint_version,
        "environmentKey": environment_key,
        "buildRecord": {
            "buildId": f"bld-contract-{code}",
            "kind": "ENVIRONMENT_BOUND",
            "payloadHash": structure_hash(payload),
            "contractTest": True,
        },
        "objects": [
            {
                "businessKey": business_key,
                "dhis2Type": "dataElement",
                "uid": resolved_uid,
                "code": code,
                "disposition": disposition,
                "state": disposition,
            }
        ],
        "payloadFiles": {"metadata.json": structure_hash(payload)},
        "dispositions": {business_key: disposition},
        "provenance": {"blueprint": {"type": "BLUEPRINT", "artifactHash": "sha256:" + "0" * 64}},
        "managedScope": [
            {
                "businessKey": business_key,
                "dhis2Type": "dataElement",
                "uid": resolved_uid,
                "disposition": disposition,
            }
        ],
        # 观测基准由调用方给出：漂移检查比较的就是它（DX-EXE-020）
        "observationBaselines": list(observation_baselines or []),
        "uidToBusinessKey": [
            {
                "businessKey": business_key,
                "uid": resolved_uid,
                "dhis2Type": "dataElement",
                "code": code,
                "disposition": disposition,
                "variesByEnvironment": False,
            }
        ],
        "controlledFields": {},
        "environmentDependentObjects": [],
        "expressionValidation": [],
        "deletionCandidates": [],
        "referencesOutsideManagedScope": [
            {"businessKey": "default.categoryCombo", "dhis2Type": "categoryCombo", "uid": category_combo, "disposition": "REFERENCE"}
        ],
        "changeSummary": {
            "environmentKey": environment_key,
            "blueprintVersion": blueprint_version,
            "byType": {"dataElement": {"create": 1}},
            "referencesOutsideManagedScope": [],
            "environmentDependentObjects": [],
        },
        "notes": ["契约测试包：单个 DataElement"],
    }

    def dump(document: Any) -> bytes:
        from factory_foundation.api import file_json

        return file_json(document)

    files = {"metadata.json": dump(payload), "metadata_manifest.json": dump(manifest)}
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in sorted(files):
            entry_info = zipfile.ZipInfo(entry, date_time=(1980, 1, 1, 0, 0, 0))
            entry_info.compress_type = zipfile.ZIP_DEFLATED
            entry_info.create_system = 0
            entry_info.external_attr = 0
            archive.writestr(entry_info, files[entry])
    handle = stage_content(
        iter([buffer.getvalue()]),
        project_id=project_id,
        artifact_type="METADATA_PACKAGE",
        media_type="application/zip",
    )
    with unit_of_work() as session:
        registered = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project_id,
                type="METADATA_PACKAGE",
                media_type="application/zip",
                data_classification=DataClassification.INTERNAL,
                creator_subsystem="S2",
                build_record=manifest["buildRecord"],
            ),
            session=session,
        )
    assert all(section in manifest for section in MANIFEST_SECTIONS)
    return str(registered.id)


__all__ = ["MANIFEST_SECTIONS", "build_single_object_package", "default_category_combo", "new_uid"]
