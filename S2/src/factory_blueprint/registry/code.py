"""Stable ``code`` generation for managed objects (BP-REG-009, FR-REG-008).

The code is derived from data the Blueprint already contains (prefix, naming
convention, object type, businessKey, position), so recompiling never invents a new
one and cross-environment comparison stays possible.
"""

from __future__ import annotations

import re

PREFIX_TYPE_KEY = "PREFIX_TYPE_KEY"
PREFIX_TYPE_INDEX = "PREFIX_TYPE_INDEX"
TYPE_KEY = "TYPE_KEY"

#: short, readable stand-ins for DHIS2 type names inside a code
TYPE_ABBREVIATIONS: dict[str, str] = {
    "dataElement": "DE",
    "optionSet": "OS",
    "option": "OPT",
    "category": "CAT",
    "categoryOption": "CO",
    "categoryCombo": "CC",
    "trackedEntityType": "TE",
    "trackedEntityAttribute": "TEA",
    "program": "PR",
    "programStage": "PS",
    "programStageDataElement": "PSDE",
    "programTrackedEntityAttribute": "PTEA",
    "programSection": "PSEC",
    "programStageSection": "PSS",
    "programRule": "RULE",
    "programRuleVariable": "RV",
    "programRuleAction": "RA",
    "programIndicator": "PI",
    "validationRule": "VR",
    "relationshipType": "RT",
    "attribute": "ATTR",
    "userRole": "UR",
    "userGroup": "UG",
    "organisationUnit": "OU",
    "dataSet": "DS",
    "indicator": "IND",
}

MAX_KEY_SLUG_LENGTH = 48
_NON_ALNUM = re.compile(r"[^A-Z0-9]+")


def type_abbreviation(dhis2_type: str) -> str:
    if dhis2_type in TYPE_ABBREVIATIONS:
        return TYPE_ABBREVIATIONS[dhis2_type]
    cleaned = _NON_ALNUM.sub("", dhis2_type.upper())
    return cleaned[:4] or "OBJ"


def key_slug(business_key: str) -> str:
    return _NON_ALNUM.sub("_", business_key.upper()).strip("_")[:MAX_KEY_SLUG_LENGTH]


def derive_code(
    *,
    prefix: str,
    naming_convention: str,
    dhis2_type: str,
    business_key: str,
    position: int | None = None,
) -> str:
    """Build the object's code under the project's naming convention."""
    abbreviation = type_abbreviation(dhis2_type)
    slug = key_slug(business_key)
    if naming_convention == PREFIX_TYPE_INDEX:
        index = 1 if position is None else position + 1
        return f"{prefix}_{abbreviation}_{index:04d}"
    if naming_convention == TYPE_KEY:
        return f"{abbreviation}_{slug}"
    return f"{prefix}_{abbreviation}_{slug}"
