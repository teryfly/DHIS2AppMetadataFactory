"""The initial Blueprint document (version ``0.1.0``).

Every section is created as an explicit empty collection so later patches always
have a baseline to address and Diff never has to guess about a missing section.
Defaults follow S2 SRS BP-REG-008a (UID pool 200 / 50) and BP-SCH-003.
"""

from __future__ import annotations

from typing import Any

from factory_blueprint.dto.blueprint import InitialProjectInfo
from factory_blueprint.enums import Disposition, Section
from factory_blueprint.schema import store

DEFAULT_CODE_PREFIX = "GEN"
DEFAULT_NAMING_CONVENTION = "PREFIX_TYPE_KEY"
DEFAULT_UID_BATCH_SIZE = 200
DEFAULT_UID_LOW_WATER_MARK = 50
DEFAULT_DECISION_CONFIDENCE = 0.7
DEFAULT_EXTRACTION_CONFIDENCE = 0.6
DEFAULT_SHARING = {"accessLevel": "READ", "publicAccess": "none"}


def empty_sections() -> dict[str, Any]:
    return {
        Section.REQUIREMENTS.value: {"items": []},
        Section.DOMAIN.value: {"entities": [], "relationships": []},
        Section.USE_CASES.value: {"items": []},
        Section.ACTORS.value: {"items": []},
        Section.ORGANIZATION_MODEL.value: {"levels": [], "orgUnitRefs": []},
        Section.ACCESS_MODEL.value: {
            "roles": [],
            "userGroups": [],
            "sharingDefaults": dict(DEFAULT_SHARING),
            "sharingOverrides": [],
        },
        Section.ARCHITECTURE.value: {"decisions": []},
        Section.MAPPING.value: {"entries": []},
        Section.REGISTRY.value: {"entries": []},
        Section.PAGES.value: {"items": []},
        Section.API_INTENTS.value: {"items": []},
        Section.UI_INTENTS.value: {"items": []},
        Section.RULES.value: {
            "validationRules": [],
            "programRules": [],
            "programRuleVariables": [],
            "programIndicators": [],
            "others": [],
        },
        Section.FIXTURES.value: {"specifications": [], "demoUsers": []},
        Section.ENVIRONMENT_CONSTRAINTS.value: {"items": []},
    }


def generation_policy(project_info: InitialProjectInfo) -> dict[str, Any]:
    return {
        "codePrefix": project_info.code_prefix or DEFAULT_CODE_PREFIX,
        "namingConvention": project_info.naming_convention or DEFAULT_NAMING_CONVENTION,
        "uidPool": {
            "batchSize": project_info.uid_pool_batch_size or DEFAULT_UID_BATCH_SIZE,
            "lowWaterMark": (
                project_info.uid_pool_low_water_mark
                if project_info.uid_pool_low_water_mark is not None
                else DEFAULT_UID_LOW_WATER_MARK
            ),
        },
        "environmentBindingRules": {"defaultMatch": "CODE_THEN_UID", "byType": []},
        "deletionPolicy": "CANDIDATE_WITH_APPROVED_CR",
        "decisionConfidenceThreshold": (
            project_info.decision_confidence_threshold
            if project_info.decision_confidence_threshold is not None
            else DEFAULT_DECISION_CONFIDENCE
        ),
        "extractionConfidenceThreshold": (
            project_info.extraction_confidence_threshold
            if project_info.extraction_confidence_threshold is not None
            else DEFAULT_EXTRACTION_CONFIDENCE
        ),
    }


def project_section(project_info: InitialProjectInfo) -> dict[str, Any]:
    project: dict[str, Any] = {
        "deliveryMode": project_info.delivery_mode.value,
        "projectType": project_info.project_type.value,
        "targetDhis2": {"versionConstraint": project_info.target_dhis2_version_constraint},
        "defaultLanguage": project_info.default_language,
        "translationLanguages": sorted(set(project_info.translation_languages)),
    }
    if project_info.display_name:
        project["displayName"] = project_info.display_name
    if project_info.summary:
        project["summary"] = project_info.summary
    return project


def initial_document(
    project_info: InitialProjectInfo, schema_version: int | None = None
) -> dict[str, Any]:
    version = store.resolve_version(schema_version)
    document: dict[str, Any] = {"blueprintSchemaVersion": version}
    document[Section.PROJECT.value] = project_section(project_info)
    document[Section.GENERATION_POLICY.value] = generation_policy(project_info)
    document.update(empty_sections())
    return document


#: entries whose disposition must never be silently dropped by a compiler
KNOWN_DISPOSITIONS: frozenset[str] = frozenset(d.value for d in Disposition)
