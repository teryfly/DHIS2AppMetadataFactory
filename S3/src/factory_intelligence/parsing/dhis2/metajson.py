"""Existing-DHIS2 metadata parser: metadata export JSON and S4's discovery result.

AI-INP-002 lists "既有 DHIS2 元数据 — 元数据导出 JSON、发现结果" as a *deterministic*
input whose review obligation is 复核复用意图: what comes out of here describes objects that
already exist in a target environment, and the decision to reuse or replace one belongs to
the Mapping Agent and the human Gate (AI-AG-011), not to this parser.

Three rules shape the module:

* **A DHIS2 uid is never an identity.**  SRS 1.3 forbids S3 generating or consuming UIDs as
  identity, so :func:`_identity_of` derives the businessKey from the object's ``code``, else
  its ``displayName``/``name``, else its kind and JSON pointer — never the uid.  The uid is
  still worth keeping as *observation*, so it travels in ``payload["observedUid"]``, which is
  where AI-AG-011's similar-object search reads it from.  An expression that names objects by
  uid is paraphrased into the statement through the observed code or name, and kept verbatim
  in the payload.
* **Unknown kinds are asked, never guessed.**  A kind outside :data:`KIND_CATEGORY` becomes an
  ``OPEN_QUESTION`` entry, because AI-INP-005 requires the unexplained to be part of the NRR
  and a catch-all category would stop the thirteen from meaning anything.
* **Every fact carries its JSON pointer** (``Location.pointer("/dataElements/3")``), so an
  AI-INP-009 conflict between two inputs about one object can name both positions.

The input may be a metadata export (``{"dataElements": [ … ], "optionSets": [ … ]}``, with or
without a ``metaData`` envelope), a bare array of objects, or S4's discovery result
(``{"objects": [{"dhis2Type": …, "uid": …}]}``) — all three are walked the same way, with each
object's own ``kind``/``klass``/``objectType``/``dhis2Type`` winning over the name of the
collection it sat in.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Any

from factory_foundation.api import DataClassification

from factory_intelligence.enums import NrrStatus, ParserType, QuestionSeverity
from factory_intelligence.parsing.base import (
    Fragment,
    Location,
    NrrCategory,
    NrrEntry,
    ParseError,
    business_key,
    disambiguate,
)

#: Registered in ``factory_intelligence.registries.AI_ERROR_CODES`` (00 doc 6.5).
INPUT_UNREADABLE = "AI_PARSER_INPUT_INVALID"

#: How an object names its own type, in the order a DHIS2 export and S4 both write it.
KIND_KEYS: tuple[str, ...] = ("kind", "klass", "objectType", "dhis2Type", "className", "type")
#: How an object names its uid.  Observed data only, never identity.
UID_KEYS: tuple[str, ...] = ("id", "uid", "UUID")
#: How an object names itself for a human.
NAME_KEYS: tuple[str, ...] = ("displayName", "name", "shortName", "displayFieldName")
#: How an object names its business code — the identity this parser uses first.
CODE_KEYS: tuple[str, ...] = ("code", "shortCode", "externalCode")

#: The recognised kinds.  Deliberately short: a kind S3 has not thought about must not be
#: classified, and anything absent here arrives as an open question instead.
KIND_CATEGORY: Mapping[str, NrrCategory] = {
    "dataelement": NrrCategory.BUSINESS_ENTITY,
    "optionset": NrrCategory.DATA_REQUIREMENT,
    "user": NrrCategory.PARTICIPANT,
    "usergroup": NrrCategory.PARTICIPANT,
    "program": NrrCategory.ACCESS,
    "authority": NrrCategory.ACCESS,
    "programindicator": NrrCategory.BUSINESS_RULE,
    "dataelementoperand": NrrCategory.BUSINESS_RULE,
}

#: Collection key → kind, for the shapes a metadata export uses.  An unlisted key falls back
#: to its singular, so a new collection arrives as an open question rather than a guess.
COLLECTION_KINDS: Mapping[str, str] = {
    "dataElements": "dataElement",
    "optionSets": "optionSet",
    "options": "option",
    "users": "user",
    "userGroups": "userGroup",
    "programs": "program",
    "authorities": "authority",
    "programIndicators": "programIndicator",
    "dataElementOperands": "dataElementOperand",
    "objects": "object",
}

#: Scalar fields copied verbatim into ``payload["observed"]`` when the export carries them.
#: Names, codes and uids are handled apart; this is the rest of what was observed.
OBSERVED_FIELDS: Mapping[NrrCategory, tuple[str, ...]] = {
    NrrCategory.BUSINESS_ENTITY: (
        "valueType",
        "aggregationType",
        "domainType",
        "formName",
        "publicAccess",
    ),
    NrrCategory.DATA_REQUIREMENT: ("version", "publicAccess"),
    NrrCategory.PARTICIPANT: ("username", "synchronizedDate", "publicAccess"),
    NrrCategory.ACCESS: ("accessLevel", "programType", "publicAccess"),
    NrrCategory.BUSINESS_RULE: ("filter", "decimals", "shortName"),
    NrrCategory.OPEN_QUESTION: (),
}

#: A ``#{uid}`` / ``V{var}`` token inside a DHIS2 expression.
_EXPRESSION_TOKEN = re.compile(r"[#Vv]\{([^{}]*)\}")

#: Statement limit: S2 caps a statement at 4096 characters, so an option list quotes a
#: bounded number of names and then says how many more there are.
STATEMENT_OPTION_LIMIT = 12

_Item = tuple[str, str, dict[str, Any]]


class Dhis2MetadataParser:
    """Metadata export JSON / S4 discovery result → NRR fragment (AI-INP-002, AI-INP-005)."""

    parser_type: ParserType = ParserType.DHIS2_METADATA
    parser_version: str = "1.0.0"

    def parse(
        self,
        content: bytes,
        *,
        source_artifact_id: str,
        classification: DataClassification,
        options: Mapping[str, Any],
    ) -> Fragment:
        fragment = Fragment(
            parser_type=self.parser_type,
            parser_version=self.parser_version,
            source_artifact_id=source_artifact_id,
            classification=classification,
        )
        document = _load(content)
        fragment.notes.update(_record_notes(content, options))

        items, skipped = _walk(document)
        if not items:
            raise ParseError(
                INPUT_UNREADABLE,
                "the DHIS2 metadata input contains no objects, so nothing could be recorded",
            )
        fragment.notes["skippedNonObjectEntries"] = skipped
        observed_names = _observed_names(items)
        keys: list[str] = []
        unknown: dict[str, list[str]] = {}

        for pointer, fallback_kind, item in items:
            kind = _kind_of(item) or fallback_kind or "object"
            category = KIND_CATEGORY.get(_normalise(kind), NrrCategory.OPEN_QUESTION)
            uid = _first_of(item, UID_KEYS)
            name = _first_of(item, NAME_KEYS)
            code = _first_of(item, CODE_KEYS)
            location = Location.pointer(pointer)
            identity = _identity_of(kind=kind, code=code, name=name, pointer=pointer)
            payload: dict[str, Any] = {"kind": kind, "name": name, "code": code}
            if uid:
                #: an observation, never an identity: AI-AG-011 reads similar objects from here
                payload["observedUid"] = uid
            observed = _observed_of(item, category)
            if observed:
                payload["observed"] = observed
            if category is NrrCategory.DATA_REQUIREMENT:
                payload["options"] = _options_of(item)
            if category is NrrCategory.BUSINESS_ENTITY:
                #: a dataElement that names its option list by uid only is still an
                #: observation, and the observation is what AI-AG-011 searches on
                option_set = item.get("optionSet")
                if isinstance(option_set, str) and option_set.strip():
                    payload["observedOptionSetUid"] = option_set.strip()
            if category is NrrCategory.BUSINESS_RULE:
                _attach_expression(item, payload, observed_names)
            if category is NrrCategory.OPEN_QUESTION:
                unknown.setdefault(kind, []).append(pointer)
                payload["unrecognisedKind"] = kind

            entry = NrrEntry(
                category=category,
                business_key=disambiguate(
                    keys, business_key(category, identity), seed=str(location)
                ),
                statement=_statement(category, kind, identity, item, payload),
                location=location,
                confidence=1.0,
                status=NrrStatus.CONFIRMED,
                source_artifact_id=source_artifact_id,
                rationale=(
                    "existing target-environment content: reuse or replace is a Mapping "
                    "Agent decision behind a human Gate (AI-AG-011)"
                ),
                open_questions=(
                    (_object_question(kind, pointer),)
                    if category is NrrCategory.OPEN_QUESTION
                    else ()
                ),
                payload=payload,
            )
            keys.append(entry.business_key)
            fragment.entries.append(entry)

        fragment.notes["objectCount"] = len(items)
        fragment.notes["kindCounts"] = _kind_counts(items)
        #: AI-INP-002's 复核复用意图 applies to the whole row, not only to what the parser
        #: could not read: every object it recognised is a *candidate for reuse*, and an
        #: existing data element with the same name in the target environment is not
        #: automatically the one this project wants.  Reading a clean export as confirmed
        #: would let a Mapping Agent reuse an object nobody agreed to reuse.
        fragment.needs_review = True
        fragment.review_reason = (
            "existing DHIS2 metadata was read as reuse candidates: a human confirms which "
            "objects this project reuses and which it recreates"
            + (
                "; it also carries kinds this parser does not recognise: "
                + ", ".join(sorted(unknown))
                if unknown
                else ""
            )
            + " (AI-INP-002, AI-AG-011)"
        )
        if items:
            fragment.open_questions.append(_reuse_question(items))
        if unknown:
            fragment.notes["unrecognisedKinds"] = {
                kind: sorted(pointers) for kind, pointers in sorted(unknown.items())
            }
            for kind, pointers in sorted(unknown.items()):
                fragment.open_questions.append(_kind_question(kind, pointers))
        return fragment


def _load(content: bytes) -> Any:
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError(
            INPUT_UNREADABLE, f"the DHIS2 metadata input is not UTF-8 text: {exc}"
        ) from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ParseError(
            INPUT_UNREADABLE, f"the DHIS2 metadata input is not JSON: {exc}"
        ) from exc


def _record_notes(content: bytes, options: Mapping[str, Any]) -> dict[str, Any]:
    """The AI-INP-004 fields a parse record carries besides parserType/parserVersion."""
    notes: dict[str, Any] = {
        "inputHash": "sha256:" + hashlib.sha256(content).hexdigest(),
        "inputBytes": len(content),
        "usedModel": False,
        "reviewIntent": "reuse",
    }
    if options:
        notes["options"] = {
            str(key): _plain(value) for key, value in sorted(options.items(), key=str)
        }
    return notes


def _plain(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in sorted(value.items(), key=str)}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_plain(item) for item in value]
    return str(value)


def _walk(document: Any) -> tuple[list[_Item], int]:
    """Every object in the input, with the JSON pointer it was found at."""
    out: list[_Item] = []
    skipped = 0

    def visit(node: Any, pointer: str, kind: str) -> None:
        nonlocal skipped
        if isinstance(node, Mapping):
            if _is_object(node):
                out.append((pointer or "/", kind, dict(node)))
                return
            for key, value in node.items():
                child = f"{pointer}/{_escape(str(key))}"
                if isinstance(value, (str, bytes)) or not isinstance(value, (Mapping, Sequence)):
                    continue
                visit(value, child, COLLECTION_KINDS.get(str(key)) or _singular(str(key)))
            return
        if isinstance(node, Sequence):
            for index, entry in enumerate(node):
                if isinstance(entry, Mapping):
                    visit(entry, f"{pointer}/{index}", kind)
                else:
                    #: a list of bare strings (an authority list, a uid list) is not an
                    #: object; it is counted, never invented into a requirement
                    skipped += 1

    visit(document, "", "")
    return out, skipped


def _is_object(node: Mapping[str, Any]) -> bool:
    """A mapping is one object when it names its kind, or names itself by code/name/uid.

    An export envelope (``{"dataElements": […]}``) does neither, so it is walked instead of
    emitted.
    """
    if any(_text(node.get(key)) for key in KIND_KEYS):
        return True
    return any(_text(node.get(key)) for key in (*UID_KEYS, *NAME_KEYS, *CODE_KEYS))


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _singular(collection: str) -> str:
    if collection.endswith("ies"):
        return collection[:-3] + "y"
    if collection.endswith("s") and not collection.endswith("ss"):
        return collection[:-1]
    return collection


def _normalise(kind: str) -> str:
    text = unicodedata.normalize("NFKC", str(kind or ""))
    return re.sub(r"[^0-9a-z]", "", text.lower())


def _kind_of(item: Mapping[str, Any]) -> str | None:
    for key in KIND_KEYS:
        value = _text(item.get(key))
        if value:
            return value
    return None


def _first_of(item: Mapping[str, Any], keys: Sequence[str]) -> str | None:
    """The first of ``keys`` the object states, looking inside S4's ``content`` as well."""
    sources: list[Any] = [item, item.get("content")]
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        for key in keys:
            value = _text(source.get(key))
            if value:
                return value
    return None


def _text(value: Any) -> str:
    """The scalar reading of a JSON value: a string passes through, a container does not.

    ``str`` is a :class:`~collections.abc.Sequence`, so the container test has to see the
    string case first — otherwise every ``id`` and ``code`` in an export reads as empty and
    the parser reports an input that has no objects at all.
    """
    if isinstance(value, str):
        return value.strip()
    if value is None or isinstance(value, (Mapping, list, tuple, set)):
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _observed_of(item: Mapping[str, Any], category: NrrCategory) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in OBSERVED_FIELDS.get(category, ()):
        if key not in item:
            continue
        value = item[key]
        if isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
    return out


def _identity_of(*, kind: str, code: str | None, name: str | None, pointer: str) -> str:
    """Code, else display name, else kind and pointer — never the uid (SRS 1.3)."""
    if code:
        return code
    if name:
        return name
    return f"{kind} at {pointer}"


def _observed_names(items: Sequence[_Item]) -> dict[str, str]:
    """uid → code-or-name, so an expression can be read without presenting a uid."""
    out: dict[str, str] = {}
    for _pointer, _fallback, item in items:
        uid = _text(item.get("id")) or _text(item.get("uid"))
        if not uid:
            continue
        label = (
            _text(item.get("code"))
            or _text(item.get("displayName"))
            or _text(item.get("name"))
        )
        content = item.get("content")
        if isinstance(content, Mapping):
            label = label or _text(content.get("code")) or _text(content.get("name"))
        if label:
            out[uid] = label
    return out


def _attach_expression(
    item: Mapping[str, Any], payload: dict[str, Any], observed_names: Mapping[str, str]
) -> None:
    """Keep the expression verbatim and paraphrase it, as two separate payload keys.

    A programIndicator states ``expression``; a dataElementOperand does not, so its parts
    are composed into the same ``#{…}`` form and the payload says which happened.  Nothing
    here rewrites the observed text.
    """
    observed = _text(item.get("expression"))
    composed = "" if observed else _operand_expression(item, payload)
    payload["expression"] = observed or composed
    payload["expressionSource"] = (
        "observed" if observed else "composedFromFields" if composed else "absent"
    )
    payload["expressionParaphrase"] = (
        _paraphrase(payload["expression"], observed_names) if payload["expression"] else ""
    )
    if not observed and composed:
        #: the parts a composed expression was built from, so a reviewer can check it
        payload["expressionParts"] = {
            key: _plain(item[key])
            for key in ("dataElement", "attributeOptionCombo")
            if key in item and isinstance(item[key], (str, Mapping))
        }


def _operand_expression(item: Mapping[str, Any], payload: dict[str, Any]) -> str:
    """A dataElementOperand states no expression; its parts are the expression."""
    data_element = item.get("dataElement")
    combo = item.get("attributeOptionCombo")
    if data_element is None and combo is None:
        return ""
    left = _reference_label(data_element, payload, "observedDataElementUid")
    right = _reference_label(combo, payload, "observedAttributeOptionComboUid")
    if not left:
        return ""
    return f"#{{{left}}}" + (f".{{{right}}}" if right else "")


def _reference_label(value: Any, payload: dict[str, Any], uid_key: str) -> str:
    """A name for one side of an operand reference that never puts a uid in the statement.

    Where the input only identifies the object by uid, the observation is recorded in the
    payload under ``uid_key`` and the statement says so in words: SRS 1.3 forbids S3 using a
    uid as identity, and a statement is the one text a human reads.
    """
    if isinstance(value, Mapping):
        code = _text(value.get("code"))
        if code:
            payload[uid_key] = f"code:{code}"
            return code
        uid = _text(value.get("id")) or _text(value.get("uid"))
        if uid:
            payload[uid_key] = uid
            return uid_key
        return ""
    text = _text(value)
    if text:
        payload[uid_key] = text
        return uid_key
    return ""


def _paraphrase(expression: str, observed_names: Mapping[str, str]) -> str:
    """Replace ``#{uid}`` tokens with the object's code or name where the input says what it is."""

    def replace(match: re.Match[str]) -> str:
        token = match.group(1)
        labels = [observed_names.get(part.strip()) for part in token.split(".")]
        if any(label is None for label in labels) or not labels:
            return match.group(0)
        joined = ".".join(str(label) for label in labels)
        return match.group(0).replace(token, joined)

    return _EXPRESSION_TOKEN.sub(replace, expression)


def _statement(
    category: NrrCategory,
    kind: str,
    identity: str,
    item: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> str:
    quoted = f"'{identity}'"
    if category is NrrCategory.OPEN_QUESTION:
        return (
            f"The DHIS2 input contains an object of the unrecognised kind '{kind}' named "
            f"{quoted}; this parser will not guess which requirement it carries, so its "
            "reuse intent must be confirmed (AI-INP-002)."
        )
    if category is NrrCategory.BUSINESS_ENTITY:
        return (
            f"Business entity {quoted} already exists in the target environment as a DHIS2 "
            f"{kind}{_entity_details(item, payload)}."
        )
    if category is NrrCategory.DATA_REQUIREMENT:
        options = list(payload.get("options") or [])
        if not options:
            return f"Data requirement {quoted} is an existing DHIS2 {kind} with no options."
        listed = ", ".join(_option_text(option) for option in options[:STATEMENT_OPTION_LIMIT])
        more = (
            f", and {len(options) - STATEMENT_OPTION_LIMIT} more"
            if len(options) > STATEMENT_OPTION_LIMIT
            else ""
        )
        return (
            f"Data requirement {quoted} is an existing DHIS2 {kind} offering "
            f"{len(options)} options: {listed}{more}."
        )
    if category is NrrCategory.PARTICIPANT:
        return (
            f"Participant {quoted} already exists in the target environment as a DHIS2 {kind}, "
            "so the roles it holds are observed rather than invented."
        )
    if category is NrrCategory.ACCESS:
        access = _text(item.get("accessLevel"))
        return (
            f"Access requirement: the existing DHIS2 {kind} {quoted}"
            + (f" carries access level '{access}'" if access else "")
            + ", so its sharing and data-capture rules must be carried over."
        )
    paraphrase = str(payload.get("expressionParaphrase") or "")
    if not paraphrase:
        return (
            f"Business rule {quoted} is an existing DHIS2 {kind} whose expression could not be "
            "read from the input; the rule it applies must be confirmed."
        )
    kept = (
        "kept verbatim in the payload"
        if payload.get("expressionSource") == "observed"
        else "composed from its parts and kept in the payload"
    )
    return (
        f"Business rule {quoted} is an existing DHIS2 {kind} that computes "
        f"'{paraphrase}' ({kept})."
    )


def _entity_details(item: Mapping[str, Any], payload: Mapping[str, Any]) -> str:
    parts: list[str] = []
    value_type = _text(item.get("valueType"))
    if value_type:
        parts.append(f"value type '{value_type}'")
    option_set = item.get("optionSet")
    if isinstance(option_set, Mapping):
        label = _text(option_set.get("code")) or _text(option_set.get("name"))
        if label:
            parts.append(f"option list '{label}'")
    observed_uid = payload.get("observedOptionSetUid")
    if observed_uid:
        parts.append("referencing an option list by uid only (observed, not identity)")
    combo = item.get("categoryCombo")
    if isinstance(combo, Mapping):
        name = _text(combo.get("name")) or _text(combo.get("code"))
        if name:
            parts.append(f"category combination '{name}'")
    return ("; " + ", ".join(parts)) if parts else ""


def _options_of(item: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = item.get("options")
    options: list[dict[str, Any]] = []
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        return options
    for entry in raw:
        if isinstance(entry, Mapping):
            code = _text(entry.get("code"))
            name = _text(entry.get("name")) or _text(entry.get("displayName")) or code
            option: dict[str, Any] = {"code": code or None, "name": name or None}
            uid = _text(entry.get("id")) or _text(entry.get("uid"))
            if uid:
                option["observedUid"] = uid
            options.append(option)
    return options


def _option_text(option: Mapping[str, Any]) -> str:
    name = option.get("name") or option.get("code") or ""
    code = option.get("code")
    if code and code != name:
        return f"{name} ({code})"
    return str(name)


def _object_question(kind: str, pointer: str) -> dict[str, Any]:
    return {
        "question": (
            f"The DHIS2 input contains a '{kind}' object at {pointer} whose kind this parser "
            "does not recognise. Which requirement does it carry, and should it be reused?"
        ),
        "severity": QuestionSeverity.HIGH.value,
    }


def _reuse_question(items: Sequence[_Item]) -> dict[str, Any]:
    """One question for the whole export: which of these objects does this project reuse?

    MEDIUM rather than HIGH: leaving it unanswered does not corrupt the NRR — every entry is
    still a candidate — but a Mapping Agent must not reuse anything it says without this
    answer, and the workbench has to show the obligation somewhere (AI-INP-002).
    """
    counts = _kind_counts(items)
    listed = ", ".join(f"{count} {kind}" for kind, count in sorted(counts.items()))
    return {
        "question": (
            f"This input carries existing DHIS2 metadata ({listed}). For each kind, is the "
            "project reusing these objects as they are, treating them as the brownfield "
            "baseline a new design must match, or replacing them? The parser records every "
            "object as a reuse candidate; none of them is confirmed until someone says so "
            "(AI-INP-002, AI-AG-011)."
        ),
        "severity": QuestionSeverity.MEDIUM.value,
    }


def _kind_question(kind: str, pointers: Sequence[str]) -> dict[str, Any]:
    shown = ", ".join(sorted(pointers)[:5])
    more = f" and {len(pointers) - 5} more" if len(pointers) > 5 else ""
    return {
        "question": (
            f"{len(pointers)} object(s) of kind '{kind}' ({shown}{more}) are not recognised by "
            "this parser. What requirement does each carry, and is any of them reused?"
        ),
        "severity": QuestionSeverity.HIGH.value,
    }


def _kind_counts(items: Sequence[_Item]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for _pointer, fallback, item in items:
        kind = _kind_of(item) or fallback or "object"
        counts[kind] = counts.get(kind, 0) + 1
    return dict(sorted(counts.items()))


__all__ = [
    "Dhis2MetadataParser",
    "KIND_CATEGORY",
    "COLLECTION_KINDS",
    "KIND_KEYS",
    "UID_KEYS",
    "NAME_KEYS",
    "CODE_KEYS",
    "OBSERVED_FIELDS",
    "INPUT_UNREADABLE",
]
