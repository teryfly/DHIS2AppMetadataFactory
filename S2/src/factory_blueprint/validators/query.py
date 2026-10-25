"""The cross-report issue read (BP-VAL-005, BP-AC-12).

S1 names the reports — its own artifact index knows which ``VALIDATION_REPORT`` and
``DHIS2_IMPORT_REPORT`` a step produced — and S2 answers for all of them at once,
filtered, sorted and paged here.  Doing it in the client would mean loading thousands of
findings to show fifty (CP-UI-004, UI-REV-005).

Two document shapes have to be read, because the two producers wrote different things:

* S2's own report carries ``issues`` already in the BP-VAL-001 structure;
* S4 saves a ``DHIS2_IMPORT_REPORT`` as the instance's **raw** ImportReport, because the
  unaltered evidence is what a human re-checks an import against (FR-SEC-014).  S4's
  normalized view of it goes into its own execution report, not this artifact, so reading
  a DHIS2_IMPORT_REPORT means flattening the instance's shape here.

The rule IDs for those rows use S4's ``DXIS-<ERROR CLASS>`` vocabulary on purpose: one
``ruleId`` filter has to mean the same thing in both kinds of report, or a reviewer
sorting by rule silently sees only half the findings.
"""

from __future__ import annotations

from typing import Any

from factory_foundation.api import external_io, unit_of_work
from sqlalchemy import select

from factory_blueprint import artifacts
from factory_blueprint.dto.issue import Issue, derive_issue_id, sort_issues
from factory_blueprint.enums import DesignStage, IssueLevel, Section
from factory_blueprint.models.registry import RegistryEntryRow

#: the instance's own status values: accepted, and "reported but not applied"
IGNORED_STATUSES = frozenset({"IGNORED"})
ACCEPTED_STATUSES = frozenset({"OK", "SUCCESS"})

#: where each DHIS2 keeps its per-object rows — versions spell this differently
_ROW_CONTAINERS = ("objects", "detailedMessages", "messages", "imports")

#: errCode/message vocabulary → error class, mirroring S4's normalization (DX-EXE-027)
ERROR_CLASS_HINTS: tuple[tuple[str, str], ...] = (
    ("reference", "REFERENCE_ERROR"),
    ("not found", "REFERENCE_ERROR"),
    ("cannot find", "REFERENCE_ERROR"),
    ("property", "SCHEMA_ERROR"),
    ("schema", "SCHEMA_ERROR"),
    ("required", "SCHEMA_ERROR"),
    ("invalid value", "FIELD_ERROR"),
    ("type", "TYPE_ERROR"),
    ("uid", "IDENTIFIER_ERROR"),
    ("identifier", "IDENTIFIER_ERROR"),
    ("page", "PAGE_ERROR"),
    ("design", "PAGE_ERROR"),
)

DEFAULT_IMPORT_STAGE = DesignStage.MAPPING

#: ``sort_by`` spellings S1 forwards; ``objectKey`` is S4's name for the same identity
_SORT_KEYS = {
    "level": lambda issue: issue.level.value,
    "ruleId": lambda issue: issue.rule_id,
    "sourceStage": lambda issue: issue.source_stage.value if issue.source_stage else "",
    "businessKey": lambda issue: issue.business_key or "",
    "objectKey": lambda issue: issue.business_key or issue.uid or "",
}


@external_io
def query_issues(
    report_artifact_ids: list[str],
    *,
    level: str | None = None,
    source_stage: str | None = None,
    rule_id: str | None = None,
    business_key: str | None = None,
    sort_by: str = "level",
    sort_dir: str = "ASC",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """Every finding in these reports, in one structure, filtered and paged (BP-VAL-005)."""
    rows = _load(sorted(set(report_artifact_ids)))
    matched = [
        issue
        for issue in rows
        if (level is None or issue.level.value == level.upper())
        and (source_stage is None or _stage_of(issue) == source_stage.upper())
        and (rule_id is None or issue.rule_id == rule_id)
        and (business_key is None or issue.business_key == business_key)
    ]
    ordered = _sorted(matched, sort_by=sort_by, sort_dir=sort_dir)
    return {
        # S2 owns the Issue structure (00 doc 6.10), so it also owns the spelling on the
        # way out: camelCase, with the level field named ``level`` in both report kinds.
        "items": [_as_item(issue) for issue in ordered[offset : offset + limit]],
        "total": len(ordered),
        "limit": limit,
        "offset": offset,
    }


def _load(report_ids: list[str]) -> list[Issue]:
    issues: list[Issue] = []
    for report_id in report_ids:
        document, _ = artifacts.read_json(report_id)
        issues.extend(_from_document(document, report_id))
    return sort_issues(issues)


def _from_document(document: dict[str, Any], report_id: str) -> list[Issue]:
    listed = document.get("issues")
    if isinstance(listed, list):
        return [
            _from_issue_document(entry)
            for entry in listed
            if isinstance(entry, dict) and entry.get("level")
        ]
    if str(document.get("kind") or "") == "DHIS2_IMPORT_REPORT":
        return _from_raw_import_report(document, report_id)
    return []


def _from_issue_document(entry: dict[str, Any]) -> Issue:
    """Read one already-normalized issue, tolerating S4's snake_case field names."""
    payload = {
        _camel(key): value for key, value in entry.items() if not key.startswith("_")
    }
    business_key = payload.get("businessKey") or payload.get("objectKey")
    uid = payload.get("uid") or payload.get("objectUid")
    message = str(payload.get("message") or payload.get("description") or "")
    rule_id = str(payload.get("ruleId") or "")
    stage = payload.get("sourceStage")
    return Issue(
        issue_id=str(payload.get("issueId") or "")
        or derive_issue_id(rule_id, business_key, uid, payload.get("location"), message),
        rule_id=rule_id,
        level=IssueLevel(str(payload["level"]).upper()),
        message=message,
        business_key=business_key,
        uid=uid,
        dhis2_type=payload.get("dhis2Type"),
        artifact_id=payload.get("artifactId"),
        location=payload.get("location"),
        section=_section(payload.get("section")),
        source_stage=DesignStage(str(stage).upper()) if stage else None,
        remediation=payload.get("remediation") or payload.get("suggestedAction"),
        details=dict(payload.get("details") or {}),
    )


def _from_raw_import_report(document: dict[str, Any], report_id: str) -> list[Issue]:
    inner = document.get("report")
    response: dict[str, Any] = inner if isinstance(inner, dict) else document
    rows = _instance_rows(response)
    if not rows:
        return []
    owners = _owners(sorted({uid for uid, _, _ in rows if uid}))
    issues: list[Issue] = []
    for uid, status, row in rows:
        error_class = _error_class(row)
        message = _message(row) or f"{row.get('klass') or row.get('type') or 'object'} was rejected"
        owner = owners.get(uid or "")
        business_key, dhis2_type = owner if owner else (None, _type_of(row))
        issues.append(
            Issue(
                issue_id=derive_issue_id(
                    f"DXIS-{error_class}", business_key, uid, None, message
                ),
                rule_id=f"DXIS-{error_class}",
                level=IssueLevel.WARNING if status in IGNORED_STATUSES else IssueLevel.BLOCKING,
                message=message,
                business_key=business_key,
                uid=uid,
                dhis2_type=dhis2_type,
                artifact_id=report_id,
                section=Section.REGISTRY if business_key else None,
                source_stage=DEFAULT_IMPORT_STAGE,
                remediation=_remediation(error_class),
                details={
                    key: row[key]
                    for key in ("status", "klass", "errCode", "errorCode", "params")
                    if row.get(key) is not None
                },
            )
        )
    return issues


def _instance_rows(response: dict[str, Any]) -> list[tuple[str | None, str, dict[str, Any]]]:
    """Every per-object row the instance reported, from whichever container it used."""
    rows: list[dict[str, Any]] = []
    for key in _ROW_CONTAINERS:
        value = response.get(key)
        if isinstance(value, list):
            rows.extend(item for item in value if isinstance(item, dict))
        elif isinstance(value, dict):
            for group in value.values():
                if isinstance(group, list):
                    rows.extend(item for item in group if isinstance(item, dict))
    for report in response.get("typeReports") or []:
        if not isinstance(report, dict):
            continue
        for obj in report.get("objectReports") or []:
            if isinstance(obj, dict):
                rows.append(_flatten_type_report_row(report, obj))
    out: list[tuple[str | None, str, dict[str, Any]]] = []
    for row in rows:
        status = str(row.get("status") or "").upper()
        if not status or status in ACCEPTED_STATUSES:
            continue
        # 2.41 puts no status on an objectReport at all: having errorReports is the rejection
        uid = row.get("uid") or row.get("mainId")
        out.append((str(uid) if uid else None, status, row))
    return out


def _flatten_type_report_row(report: dict[str, Any], obj: dict[str, Any]) -> dict[str, Any]:
    errors = [item for item in (obj.get("errorReports") or []) if isinstance(item, dict)]
    if not errors and not obj.get("status"):
        return {}
    messages = "; ".join(str(item.get("message")) for item in errors if item.get("message"))
    codes = [str(item["errorCode"]) for item in errors if item.get("errorCode")]
    return {
        "status": obj.get("status") or ("ERROR" if errors else None),
        "klass": obj.get("klass") or report.get("klass"),
        "uid": obj.get("uid") or obj.get("mainId"),
        "message": messages,
        "errCode": codes[0] if codes else None,
    }


def _owners(uids: list[str]) -> dict[str, tuple[str, str]]:
    """UID → (businessKey, dhis2Type) from S2's own Registry index — the ownership S2 keeps.

    The type comes from the index rather than from the instance's ``klass``, because
    ``klass`` is a Java class name whose package and spelling change across versions; the
    index is what S2 asserted when it allocated the number.  No version is named:
    BP-REG-005 makes one UID mean exactly one business concept, so any index row for it
    answers the question.
    """
    if not uids:
        return {}
    with unit_of_work() as session:
        rows = session.execute(
            select(
                RegistryEntryRow.uid,
                RegistryEntryRow.business_key,
                RegistryEntryRow.dhis2_type,
            ).where(RegistryEntryRow.uid.in_(uids))
        ).all()
    found: dict[str, tuple[str, str]] = {}
    for uid, business_key, dhis2_type in rows:
        found.setdefault(str(uid), (str(business_key), str(dhis2_type)))
    return found


def _error_class(row: dict[str, Any]) -> str:
    haystack = " ".join(
        str(row.get(key) or "") for key in ("errCode", "errorCode", "message", "klass")
    ).lower()
    for needle, error_class in ERROR_CLASS_HINTS:
        if needle in haystack:
            return error_class
    return "UNKNOWN"


def _message(row: dict[str, Any]) -> str:
    return str(row.get("message") or row.get("description") or "")


def _type_of(row: dict[str, Any]) -> str | None:
    value = row.get("dhis2Type") or row.get("type")
    if value:
        return str(value)
    klass = row.get("klass")
    # ``org.hisp.dhis.dataelement.DataElement``: the last segment, never the package,
    # because both change across versions (S4 spells out the same reason)
    return str(klass).rsplit(".", 1)[-1].lower() if klass else None


def _remediation(error_class: str) -> str:
    if error_class in {"REFERENCE_ERROR", "IDENTIFIER_ERROR"}:
        return "check the environment binding and the discovery result for this object"
    if error_class in {"SCHEMA_ERROR", "FIELD_ERROR", "TYPE_ERROR"}:
        return "compare the object against the Capability Profile's schema for this version"
    return "read the instance's report entry beside this finding"


def _sorted(issues: list[Issue], *, sort_by: str, sort_dir: str) -> list[Issue]:
    key = _SORT_KEYS.get(sort_by)
    if key is None:
        return sort_issues(issues)
    return sorted(issues, key=key, reverse=sort_dir.upper() == "DESC")


def _stage_of(issue: Issue) -> str:
    return issue.source_stage.value if issue.source_stage else ""


def _section(value: Any) -> Section | None:
    try:
        return Section(str(value)) if value else None
    except ValueError:
        return None


def _camel(key: str) -> str:
    parts = key.lstrip("_").split("_")
    return parts[0] + "".join(part.title() for part in parts[1:])


def _as_item(issue: Issue) -> dict[str, Any]:
    reduced = issue.model_dump(mode="json")
    return {
        "issueId": reduced["issue_id"],
        "ruleId": reduced["rule_id"],
        "level": reduced["level"],
        "message": reduced["message"],
        "businessKey": reduced["business_key"],
        "uid": reduced["uid"],
        "dhis2Type": reduced["dhis2_type"],
        "artifactId": reduced["artifact_id"],
        "location": reduced["location"],
        "section": reduced["section"],
        "sourceStage": reduced["source_stage"],
        "remediation": reduced["remediation"],
        "details": reduced["details"],
    }
