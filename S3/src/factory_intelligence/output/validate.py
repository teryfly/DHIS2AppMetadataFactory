"""The output validation ladder (AI-RT-010 … AI-RT-013).

Four rungs, cheapest first, each producing the same :class:`ValidationReport` so the
corrective retry always sees one format:

===  ==============================================================================  ======
rung  check                                                                          stops
===  ==============================================================================  ======
0    envelope: the answer is one JSON object with AI-RT-010's top-level shape         yes
1    JSON Schema: :func:`~factory_intelligence.output.schema.envelope_schema`, then
     every patch entry against **S2's own** section item schema (AI-ASM-03)           yes
2    agent constraints: :mod:`factory_intelligence.agents.checks` (AI-RT-012)         yes
3    S2's ``submit_patch`` — in ``runtime/patching.py``, not here                     yes
===  ==============================================================================  ======

Rung 1 validates an entry against the section schema read from S2 rather than against a
copy, which is what makes AI-ASM-03 true: S3's verdict and S2's cannot drift apart, because
there is one schema and both read it.  A path outside the agent's declared sections fails
*before* its value is examined, so an agent that writes somewhere it has no business
writing gets that answer rather than a confusing property error.

Rungs 0-2 are here; rung 3 is a database write and belongs to the runtime's single commit
point (00 doc 6.2), so ``validate()`` returns a typed :class:`AgentOutput` for the caller to
turn into a patch instead of submitting one itself.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import jsonschema
from pydantic import ValidationError

from factory_intelligence.agents.checks.base import CheckContext
from factory_intelligence.agents.checks.rules import run_checks
from factory_intelligence.agents.spec import AgentSpec
from factory_intelligence.dto.agent import AgentOutput
from factory_intelligence.output.errors import (
    LADDER_CONSTRAINT,
    LADDER_ENVELOPE,
    LADDER_SCHEMA,
    Finding,
    ValidationReport,
)
from factory_intelligence.output.schema import (
    Container,
    classify_path,
    envelope_schema,
    is_within,
)

#: The keys AI-RT-010 names at the top level.  Checked before JSON Schema so a non-object
#: answer produces one clear finding instead of a schema dump.
_ENVELOPE_KEYS = ("summary", "decisions", "patch", "questions", "confidence")


def validate(
    raw: Any,
    *,
    spec: AgentSpec,
    context: CheckContext,
    max_operations: int = 200,
) -> tuple[AgentOutput | None, ValidationReport]:
    """Run rungs 0-2.  Returns the typed answer, or the findings that refused it."""
    report = ValidationReport()
    envelope = _rung_envelope(raw, report)
    if envelope is None:
        return None, report
    _rung_schema(envelope, spec=spec, context=context, report=report, max_operations=max_operations)
    if not report.ok:
        return None, report
    answer = _rung_typed(envelope, report)
    if answer is None or not report.ok:
        return None, report
    _rung_constraints(answer, spec=spec, context=context, report=report)
    if not report.ok:
        return None, report
    return answer, report


# --- rung 0 ---------------------------------------------------------------------


def _rung_envelope(raw: Any, report: ValidationReport) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        report.stopped_at = LADDER_ENVELOPE
        report.add(
            Finding(
                rule_id="AI-ENV-001",
                ladder=LADDER_ENVELOPE,
                message=(
                    "the answer must be one JSON object; got "
                    f"{type(raw).__name__}. Return only the object, with no prose around it."
                ),
            )
        )
        return None
    missing = [key for key in _ENVELOPE_KEYS if key not in raw and key != "patch"]
    if missing:
        report.stopped_at = LADDER_ENVELOPE
        for key in missing:
            report.add(
                Finding(
                    rule_id="AI-ENV-002",
                    ladder=LADDER_ENVELOPE,
                    message=f"the answer has no {key!r}; AI-RT-010 requires it at the top level",
                    location=key,
                )
            )
        return None
    # ``null`` is a valid answer for summary/confidence but not an absent one
    if not str(raw.get("summary") or "").strip():
        report.stopped_at = LADDER_ENVELOPE
        report.add(
            Finding(
                rule_id="AI-ENV-003",
                ladder=LADDER_ENVELOPE,
                message="summary must be a non-empty string: it is the only auditable "
                "decision record the run leaves behind (AI-RT-014)",
                location="summary",
            )
        )
        return None
    return dict(raw)


# --- rung 1 ---------------------------------------------------------------------


def _rung_schema(
    envelope: dict[str, Any],
    *,
    spec: AgentSpec,
    context: CheckContext,
    report: ValidationReport,
    max_operations: int,
) -> None:
    validator = jsonschema.Draft202012Validator(
        envelope_schema(max_operations=max_operations)
    )
    for error in sorted(validator.iter_errors(envelope), key=_error_sort):
        report.add(
            Finding(
                rule_id="AI-SCO-001",
                ladder=LADDER_SCHEMA,
                message=_error_text(error),
                location=_error_pointer(error),
            )
        )
    if report.findings:
        report.stopped_at = LADDER_SCHEMA
        return
    _check_patch_paths_and_entries(envelope, spec=spec, context=context, report=report)
    if report.findings:
        report.stopped_at = LADDER_SCHEMA


def _check_patch_paths_and_entries(
    envelope: dict[str, Any],
    *,
    spec: AgentSpec,
    context: CheckContext,
    report: ValidationReport,
) -> None:
    patch = envelope.get("patch")
    if not isinstance(patch, dict):
        return
    operations = patch.get("operations") or []
    allowed = set(spec.writable_section_values)
    base_version = str(patch.get("baseVersion") or "")
    if context.base_version and base_version and base_version != context.base_version:
        report.add(
            Finding(
                rule_id="AI-SCO-002",
                ladder=LADDER_SCHEMA,
                message=(
                    f"patch.baseVersion is {base_version!r} but this run was built against "
                    f"{context.base_version!r}; author the patch against the version in context"
                ),
                location="patch.baseVersion",
            )
        )
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            continue
        path = str(operation.get("path") or "")
        section, container_property, remainder = classify_path(path)
        if section not in allowed:
            report.add(
                Finding(
                    rule_id="AI-SCO-003",
                    ladder=LADDER_SCHEMA,
                    message=(
                        f"{path} is outside this agent's scope; it may write "
                        f"{', '.join(sorted(allowed)) or 'nothing'} (AI-RT-012)"
                    ),
                    location=f"patch.operations[{index}].path",
                )
            )
            continue
        containers = _containers_for(context, section or "")
        if container_property is None:
            # a write to the section as a whole; allowed only when the section has no
            # containers, i.e. a whole-object section such as generationPolicy
            if containers:
                report.add(
                    Finding(
                        rule_id="AI-SCO-004",
                        ladder=LADDER_SCHEMA,
                        message=(
                            f"{path} replaces the whole {section!r} section; address an entry "
                            f"inside one of {', '.join(sorted(c.property_name for c in containers))}"
                        ),
                        location=f"patch.operations[{index}].path",
                    )
                )
            continue
        container = next(
            (item for item in containers if item.property_name == container_property), None
        )
        if container is None:
            report.add(
                Finding(
                    rule_id="AI-SCO-005",
                    ladder=LADDER_SCHEMA,
                    message=(
                        f"{path} addresses {container_property!r}, which is not a container of "
                        f"the {section!r} section"
                    ),
                    location=f"patch.operations[{index}].path",
                )
            )
            continue
        _validate_entry_value(
            operation, container=container, index=index, report=report, remainder=remainder or ""
        )


def _validate_entry_value(
    operation: dict[str, Any],
    *,
    container: Container,
    index: int,
    report: ValidationReport,
    remainder: str,
) -> None:
    if operation.get("op") == "remove":
        return
    if "/" in remainder:
        # The path addresses a field *inside* an entry — ``/mapping/entries/x/confidence``,
        # or the confirmation record the runtime adds — so its value is not an entry and the
        # entry schema would reject a scalar, an array element or a partial object outright.
        # Which field exists, and what it holds, is S2's section schema's business (rung 3);
        # the section and container above were already checked, which is what AI-RT-012 bounds.
        return
    value = operation.get("value")
    if value is None:
        report.add(
            Finding(
                rule_id="AI-SCO-006",
                ladder=LADDER_SCHEMA,
                message=f"{operation.get('op')} of {operation.get('path')} has no value",
                location=f"patch.operations[{index}].value",
            )
        )
        return
    validator = jsonschema.Draft202012Validator(container.standalone_schema())
    for error in sorted(validator.iter_errors(value), key=_error_sort):
        report.add(
            Finding(
                rule_id="AI-SCO-007",
                ladder=LADDER_SCHEMA,
                message=(
                    f"{container.section}.{container.property_name} entry is not valid against "
                    f"S2's own section schema: {_error_text(error)}"
                ),
                location=f"patch.operations[{index}].value{_error_pointer(error)}",
            )
        )


def _containers_for(context: CheckContext, section: str) -> Sequence[Container]:
    return context.containers.get(section, ())


# --- rung 1.5: typed answer ------------------------------------------------------


def _rung_typed(envelope: dict[str, Any], report: ValidationReport) -> AgentOutput | None:
    """Bind the accepted answer to its model.

    A failure here after JSON Schema passed means the two descriptions of the envelope
    disagree, which is a defect in S3 rather than in the answer — so it is reported as an
    envelope finding, and the run gets its one corrective retry rather than crashing.
    """
    try:
        return AgentOutput.model_validate(envelope)
    except ValidationError as exc:  # pragma: no cover - guarded by a consistency test
        report.stopped_at = LADDER_ENVELOPE
        for error in exc.errors(include_url=False):
            report.add(
                Finding(
                    rule_id="AI-ENV-004",
                    ladder=LADDER_ENVELOPE,
                    message=str(error.get("msg")),
                    location=".".join(str(part) for part in error.get("loc", ())),
                )
            )
        return None


# --- rung 2 ---------------------------------------------------------------------


def _rung_constraints(
    answer: AgentOutput, *, spec: AgentSpec, context: CheckContext, report: ValidationReport
) -> None:
    findings = run_checks(answer, context, spec.checks)
    if findings:
        report.stopped_at = LADDER_CONSTRAINT
        report.extend(findings)


def _error_sort(error: jsonschema.ValidationError) -> tuple[str, str]:
    return (_error_pointer(error), str(error.message))


def _error_pointer(error: jsonschema.ValidationError) -> str:
    return "".join(f"/{part}" for part in error.absolute_path)


def _error_text(error: jsonschema.ValidationError) -> str:
    expected = error.validator_value
    if error.validator in ("enum",):
        return f"{_error_pointer(error)} must be one of {list(expected)}"
    if error.validator in ("additionalProperties",):
        return f"{_error_pointer(error)} carries properties the schema forbids: {error.message}"
    text = str(error.message)
    return text if len(text) <= 400 else text[:400] + "…"


def is_within_section(path: str, prefixes: Sequence[str]) -> bool:
    """Shared path test, exposed for the runtime's patch builder."""
    return any(is_within(prefix, path) for prefix in prefixes)


__all__ = ["validate", "is_within_section"]
