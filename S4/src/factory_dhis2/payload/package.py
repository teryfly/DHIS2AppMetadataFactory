"""读取 S2 的 METADATA_PACKAGE（DX-EXE-003、DX-ASM-02）。

S4 不调用 S2：它需要的全部信息——UID↔businessKey、处置、观测基准、受控字段、Managed
Scope、表达式校验安排、删除候选集——都在包里的 ``metadata_manifest.json``（BP-CMP-027a）。
manifest 缺段就是**阻塞问题**而不是猜测（DX-ASM-02 的“不成立时无法漂移检查、Snapshot、
映射问题与验证”）。

载荷本身是 ``metadata.json``：一个对象类型 → 对象列表的单一导入包（FR-CMP-005），
VALIDATE 与 COMMIT 提交的都是它的原样字节（DX-BR-05）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_foundation.api import content_hash

from factory_dhis2.artifacts import read_archive, read_metadata
from factory_dhis2.errors import InputArtifactInvalidError

METADATA_FILE = "metadata.json"
MANIFEST_FILE = "metadata_manifest.json"
CHANGE_SUMMARY_FILE = "change-summary.json"
DATASTORE_FILE = "datastore-changes.json"
DEPENDENCY_GRAPH_FILE = "dependency-graph.json"

#: manifest 里 S4 必须用到的段（缺任何一段都是一个阻塞问题）
REQUIRED_MANIFEST_SECTIONS: tuple[str, ...] = (
    "objects",
    "uidToBusinessKey",
    "dispositions",
    "observationBaselines",
    "managedScope",
    "controlledFields",
    "expressionValidation",
    "deletionCandidates",
)


@dataclass
class MetadataPackage:
    """一个已解开的元数据包：载荷 + manifest + 原始字节。"""

    artifact_id: str
    artifact_hash: str
    payload: dict[str, Any]
    manifest: dict[str, Any]
    files: dict[str, bytes] = field(default_factory=dict, repr=False)
    missing_sections: list[str] = field(default_factory=list)

    @property
    def project_id(self) -> str | None:
        value = self.manifest.get("projectId")
        return value if isinstance(value, str) else None

    @property
    def blueprint_version(self) -> str | None:
        value = self.manifest.get("blueprintVersion")
        return value if isinstance(value, str) else None

    @property
    def environment_key(self) -> str | None:
        value = self.manifest.get("environmentKey")
        return value if isinstance(value, str) else None

    @property
    def object_count(self) -> int:
        return sum(len(items) for items in self.payload.values() if isinstance(items, list))

    # --- manifest 访问器 ---------------------------------------------------

    def uid_map(self) -> list[dict[str, Any]]:
        rows = self.manifest.get("uidToBusinessKey") or []
        return [row for row in rows if isinstance(row, dict)]

    def uid_to_business_key(self) -> dict[str, str]:
        """DX-EXE-027：ImportReport 里的 UID 要映射回 businessKey。"""
        out: dict[str, str] = {}
        for row in self.uid_map():
            uid = row.get("uid")
            key = row.get("businessKey")
            if isinstance(uid, str) and isinstance(key, str):
                out[uid] = key
        return out

    def dispositions(self) -> dict[str, str]:
        raw = self.manifest.get("dispositions") or {}
        return {str(key): str(value) for key, value in raw.items() if value}

    def objects(self) -> list[dict[str, Any]]:
        rows = self.manifest.get("objects") or []
        return [row for row in rows if isinstance(row, dict)]

    def observation_baselines(self) -> list[dict[str, Any]]:
        rows = self.manifest.get("observationBaselines") or []
        return [row for row in rows if isinstance(row, dict)]

    def managed_scope(self) -> list[dict[str, Any]]:
        rows = self.manifest.get("managedScope") or []
        return [row for row in rows if isinstance(row, dict)]

    def controlled_fields(self) -> dict[str, list[str]]:
        raw = self.manifest.get("controlledFields") or {}
        return {str(key): [str(item) for item in (value or [])] for key, value in raw.items()}

    def expression_validation(self) -> list[dict[str, Any]]:
        rows = self.manifest.get("expressionValidation") or []
        return [row for row in rows if isinstance(row, dict)]

    def deletion_candidates(self) -> list[dict[str, Any]]:
        rows = self.manifest.get("deletionCandidates") or []
        return [row for row in rows if isinstance(row, dict)]

    def change_summary(self) -> dict[str, Any]:
        raw = self.manifest.get("changeSummary") or {}
        return dict(raw) if isinstance(raw, dict) else {}

    def payload_by_uid(self) -> dict[str, dict[str, Any]]:
        """UID → 载荷对象，部署后验证与对账按 UID 找它。"""
        out: dict[str, dict[str, Any]] = {}
        for objects in self.payload.values():
            if not isinstance(objects, list):
                continue
            for item in objects:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    out[item["id"]] = item
        return out

    def type_by_uid(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for collection, objects in self.payload.items():
            if not isinstance(objects, list):
                continue
            for item in objects:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    out[item["id"]] = _collection_type(collection)
        return out


def read_package(artifact_id: str) -> MetadataPackage:
    """解开一个 METADATA_PACKAGE 产物（外部 I/O：读对象存储）。"""
    info = read_metadata(artifact_id)
    if info.type != "METADATA_PACKAGE":
        raise InputArtifactInvalidError(
            f"artifact {artifact_id} is a {info.type}, not the METADATA_PACKAGE S4 executes",
            artifact_id=artifact_id,
            details={"artifactId": artifact_id, "artifactType": info.type},
        )
    files = read_archive(artifact_id)
    payload = _json_of(files, METADATA_FILE, artifact_id)
    manifest = _json_of(files, MANIFEST_FILE, artifact_id)
    missing = [name for name in REQUIRED_MANIFEST_SECTIONS if name not in manifest]
    return MetadataPackage(
        artifact_id=artifact_id,
        artifact_hash=info.content_hash,
        payload=payload,
        manifest=manifest,
        files=files,
        missing_sections=missing,
    )


def require_complete(package: MetadataPackage, operation: str) -> None:
    """manifest 缺段 → 阻塞（DX-ASM-02）。"""
    if package.missing_sections:
        raise InputArtifactInvalidError(
            f"the package manifest is missing {package.missing_sections}; {operation} "
            "cannot proceed without them",
            artifact_id=package.artifact_id,
            details={
                "missing": package.missing_sections,
                "artifactId": package.artifact_id,
                "rule": "DX-ASM-02",
            },
        )


def _json_of(files: dict[str, bytes], name: str, artifact_id: str) -> dict[str, Any]:
    import json

    raw = files.get(name)
    if raw is None:
        raise InputArtifactInvalidError(
            f"package {artifact_id} has no {name}",
            artifact_id=artifact_id,
            details={"artifactId": artifact_id, "missingFile": name, "present": sorted(files)},
        )
    document: Any = json.loads(raw)
    if not isinstance(document, dict):
        raise InputArtifactInvalidError(
            f"{name} of package {artifact_id} is not a JSON object",
            artifact_id=artifact_id,
            details={"artifactId": artifact_id, "file": name},
        )
    return document


def _collection_type(collection: str) -> str:
    """``dataElements`` → ``dataElement``：DHIS2 的集合名就是类型名加复数。"""
    for suffix in ("ies", "ses", "s"):
        if collection.endswith(suffix) and len(collection) > len(suffix):
            head = collection[: -len(suffix)]
            return f"{head}y" if suffix == "ies" else head
    return collection


def package_bytes_hash(files: dict[str, bytes]) -> str:
    """包内文件按名排序拼接后的哈希（与 S2 的 package_hash 同一定义）。"""
    return content_hash(b"".join(files[name] for name in sorted(files)))


__all__ = [
    "CHANGE_SUMMARY_FILE",
    "DATASTORE_FILE",
    "DEPENDENCY_GRAPH_FILE",
    "MANIFEST_FILE",
    "METADATA_FILE",
    "REQUIRED_MANIFEST_SECTIONS",
    "MetadataPackage",
    "package_bytes_hash",
    "read_package",
    "require_complete",
]
