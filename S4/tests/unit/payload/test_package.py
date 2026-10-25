"""读取 S2 的 METADATA_PACKAGE（DX-EXE-003、DX-ASM-02、BP-CMP-027a）。

S4 不调用 S2：执行需要的一切都在包里的 ``metadata_manifest.json``。这些测试按 S2 实际
发出的形状（``S2/src/factory_blueprint/compilers/manifest.py`` 的 12 段）造一个真产物，
经 S0 登记后用 S4 的读取器解开，断言每个访问器拿到的是 manifest 里的事实；缺段与类型
不符都必须是阻塞错误，而不是“猜一个继续”。
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

import pytest
from factory_foundation.api import (
    ArtifactCreate,
    DataClassification,
    register_artifact,
    stage_content,
    unit_of_work,
)

from factory_dhis2.errors import InputArtifactInvalidError
from factory_dhis2.payload.package import (
    MANIFEST_FILE,
    METADATA_FILE,
    REQUIRED_MANIFEST_SECTIONS,
    MetadataPackage,
    package_bytes_hash,
    read_package,
    require_complete,
)

pytestmark = pytest.mark.usefixtures("harness")

PROJECT_ID = "prj-package-owner"

#: S2 的 metadata_manifest.json 的全部 12 段（BP-CMP-027a，顺序照 SRS 12.4）
MANIFEST_SECTIONS: tuple[str, ...] = (
    "buildRecord",
    "objects",
    "hashes",
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

UID_DE = "FTRrcoaog83"
UID_OSET = "uZpRURPNDlJ"


def payload() -> dict[str, Any]:
    return {
        "dataElements": [
            {"id": UID_DE, "name": "体重", "shortName": "体重", "valueType": "NUMBER"},
        ],
        "optionSets": [
            {"id": UID_OSET, "name": "性别", "options": [{"id": "M", "name": "男"}]},
        ],
    }


def manifest(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schemaVersion": 1,
        "projectId": PROJECT_ID,
        "blueprintVersion": "1.4.0",
        "environmentKey": "staging",
        "buildRecord": {"builder": "S2", "inputs": []},
        "objects": [
            {"businessKey": "de.weight", "dhis2Type": "dataElement", "uid": UID_DE},
            {"businessKey": "os.sex", "dhis2Type": "optionSet", "uid": UID_OSET},
        ],
        "hashes": {METADATA_FILE: "sha256:" + "1" * 64},
        "dispositions": {"de.weight": "CREATE", "os.sex": "REUSE", "gone.one": "DELETE"},
        "provenance": {"blueprint": {"type": "BLUEPRINT", "artifactHash": "sha256:" + "2" * 64}},
        "managedScope": [
            {"dhis2Type": "dataElement", "uid": UID_DE, "businessKey": "de.weight"},
            {"dhis2Type": "categoryCombo", "uid": "wkZl0zMZMJp", "businessKey": "cc.default"},
        ],
        "observationBaselines": [
            {
                "businessKey": "os.sex",
                "uid": UID_OSET,
                "observedHash": "sha256:" + "3" * 64,
                "observedLastUpdated": "2026-01-05T09:00:00.000",
            }
        ],
        "uidToBusinessKey": [
            {
                "uid": UID_DE,
                "businessKey": "de.weight",
                "dhis2Type": "dataElement",
                "disposition": "CREATE",
            },
            {
                "uid": UID_OSET,
                "businessKey": "os.sex",
                "dhis2Type": "optionSet",
                "disposition": "REUSE",
                "observedLastUpdated": "2026-01-05T09:00:00.000",
                "observedHash": "sha256:" + "3" * 64,
            },
        ],
        "controlledFields": {"de.weight": ["name", "shortName"], "os.sex": ["name"]},
        "environmentDependentObjects": [],
        "expressionValidation": [
            {
                "businessKey": "de.weight",
                "field": "formExpression",
                "validation": "DHIS2_VALIDATE",
                "compiled": "#{FTRrcoaog83}",
            },
            {
                "businessKey": "os.sex",
                "field": "condition",
                "validation": "POST_VERIFY",
                "compiled": "V{some_missing}",
            },
            {
                "businessKey": "de.weight",
                "field": "description",
                "needsServerCheck": True,
                "compiled": "V{other}",
            },
        ],
        "deletionCandidates": [
            {"businessKey": "gone.one", "dhis2Type": "dataElement", "uid": "xQ9v3kLm2Zz"}
        ],
    }
    document.update(overrides)
    return document


def zip_bytes(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for name in sorted(files):
            info = zipfile.ZipInfo(filename=name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0
            archive.writestr(info, files[name])
    return buffer.getvalue()


def register_document(
    files: dict[str, bytes],
    *,
    artifact_type: str = "METADATA_PACKAGE",
    creator: str = "S2",
    project_id: str = PROJECT_ID,
) -> str:
    """按 S0 的两段式创建：事务外暂存，事务内登记。"""
    handle = stage_content(
        iter([zip_bytes(files)]),
        project_id=project_id,
        artifact_type=artifact_type,
        media_type="application/zip",
    )
    with unit_of_work() as session:
        info = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project_id,
                type=artifact_type,
                media_type="application/zip",
                data_classification=DataClassification.CONFIDENTIAL,
                creator_subsystem=creator,
            ),
            session=session,
        )
    return info.id


def register_package(
    *, manifest_document: dict[str, Any] | None = None, payload_document: dict[str, Any] | None = None
) -> str:
    return register_document(
        {
            METADATA_FILE: json.dumps(
                payload_document if payload_document is not None else payload(),
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8"),
            MANIFEST_FILE: json.dumps(
                manifest_document if manifest_document is not None else manifest(),
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8"),
        }
    )


# --- DX-EXE-003：解开 S2 的包 -------------------------------------------------


def test_a_metadata_package_from_s2_unwraps_into_payload_and_manifest() -> None:
    """DX-EXE-003：执行输入就是包里的两份 JSON，S4 不调用 S2。"""
    artifact_id = register_package()
    package = read_package(artifact_id)
    assert package.artifact_id == artifact_id
    assert package.payload["dataElements"][0]["id"] == UID_DE
    assert package.manifest["schemaVersion"] == 1
    assert package.missing_sections == []


def test_the_package_carries_the_artifact_content_hash_as_its_payload_hash() -> None:
    """DX-EXE-005：哈希的来源是产物字节，S1 的 Gate 绑定用的是同一个值。"""
    package = read_package(register_package())
    assert package.artifact_hash.startswith("sha256:")
    assert len(package.artifact_hash) == 71


def test_the_package_names_the_project_blueprint_version_and_environment() -> None:
    """DX-ASM-02：包的归属来自 manifest，不来自调用方的口头声明。"""
    package = read_package(register_package())
    assert package.project_id == PROJECT_ID
    assert package.blueprint_version == "1.4.0"
    assert package.environment_key == "staging"


def test_the_object_count_is_the_payload_objects_not_the_manifest_rows() -> None:
    """载荷才是被执行的东西。"""
    package = read_package(register_package())
    assert package.object_count == 2


def test_uid_to_business_key_maps_every_manifest_row() -> None:
    """DX-EXE-027 的前提：ImportReport 的 UID 要能映射回 businessKey。"""
    package = read_package(register_package())
    assert package.uid_to_business_key() == {UID_DE: "de.weight", UID_OSET: "os.sex"}
    assert len(package.uid_map()) == 2


def test_dispositions_are_read_as_declared_per_business_key() -> None:
    """DX-EXE-020 的输入：REUSE / REFERENCE / UPDATE 才需要漂移检查。"""
    package = read_package(register_package())
    assert package.dispositions() == {
        "de.weight": "CREATE",
        "os.sex": "REUSE",
        "gone.one": "DELETE",
    }


def test_managed_scope_observation_baselines_and_deletion_candidates_are_exposed() -> None:
    """DX-EXE-022 / DX-EXE-020 / DX-EXE-038 各自的清单。"""
    package = read_package(register_package())
    assert [row["uid"] for row in package.managed_scope()] == [UID_DE, "wkZl0zMZMJp"]
    assert package.observation_baselines()[0]["observedHash"] == "sha256:" + "3" * 64
    assert package.deletion_candidates()[0]["businessKey"] == "gone.one"


def test_controlled_fields_are_lists_of_field_names_per_business_key() -> None:
    """DX-EXE-040：部署后验证逐字段比对时用的就是这张表。"""
    package = read_package(register_package())
    assert package.controlled_fields() == {
        "de.weight": ["name", "shortName"],
        "os.sex": ["name"],
    }


def test_expression_validation_rows_keep_the_s2_classification() -> None:
    """D-05 / DX-EXE-028：分类是 S2 做的，S4 只读。"""
    package = read_package(register_package())
    rows = package.expression_validation()
    assert [row["validation"] for row in rows[:2]] == ["DHIS2_VALIDATE", "POST_VERIFY"]


def test_payload_objects_are_addressable_by_uid_and_type() -> None:
    """部署后验证与对账按 UID 找载荷对象。"""
    package = read_package(register_package())
    by_uid = package.payload_by_uid()
    assert set(by_uid) == {UID_DE, UID_OSET}
    assert by_uid[UID_DE]["valueType"] == "NUMBER"
    assert package.type_by_uid() == {UID_DE: "dataElement", UID_OSET: "optionSet"}


def test_the_collection_name_is_converted_back_to_the_dhis2_type() -> None:
    """``categories`` → ``category``、``optionSets`` → ``optionSet``。"""
    package = MetadataPackage(
        artifact_id="art-x",
        artifact_hash="sha256:" + "0" * 64,
        payload={
            "categories": [{"id": "a" * 11}],
            "categoryOptionCombos": [{"id": "b" * 11}],
            "dataElements": [{"id": "c" * 11}],
        },
        manifest={},
    )
    assert package.type_by_uid() == {
        "a" * 11: "category",
        "b" * 11: "categoryOptionCombo",
        "c" * 11: "dataElement",
    }


# --- DX-ASM-02：缺段与类型不符 ------------------------------------------------


def test_a_missing_manifest_section_is_reported_not_guessed() -> None:
    """DX-ASM-02：缺哪一段就点名哪一段。"""
    document = manifest()
    del document["managedScope"]
    del document["expressionValidation"]
    package = read_package(register_package(manifest_document=document))
    assert package.missing_sections == ["managedScope", "expressionValidation"]


def test_require_complete_refuses_a_package_that_is_missing_sections() -> None:
    """DX-ASM-02：缺段就是阻塞问题，漂移检查与 Snapshot 无法进行。"""
    document = manifest()
    del document["controlledFields"]
    package = read_package(register_package(manifest_document=document))
    with pytest.raises(InputArtifactInvalidError) as excinfo:
        require_complete(package, "VALIDATE")
    assert excinfo.value.details["missing"] == ["controlledFields"]
    assert excinfo.value.details["rule"] == "DX-ASM-02"


def test_a_complete_package_passes_the_completeness_check() -> None:
    document = manifest()
    package = read_package(register_package(manifest_document=document))
    require_complete(package, "COMMIT")
    assert set(REQUIRED_MANIFEST_SECTIONS) <= set(document)


def test_a_non_metadata_package_artifact_is_refused() -> None:
    """DX-EXE-003：把别的产物当成载荷提交是明确的类型错误。"""
    artifact_id = register_document(
        {"report.json": b'{"ok": true}'}, artifact_type="VALIDATION_REPORT"
    )
    with pytest.raises(InputArtifactInvalidError) as excinfo:
        read_package(artifact_id)
    assert excinfo.value.details["artifactType"] == "VALIDATION_REPORT"


def test_a_package_without_a_manifest_file_is_refused() -> None:
    """DX-EXE-003：没有 manifest 就没有一切可依据的事实。"""
    artifact_id = register_document({METADATA_FILE: b"{}"})
    with pytest.raises(InputArtifactInvalidError) as excinfo:
        read_package(artifact_id)
    assert excinfo.value.details["missingFile"] == MANIFEST_FILE


def test_a_manifest_that_is_not_a_json_object_is_refused() -> None:
    artifact_id = register_document(
        {METADATA_FILE: b"{}", MANIFEST_FILE: b"[1, 2, 3]"}
    )
    with pytest.raises(InputArtifactInvalidError) as excinfo:
        read_package(artifact_id)
    assert excinfo.value.details["file"] == MANIFEST_FILE


# --- 包内字节哈希 -------------------------------------------------------------


def test_package_bytes_hash_is_the_sorted_file_concatenation_hash() -> None:
    """与 S2 的 package_hash 同一定义：同一内容永远同一哈希。"""
    files = {"b.json": b"{}", "a.json": b"{\"x\": 1}"}
    first = package_bytes_hash(files)
    second = package_bytes_hash(dict(reversed(list(files.items()))))
    assert first == second
    assert package_bytes_hash({"a.json": b"{\"x\": 2}"}) != first
