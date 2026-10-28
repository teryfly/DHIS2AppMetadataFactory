"""The constraint rules AI-RT-012 enforces after the Schema passes.

These exist because a prohibition written only in a prompt is a request.  SRS 11.1's
"禁止" column and AI-RT-012 require the runtime to refuse an answer that breaks one, and
every rule here is the machine-readable form of a row of the AI-AG table.

Three rules are shared by several agents and are the reason the checks read *structure*
rather than text — see ``base.py``'s docstring for why that distinction is load-bearing:

``no_final_products`` / ``no_dhis2_payload``
    AI-BR-01: an agent's output is always a candidate, never a deliverable.  A DHIS2
    metadata document (``@class`` / ``href`` / ``shortName`` triples), an OpenAPI document
    (``openapi`` / ``paths``) or a UI specification (``uiSpecVersion``) inside a patch value
    is a final product arriving through the wrong door.

``uid_ban``
    AI-AG-003 and AI-AC-02.  Only ``uid`` — S2 accepts ``observedUid`` deliberately for
    brownfield entries (``factory_blueprint/draft/service.py:546``), so rejecting anything
    UID-*shaped* would refuse every REUSE entry, and S3 would look like the obstacle.

``low_confidence_question``
    AI-AG-012: a decision below the threshold or of HIGH impact may not be self-approved;
    the answer must contain the question that puts it in front of a human.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from factory_intelligence.agents.checks.base import (
    CheckContext,
    entry_values,
    finding,
    removes,
    walk_documents,
    writes,
)
from factory_intelligence.dto.agent import AgentOutput
from factory_intelligence.output.errors import Finding

RULE_PREFIX = "AIC"

#: Markers that make a document a *final product* rather than a decision about one.
FINAL_PRODUCT_MARKERS: frozenset[str] = frozenset(
    {
        "@class",  # a DHIS2 metadata object
        "href",  # a DHIS2 REST link
        "shortName",  # DHIS2 metadata prose field, always alongside an id
        "openapi",  # an OpenAPI document
        "swagger",
        "paths",  # only meaningful in an API document
        "uiSpecVersion",  # a UI specification
        "components",
        "metadata",  # an import envelope
        "importStrategy",
    }
)
#: A DHIS2 metadata type vocabulary.  Curated from the DHIS2 metadata model rather than
#: imported from S2's descriptors, which are not part of ``factory_blueprint.api`` and so
#: are not S3's to read (D-13).  Used only against identity and structure fields.
DHIS2_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "dataelement",
        "dataelementgroup",
        "dataelementgroupset",
        "dataelementoperand",
        "categoryoption",
        "categoryoptioncombo",
        "categoryoptiongroup",
        "category",
        "categorycombo",
        "optionset",
        "option",
        "trackedentity",
        "trackedentityattribute",
        "program",
        "programstage",
        "programindicator",
        "programrule",
        "programrulevariable",
        "validationrule",
        "orgunit",
        "orgunitgroup",
        "orgunitgroupset",
        "orgunitlevel",
        "user",
        "usergroup",
        "userrole",
        "authority",
        "dashboard",
        "datastore",
        "predictor",
        "indicator",
        "legendset",
        "constant",
        "datastatistics",
    }
)
#: ``valueType`` is deliberately absent although DHIS2 spells its data types the same way:
#: S2's own domain schema *requires* it on every attribute, so banning the key would make a
#: DOMAIN answer unsatisfiable rather than unsafe — the check would refuse the only document
#: the stage's schema accepts, and every real run of that agent would die at rung 2.
#: A UID as DHIS2 issues one: 11 characters, letter-initial, and in practice mixed case or
#: with digits.  A lowercase eleven-letter word is not a UID, which is why the second
#: condition is there rather than the length test alone.
_UID_SHAPE = re.compile(r"^[A-Za-z][A-Za-z0-9]{10}$")
_RULE_EXPRESSION_FIELDS = frozenset(
    {"expression", "candidateExpressions", "programExpression", "indicatorExpression"}
)


def no_final_products(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-BR-01 / AI-AG-003 / AI-AG-006: no metadata JSON, no OpenAPI, no UI spec."""
    findings: list[Finding] = []
    for write in writes(output):
        for path, document in walk_documents(write.value, path=write.path):
            findings.extend(_markers_in(path, document))
    for decision in output.decisions:
        for path, document in walk_documents(decision, path="decision"):
            findings.extend(_markers_in(path, document))
    if findings:
        findings.insert(
            0,
            finding(
                f"{RULE_PREFIX}-001",
                "an agent produces candidates only: a final product appeared in the answer "
                "(SRS 1.3, AI-BR-01). Emit the decision and let the compiler build the document.",
            ),
        )
    return findings


def _markers_in(path: str, document: Mapping[str, Any]) -> list[Finding]:
    present = [marker for marker in FINAL_PRODUCT_MARKERS if marker in document]
    if not present:
        return []
    # ``components``/``paths``/``metadata`` are common words in ordinary answers, so a
    # document must carry two markers before it counts as a product.  One marker alone is
    # far more likely to be a field a human named than a smuggled payload.
    if len(present) < 2:
        return []
    return [
        finding(
            f"{RULE_PREFIX}-001",
            f"value at {path} looks like a final product; it carries {', '.join(sorted(present))}",
            location=path,
        )
    ]


def no_dhis2_payload(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """The same ban, worded for the agents whose output *is* about DHIS2 (AI-AG-003/006)."""
    findings: list[Finding] = []
    for write in writes(output):
        for path, document in walk_documents(write.value, path=write.path):
            if "@class" in document or ("href" in document and "id" in document):
                findings.append(
                    finding(
                        f"{RULE_PREFIX}-001",
                        f"{path} is a DHIS2 metadata object; S3 describes intent and S2's "
                        "compiler emits the metadata",
                        location=path,
                    )
                )
    return findings


def no_dhis2_reference(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-001, AI-AG-002: a requirements or domain answer must not mention DHIS2 shapes.

    Structural, never prose: ``statement`` and ``rationale`` are read by a human and are
    not inspected at all.  What is inspected is a *key* carrying a DHIS2 type name, a
    businessKey that is one, and any mapping-only field appearing under a requirements or
    domain path.
    """
    findings: list[Finding] = []
    for write in writes(output):
        for path, document in walk_documents(write.value, path=write.path):
            for key in document:
                normalised = str(key).lower().replace("_", "")
                if normalised in DHIS2_TYPE_NAMES:
                    findings.append(
                        finding(
                            f"{RULE_PREFIX}-002",
                            f"{path} introduces the DHIS2 object type {key!r}; the stage that "
                            "owns DHIS2 shapes is MAPPING",
                            location=path,
                        )
                    )
        for bad in ("dhis2Type", "disposition", "uid", "controlledFields", "optionSet"):
            findings.extend(_forbidden_key(output, write.path, bad))
    for write in writes(output):
        if write.entry_key and write.entry_key.lower() in DHIS2_TYPE_NAMES:
            findings.append(
                finding(
                    f"{RULE_PREFIX}-002",
                    f"businessKey {write.entry_key!r} is a DHIS2 object type name",
                    location=write.path,
                )
            )
    return findings


def _forbidden_key(output: AgentOutput, path: str, key: str) -> list[Finding]:
    out: list[Finding] = []
    for write in writes(output):
        if write.path != path:
            continue
        for where, document in walk_documents(write.value, path=write.path):
            if key in document:
                out.append(
                    finding(
                        f"{RULE_PREFIX}-002",
                        f"{where} carries the field {key!r}, which belongs to a later stage",
                        location=where,
                    )
                )
    return out


def uid_ban(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-003 / AI-AC-02: no operation may write a UID.

    ``observedUid`` is allowed on purpose: it records what discovery *saw* in an existing
    environment and is the basis of AI-AG-011's similar-object search, whereas ``uid`` is
    the identity S2 alone allocates (BP-REG-006).  S3 must not be able to produce one even
    by accident, and S2's ``BPV-PCH-004`` agrees — this rule simply refuses the same thing
    one stage earlier, where the message can name the field instead of a JSON pointer.
    """
    findings: list[Finding] = []
    for write in writes(output):
        for path, document in walk_documents(write.value, path=write.path):
            for key in document:
                name = str(key)
                if name == "uid":
                    findings.append(
                        finding(
                            f"{RULE_PREFIX}-003",
                            f"{path} writes 'uid'; UIDs are allocated by S2 and only by S2 "
                            "(AI 1.3, BP-REG-006)",
                            location=path,
                        )
                    )
                elif name == "uids":
                    findings.append(
                        finding(
                            f"{RULE_PREFIX}-003",
                            f"{path} writes 'uids'; supply businessKeys instead",
                            location=path,
                        )
                    )
    return findings


def decision_coverage(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-003: every element with more than one solution has a decision with an alternative."""
    findings: list[Finding] = []
    declared = set(output.decision_ids)
    for entry in entry_values(output, "mapping", "entries") + entry_values(
        output, "registry", "entries"
    ):
        key = entry.get("businessKey") or "?"
        if entry.get("notMapped") is True:
            # an explicitly unmapped element carries no decision, and requiring one would
            # push the model to invent a fake option to satisfy the check
            continue
        decision_id = entry.get("decisionId")
        if not decision_id:
            findings.append(
                finding(
                    f"{RULE_PREFIX}-004",
                    f"mapping entry {key!r} has no decisionId; every mapped element needs "
                    "the decision that chose its shape",
                    location=str(key),
                )
            )
            continue
        if decision_id not in declared:
            findings.append(
                finding(
                    f"{RULE_PREFIX}-004",
                    f"entry {key!r} cites decision {decision_id!r}, which the answer does not define",
                    location=str(decision_id),
                )
            )
            continue
    for decision in output.decisions:
        findings.extend(_decision_alternatives(decision))
    return findings


def decision_registered(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """FR-MAP-001, BP-VAL-010: a cited decision must exist where S2 looks for it.

    ``decision_coverage`` proves the *answer* defines the decision; this proves the
    *document* does.  ``/architecture/decisions`` is the only container whose entries index a
    ``decisionId`` (``factory_blueprint/patch/businesskey.py``'s reference rules), so an entry
    citing a decision this patch never writes there is refused by S2 as a dangling reference —
    and BP-DEC-006 would have no decision row to show a Gate either.  Checking it here turns
    that refusal into a corrective retry that names the entry and the id.
    """
    registered = set(context.existing_decision_ids)
    for entry in entry_values(output, "architecture", "decisions"):
        decision_id = entry.get("decisionId")
        if isinstance(decision_id, str) and decision_id:
            registered.add(decision_id)
    findings: list[Finding] = []
    for section in ("mapping", "registry"):
        for entry in entry_values(output, section, "entries"):
            if entry.get("notMapped") is True:
                continue
            decision_id = entry.get("decisionId")
            if not isinstance(decision_id, str) or not decision_id:
                # an absent citation is decision_coverage's finding, not a duplicate of it
                continue
            if decision_id in registered:
                continue
            key = entry.get("businessKey") or "?"
            findings.append(
                finding(
                    f"{RULE_PREFIX}-016",
                    f"{section} entry {key!r} cites decision {decision_id!r}, which no entry in "
                    "/architecture/decisions registers: write it with `add "
                    "/architecture/decisions/-` in this same patch, or cite a decision the "
                    "draft already has",
                    location=str(decision_id),
                )
            )
    return findings


def _decision_alternatives(decision: Mapping[str, Any]) -> list[Finding]:
    identifier = decision.get("id") or decision.get("decisionId") or "?"
    alternatives = decision.get("alternatives") or []
    confidence = decision.get("confidence")
    if len(alternatives) >= 1:
        return []
    if isinstance(confidence, (int, float)) and float(confidence) >= 1.0:
        # a forced choice genuinely has no alternative; demanding one would make the model
        # fabricate a rejected option to satisfy the checker
        return []
    return [
        finding(
            f"{RULE_PREFIX}-004",
            f"decision {identifier!r} lists no alternative and is not stated as certain; "
            "AI-AG-003 requires at least one for every multi-solution element",
            location=str(identifier),
        )
    ]


def low_confidence_question(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-012 / AI-BR-05: a weak or high-impact decision must go out as a Question."""
    threshold = context.decision_confidence_threshold
    referenced: set[str] = {
        str(target) for question in output.questions for target in question.affects
    }
    # A resumed turn answers the questions the previous turn asked.  Those answers are the
    # confirmation AI-AG-012 wanted, so the same decision must not be put to the human again.
    referenced.update(str(target) for target in context.answered_affects)
    findings: list[Finding] = []
    for decision in output.decisions:
        identifier = str(decision.get("id") or decision.get("decisionId") or "")
        if not identifier:
            continue
        confidence = decision.get("confidence")
        impact = str(decision.get("impactLevel") or "").upper()
        weak = isinstance(confidence, (int, float)) and float(confidence) < threshold
        if not (weak or impact == "HIGH"):
            continue
        subjects = {str(item) for item in _decision_subjects(decision)}
        if referenced & ({identifier} | subjects):
            continue
        reason = (
            "impact level HIGH" if impact == "HIGH" else f"confidence {confidence} < {threshold}"
        )
        findings.append(
            finding(
                f"{RULE_PREFIX}-005",
                f"decision {identifier} has {reason} and no question asks the user about it; "
                "an agent may not adopt such a decision itself (AI-AG-012)",
                location=identifier,
            )
        )
    return findings


def _decision_subjects(decision: Mapping[str, Any]) -> list[Any]:
    out: list[Any] = []
    for key in ("subject", "affectedArtifacts", "affects", "businessKeys"):
        value = decision.get(key)
        if isinstance(value, str):
            out.append(value)
        elif isinstance(value, Sequence):
            out.extend(item for item in value if isinstance(item, str))
    return out


def reuse_decision_per_similar_object(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-011: for each similar object found, the answer must decide reuse or new."""
    if not context.similar_object_keys:
        return []
    text = _decision_surface(output)
    findings: list[Finding] = []
    for key in context.similar_object_keys:
        if key not in text:
            findings.append(
                finding(
                    f"{RULE_PREFIX}-006",
                    f"the similar object {key!r} found in the design reference environment is "
                    "not addressed; SRS AI-AG-011 requires a REUSE or CREATE decision with a "
                    "reason for each one",
                    location=key,
                )
            )
    return findings


def _decision_surface(output: AgentOutput) -> str:
    """Where a reuse decision may legitimately appear: decisions and patch entry content."""
    parts: list[str] = []
    for decision in output.decisions:
        for _path, document in walk_documents(decision, path="d"):
            parts.extend(str(key) for key in document)
            parts.extend(str(value) for value in document.values() if isinstance(value, str))
    for write in writes(output):
        parts.append(write.path)
        for _path, document in walk_documents(write.value, path="v"):
            parts.extend(str(key) for key in document)
            parts.extend(str(value) for value in document.values() if isinstance(value, str))
    return "\n".join(parts)


def business_key_references_only(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-006: rule expressions reference objects by businessKey, never by UID.

    The compiler substitutes the environment's UID at build time (FR-CMP-010), so a UID in
    a draft expression would freeze one environment's identity into portable design.  A
    candidate is only refused when it also carries a digit or a mixed case, because an
    eleven-letter lowercase word is a word, not a UID.
    """
    findings: list[Finding] = []
    for write in writes(output):
        for path, document in walk_documents(write.value, path=write.path):
            for field, value in document.items():
                if field not in _RULE_EXPRESSION_FIELDS:
                    continue
                for token in _uid_shaped_tokens(value):
                    findings.append(
                        finding(
                            f"{RULE_PREFIX}-007",
                            f"{path}/{field} references {token!r}, which looks like a DHIS2 UID; "
                            "reference objects by businessKey (FR-CMP-010)",
                            location=f"{path}/{field}",
                        )
                    )
    return findings


def _uid_shaped_tokens(value: Any) -> list[str]:
    if isinstance(value, str):
        candidates: list[str] = re.findall(r"[A-Za-z][A-Za-z0-9]{10}", value)
    elif isinstance(value, Sequence):
        candidates = [item for child in value for item in _uid_shaped_tokens(child)]
    else:
        return []
    return [
        token
        for token in candidates
        if _UID_SHAPE.match(token) and (any(char.isdigit() for char in token) or any(c.isupper() for c in token[1:]))
    ]


def attribute_sensitivity(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """FR-DOM-001 / AI-AG-002: every domain attribute is labelled with its sensitivity.

    S2's schema requires it too; checking here turns a rejected patch into a corrective
    retry with a message that names the attribute, and AI-POL-001 cannot classify a
    document whose sensitive fields were never marked.
    """
    findings: list[Finding] = []
    for entry in entry_values(output, "domain", "entities"):
        key = entry.get("businessKey") or "?"
        for index, attribute in enumerate(entry.get("attributes") or []):
            if not isinstance(attribute, dict):
                continue
            if not attribute.get("sensitivity"):
                findings.append(
                    finding(
                        f"{RULE_PREFIX}-008",
                        f"attribute {attribute.get('name') or index} of entity {key!r} has no "
                        "sensitivity label (FR-DOM-001)",
                        location=f"domain.entities.{key}.attributes.{index}",
                    )
                )
    return findings


def nrr_confidence_parity(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-INP-008, made enforceable on a patch: no below-threshold item may be CONFIRMED.

    The same parity rule is applied to parser output in ``parsing/confidence.py``; a
    Requirements Agent consolidating NRR fragments into the draft can reintroduce the
    violation, so the check runs on both paths.
    """
    findings: list[Finding] = []
    for entry in entry_values(output, "requirements", "items"):
        key = entry.get("businessKey") or "?"
        status = entry.get("status")
        confidence = entry.get("confidence")
        if (
            status == "CONFIRMED"
            and isinstance(confidence, (int, float))
            and float(confidence) < context.decision_confidence_threshold
        ):
            findings.append(
                finding(
                    f"{RULE_PREFIX}-009",
                    f"requirement {key!r} is CONFIRMED at confidence {confidence}, below "
                    f"the {context.decision_confidence_threshold} threshold; low-confidence "
                    "items stay CANDIDATE and become questions (AI-INP-008)",
                    location=key,
                )
            )
        if status == "REJECTED":
            findings.append(
                finding(
                    f"{RULE_PREFIX}-009",
                    f"requirement {key!r} is REJECTED; that verdict is a human's, recorded at "
                    "the Gate — an agent may propose CANDIDATE or CONFIRMED only",
                    location=key,
                )
            )
    return findings


def no_phi_fixture(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-007 / AI-POL-010: a fixture spec may not cite a PHI_PII source."""
    findings: list[Finding] = []
    if not context.phi_refs:
        return findings
    for write in writes(output):
        for path, document in walk_documents(write.value, path=write.path):
            for key, value in document.items():
                if str(key).lower() not in {"sourceartifactid", "artifactid", "source", "basis"}:
                    continue
                if isinstance(value, str) and value in context.phi_refs:
                    findings.append(
                        finding(
                            f"{RULE_PREFIX}-010",
                            f"{path}/{key} cites {value}, which is classified PHI_PII; real "
                            "patient data may not be a fixture basis under any policy",
                            location=f"{path}/{key}",
                        )
                    )
    return findings


def synthetic_only(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-007: fixture specifications describe a distribution, never captured people."""
    findings: list[Finding] = []
    for write in writes(output):
        for path, document in walk_documents(write.value, path=write.path):
            for key, value in document.items():
                if (
                    str(key).lower() in {"values", "records", "rows"}
                    and isinstance(value, list)
                    and len(value) > 10
                ):
                    findings.append(
                        finding(
                            f"{RULE_PREFIX}-011",
                            f"{path}/{key} carries {len(value)} literal values; a fixture "
                            "spec states a distribution and a count, not data rows",
                            location=f"{path}/{key}",
                        )
                    )
    return findings


def single_page_scope(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-004 / FR-API-004 / AI-AG-013: one run designs exactly one page."""
    pages: set[str] = set()
    for write in writes(output):
        for _path, document in walk_documents(write.value, path=write.path):
            for key in ("pageKey", "page"):
                value = document.get(key)
                if isinstance(value, str) and value:
                    pages.add(value)
    for path in [write.path for write in writes(output)] + [path for _i, path in removes(output)]:
        pages.update(part for part in path.split("/") if part.startswith("page:"))
    if len(pages) > 1:
        return [
            finding(
                f"{RULE_PREFIX}-012",
                f"the answer spans {len(pages)} pages ({', '.join(sorted(pages))}); a page "
                "agent runs once per page and may only touch the one in scope (FR-API-004)",
            )
        ]
    return []


def no_registry_write(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-004: an API agent may not modify the mapping or the Registry."""
    forbidden = ("/registry", "/mapping", "/architecture")
    offenders = [write.path for write in writes(output) if write.path.startswith(forbidden)]
    offenders += [path for _index, path in removes(output) if path.startswith(forbidden)]
    return [
        finding(
            f"{RULE_PREFIX}-013",
            f"{path} edits a frozen design fact; the API agent's job is the page, not the mapping",
            location=path,
        )
        for path in offenders
    ]


def logical_resource_names(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-004: a resource is named by logic, not by an environment UID."""
    findings: list[Finding] = []
    for write in writes(output):
        for path, document in walk_documents(write.value, path=write.path):
            for key in ("resource", "resourceName"):
                value = document.get(key)
                if isinstance(value, str) and _uid_shaped_tokens(value):
                    findings.append(
                        finding(
                            f"{RULE_PREFIX}-014",
                            f"{path}/{key}={value!r} embeds a DHIS2 UID; name the resource "
                            "logically and let the resolver bind the UID per environment",
                            location=f"{path}/{key}",
                        )
                    )
    return findings


def ui_data_source_resolves(output: AgentOutput, context: CheckContext) -> list[Finding]:
    """AI-AG-005 / FR-UII-002: every component's data source is an API operation or Registry entry."""
    known = {str(item) for item in context.api_operation_keys}
    findings: list[Finding] = []
    for write in writes(output):
        for path, document in walk_documents(write.value, path=write.path):
            for key in ("dataSource", "source", "operationKey"):
                value = document.get(key)
                if not isinstance(value, str) or not value:
                    continue
                if key == "source" and not value.startswith(("operation:", "registry:")):
                    continue
                if value in known or value.startswith("registry:"):
                    continue
                findings.append(
                    finding(
                        f"{RULE_PREFIX}-015",
                        f"{path}/{key}={value!r} is not an operationKey of this page's API "
                        "intent nor a Registry entry (FR-UII-002)",
                        location=f"{path}/{key}",
                    )
                )
    return findings


#: Every rule by name, as referenced by :attr:`AgentSpec.checks`.
RULES: dict[str, Any] = {
    "no_final_products": no_final_products,
    "no_dhis2_payload": no_dhis2_payload,
    "no_dhis2_reference": no_dhis2_reference,
    "uid_ban": uid_ban,
    "decision_coverage": decision_coverage,
    "decision_registered": decision_registered,
    "low_confidence_question": low_confidence_question,
    "reuse_decision_per_similar_object": reuse_decision_per_similar_object,
    "business_key_references_only": business_key_references_only,
    "attribute_sensitivity": attribute_sensitivity,
    "nrr_confidence_parity": nrr_confidence_parity,
    "no_phi_fixture": no_phi_fixture,
    "synthetic_only": synthetic_only,
    "single_page_scope": single_page_scope,
    "no_registry_write": no_registry_write,
    "logical_resource_names": logical_resource_names,
    "ui_data_source_resolves": ui_data_source_resolves,
}


def run_checks(output: AgentOutput, context: CheckContext, names: Sequence[str]) -> list[Finding]:
    """Run the named rules in the given order, collecting every finding.

    All rules run even after one fails: a model that gets one corrective retry (AI-RT-011)
    should see the complete list, the same reason S2 reports every failed rule in one round
    trip (``factory_blueprint/patch/validate.py``'s module docstring).
    """
    findings: list[Finding] = []
    for name in names:
        rule = RULES.get(name)
        if rule is None:  # pragma: no cover - guarded by the spec-integrity test
            findings.append(
                finding(
                    f"{RULE_PREFIX}-999",
                    f"unknown constraint rule {name!r} referenced by agent {context.agent_type}",
                )
            )
            continue
        findings.extend(rule(output, context))
    return findings


__all__ = ["RULES", "run_checks", "DHIS2_TYPE_NAMES", "FINAL_PRODUCT_MARKERS", "RULE_PREFIX"]
