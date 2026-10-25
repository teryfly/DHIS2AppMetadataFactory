"""Helpers that build Blueprint documents for tests."""

from __future__ import annotations

from typing import Any

from factory_blueprint.content.initial import initial_document
from factory_blueprint.dto.blueprint import InitialProjectInfo
from factory_blueprint.enums import DeliveryMode, ProjectType, RegistryEntryStatus

BASE_HASH = "sha256:" + "0" * 64


def project_info(mode: DeliveryMode = DeliveryMode.B) -> InitialProjectInfo:
    return InitialProjectInfo(
        delivery_mode=mode,
        project_type=ProjectType.BROWNFIELD,
        target_dhis2_version_constraint=">=2.41 <2.43",
        default_language="en",
        translation_languages=["fr"],
        code_prefix="MC",
    )


def empty_document(mode: DeliveryMode = DeliveryMode.B) -> dict[str, Any]:
    return initial_document(project_info(mode))


def registry_entry(
    business_key: str,
    dhis2_type: str,
    disposition: str,
    *,
    uid: str | None = None,
    observed_uid: str | None = None,
    code: str | None = None,
    controlled_fields: list[str] | None = None,
    decision_id: str = "MAP-001",
    observed: bool = True,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "businessKey": business_key,
        "dhis2Type": dhis2_type,
        "disposition": disposition,
        "status": RegistryEntryStatus.APPROVED.value,
        "managed": disposition in {"CREATE", "UPDATE", "DEPRECATED"},
        "decisionId": decision_id,
        "controlledFields": controlled_fields or [],
        "source": {"decisionId": decision_id},
    }
    if uid:
        entry["uid"] = uid
    if observed_uid:
        entry["observedUid"] = observed_uid
    if code:
        entry["code"] = code
    if observed and disposition in {"REUSE", "REFERENCE", "UPDATE"}:
        entry["sourceEnvironment"] = "DEV"
        entry["observedLastUpdated"] = "2026-01-01T00:00:00Z"
        entry["observedHash"] = BASE_HASH
    return entry


def mapping_entry(
    business_key: str,
    dhis2_type: str,
    *,
    decision_id: str = "MAP-001",
    fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "businessKey": business_key,
        "dhis2Type": dhis2_type,
        "fields": fields or {"name": {"default": business_key.split(".")[-1]}},
        "decisionId": decision_id,
        "alternatives": [{"option": "DataSet", "reasonRejected": "no aggregated totals"}],
        "risks": [{"description": "program growth", "level": "LOW"}],
        "confidence": 0.9,
        "source": {"decisionId": decision_id},
    }


def domain_entity(business_key: str, *, name: str = "Pregnancy") -> dict[str, Any]:
    return {
        "businessKey": business_key,
        "displayName": {"default": name},
        "attributes": [
            {
                "name": "dueDate",
                "valueType": "DATE",
                "required": True,
                "sensitivity": "PHI_PII",
            }
        ],
        "lifecycle": {"states": [{"key": "active", "name": "Active", "initial": True}]},
        "source": {"artifactId": "art-01INPUT0001", "location": "chap 2, line 14"},
    }


def with_entries(document: dict[str, Any], container_path: str, *entries: dict[str, Any]) -> dict[str, Any]:
    """Return a document with these entries added to the given container."""
    import copy

    result = copy.deepcopy(document)
    node = result
    for token in container_path.split("/")[1:]:
        node = node[token]
    node.extend(copy.deepcopy(entries))
    return result


def seeded_document() -> dict[str, Any]:
    """A small but schema-complete Blueprint: one entity, mapping, registry entry."""
    document = empty_document()
    document = with_entries(document, "/domain/entities", domain_entity("pregnancy"))
    document = with_entries(
        document,
        "/mapping/entries",
        mapping_entry("pregnancy", "program", decision_id="MAP-001"),
    )
    document = with_entries(
        document,
        "/architecture/decisions",
        {
            "decisionId": "MAP-001",
            "title": "Pregnancy as a Tracker Program",
            "context": "antenatal care over months",
            "decision": "model pregnancy as a Tracker Program with enrollments",
            "status": "ACCEPTED",
        },
    )
    document = with_entries(
        document,
        "/registry/entries",
        registry_entry("pregnancy", "program", "CREATE", code="MC_PR_PREG", uid="aB3cD4eF5gH"),
    )
    return document
