"""What a provider mirror must keep, and what it must match.

Two rules, each here because the suite broke on its absence:

* **One name per concept.**  S2's BP-VAL-001 and S4's DX-EXE-027 name an issue's level
  ``level``; S3 still says ``severity`` (S5IF-BP14 / S5IF-UP11).  S2 also calls the finding's
  text ``message`` where S3/S4 say ``description``.  00 doc 6.10 forbids the two names
  coexisting, because "which one wins" would otherwise only surface at run time — and a
  BLOCKING read as the default WARNING makes a failed deployment look like a passing one,
  while a dropped text reaches S5 as a finding nobody can act on.
* **One type per field.**  CP-RT-005 requires the assembly to run provider-API compatibility
  tests.  Comparing function signatures is not enough: a mirror declaring
  ``used_model: str`` accepts every fake yet rejects every real ``ParsedInput``, because S3
  answers a bool (AI-INP-004 says "是否使用模型").
"""

from __future__ import annotations

import enum
import importlib
import importlib.util
import pkgutil
import typing
from datetime import datetime
from typing import Any

import pytest
from pydantic import BaseModel

from factory_control.ports import dto
from factory_control.ports.dto import Issue, QuestionDraft

pytestmark = pytest.mark.unit

#: Provider packages whose public models S1 mirrors.
PROVIDER_PACKAGES = ("factory_blueprint", "factory_intelligence", "factory_dhis2")

#: The comparisons below are only meaningful against the real packages; on a venv that has
#: not installed them (see ``scripts/install_dev.sh``) the tests skip rather than pass empty.
ALL_PROVIDERS = all(importlib.util.find_spec(package) for package in PROVIDER_PACKAGES)

#: Mirrors whose provider twin has a different name, as mirror -> "module:Class".
RENAMED_TWINS: dict[str, str] = {
    "RegistryEntryInfo": "factory_blueprint.dto.registry:RegistryEntry",
    "VersionDetail": "factory_blueprint.dto.blueprint:VersionInfo",
    "PatchResult": "factory_blueprint.dto.patch:PatchOutcome",
}

#: Mirrors with no twin to compare against: shapes S1 invented (``BoundRef``,
#: ``ImpactAnalysis``, ``ExportBundle``) or ones whose provider model has not been located
#: yet.  Listed rather than silently skipped, so a new mirror cannot join it unnoticed.
#: The graph and dev-package mirrors (CP-UI-020/021) are not listed: S2 shipped those
#: DTOs, so the field-by-field comparison covers them even while the read functions
#: themselves are still transitional.
UNCOMPARED_MIRRORS: frozenset[str] = frozenset({
    "BlueprintDraftInfo",
    "BlueprintVersionInfo",
    "BoundRef",
    "CapabilityFreshness",
    "CompileResult",
    "CredentialMirror",
    "ExportBundle",
    "ImpactAnalysis",
    "ManagedObjectInfo",
    "NrrItem",
    "PromotionResult",
    "QuestionDraft",
    "SectionLayout",
    "ValidationOutcome",
    "VersionPage",
})


# --- One name per concept at the mirror boundary ---------------------------


def test_the_level_field_has_one_name_on_a_mirror_issue() -> None:
    from_severity = Issue(issue_id="i1", severity="BLOCKING")
    assert from_severity.level == "BLOCKING"
    assert from_severity.is_blocking
    assert "severity" not in from_severity.model_dump()
    assert "severity" not in from_severity.model_dump(by_alias=True)


def test_the_canonical_name_wins_when_a_provider_sends_both() -> None:
    assert Issue(issue_id="i2", level="INFO", severity="BLOCKING").level == "INFO"
    assert Issue(issue_id="i3").level == "WARNING"
    assert Issue.model_validate({"issueId": "i4", "severity": "BLOCKING"}).level == "BLOCKING"


def test_a_provider_answering_neither_spelling_is_still_an_issue() -> None:
    # Raising here would fail the whole provider call over one row's missing level, which
    # is the failure ``extra="ignore"`` on the mirror base exists to keep out of S1.
    assert Issue.model_validate({"issue_id": "i5", "level": None}).level == "WARNING"
    assert Issue.model_validate({"issue_id": "i6"}).description == ""


def test_the_text_of_a_finding_survives_the_boundary() -> None:
    """S2 names the finding's text ``message`` and its fix ``remediation``.

    Dropping them was silent: the row validated, carried a level and an id, and reached S5 with
    an empty description — a BLOCKING nobody could explain.
    """
    row = Issue.model_validate(
        {
            "issueId": "bp-2",
            "ruleId": "BPV-PCH-002",
            "level": "BLOCKING",
            "message": "source artifact does not exist",
            "remediation": "upload the artifact the row cites",
            "businessKey": "form.b",
        }
    )
    assert row.description == "source artifact does not exist"
    assert row.suggested_action == "upload the artifact the row cites"
    assert row.business_key == "form.b"
    # S1's own name stays authoritative when a document carries both.
    assert Issue(issue_id="i7", description="mine", message="theirs").description == "mine"


@pytest.mark.skipif(not ALL_PROVIDERS, reason="needs S2, S3 and S4 installed")
def test_an_issue_from_each_provider_lands_as_one_mirror_with_level(
    blueprint_provider: Any, dhis2_provider: Any, intelligence_provider: Any
) -> None:
    """The three subsystems' real ``Issue`` classes, not a hand-written dict.

    S2 and S4 say ``level``, S3 says ``severity`` — and a provider that adds a field must
    not break the read, which is what ``extra="ignore"`` is for.
    """
    from factory_blueprint.enums import IssueLevel

    from_s2 = Issue.model_validate(
        blueprint_provider(
            issue_id="bp-1",
            rule_id="BP-V-01",
            level=IssueLevel.BLOCKING,
            message="bad ref",
            business_key="form.a",
        ).model_dump(mode="json")
    )
    from_s4 = Issue.model_validate(
        dhis2_provider(issue_id="dx-1", description="import rejected").model_dump(mode="json")
    )
    from_s3 = Issue.model_validate(
        intelligence_provider(issue_id="ai-1", severity="BLOCKING").model_dump(mode="json")
    )
    assert (from_s2.level, from_s4.level, from_s3.level) == ("BLOCKING", "WARNING", "BLOCKING")
    assert from_s2.business_key == "form.a"
    assert from_s2.description == "bad ref", "S2 calls the text ``message``; S1 must not lose it"
    assert all("severity" not in i.model_dump() for i in (from_s2, from_s4, from_s3))


# --- Real provider output must validate into the mirrors --------------------


@pytest.mark.skipif(not ALL_PROVIDERS, reason="needs S2, S3 and S4 installed")
def test_a_real_parse_result_validates_into_the_mirror() -> None:
    parsed = _s3("dto.result", "ParsedInput")(parse_id="p-1", used_model=True, parser_type="CSV")
    mirrored = dto.ParsedInput.model_validate(parsed.model_dump(mode="json"))
    # ``used_model`` is a *whether* per AI-INP-004; a str mirror raised on every real parse.
    assert mirrored.used_model is True
    assert mirrored.parse_id == "p-1"


@pytest.mark.skipif(not ALL_PROVIDERS, reason="needs S2, S3 and S4 installed")
def test_a_real_question_draft_validates_into_the_mirror() -> None:
    question = _s3("dto.common", "Question")(
        local_id="q1", text="which year?", severity="HIGH", affects=["register:vc"]
    )
    mirrored = QuestionDraft.model_validate(question.model_dump(mode="json"))
    assert (mirrored.local_id, mirrored.severity, mirrored.affects) == ("q1", "HIGH", ["register:vc"])


# --- Field kinds must agree (CP-RT-005) ------------------------------------


def _kind(annotation: Any) -> str:
    """The kind of a field, coarse on purpose: mirrors narrow containers deliberately."""
    if annotation is None or annotation is type(None):
        return "none"
    if annotation is Any or annotation is object:
        return "any"
    origin = typing.get_origin(annotation)
    if origin is not None:
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if origin in (list, set, frozenset, tuple):
            return "list"
        if origin is dict:
            return "dict"
        return _kind(args[0]) if args else "any"
    if isinstance(annotation, type):
        if annotation is bool:
            return "bool"
        if annotation is int:
            return "int"
        if annotation is float:
            return "float"
        if annotation is str:
            return "str"
        if issubclass(annotation, enum.Enum):
            return "enum"
        if issubclass(annotation, BaseModel):
            return "model"
        if issubclass(annotation, (dict,)):
            return "dict"
        if issubclass(annotation, (list,)):
            return "list"
        if annotation.__module__.startswith("datetime") or issubclass(annotation, datetime):
            return "dt"
    return "other"


def _compatible(mirror: str, provider: str) -> bool:
    if "any" in (mirror, provider) or mirror == provider:
        return True
    if {"enum", "str", "other"} >= {mirror, provider}:  # str-enums flatten to str
        return True
    if {"dt", "str", "none"} >= {mirror, provider}:  # mirrors carry ISO strings too
        return True
    if {"int", "float"} >= {mirror, provider}:
        return True
    return mirror in ("dict", "list") and provider in ("dict", "list")


def _provider_models() -> dict[str, list[tuple[str, type[BaseModel]]]]:
    """Every public pydantic model in the installed provider packages, by class name."""
    found: dict[str, list[tuple[str, type[BaseModel]]]] = {}
    for package in PROVIDER_PACKAGES:
        try:
            root = importlib.import_module(package)
        except ImportError:
            continue
        for info in pkgutil.walk_packages(root.__path__, package + "."):
            if ".dto" not in info.name and ".models" not in info.name:
                continue
            try:
                module = importlib.import_module(info.name)
            except ImportError:
                continue
            for name, member in vars(module).items():
                if (
                    isinstance(member, type)
                    and issubclass(member, BaseModel)
                    and member.__module__ == module.__name__
                ):
                    found.setdefault(name, []).append((f"{module.__name__}:{name}", member))
    return found


def _mirror_classes() -> dict[str, type[BaseModel]]:
    return {
        name: member
        for name in dto.__all__
        if isinstance(member := getattr(dto, name), type)
        and issubclass(member, BaseModel)
        and member is not BaseModel
    }


def _s3(module: str, name: str) -> Any:
    return getattr(importlib.import_module(f"factory_intelligence.{module}"), name)


def _pairs() -> list[tuple[str, str, type[BaseModel], type[BaseModel]]]:
    models = _provider_models()
    out: list[tuple[str, str, type[BaseModel], type[BaseModel]]] = []
    for mirror_name, mirror in sorted(_mirror_classes().items()):
        for location, provider in models.get(mirror_name, []):
            out.append((mirror_name, location, mirror, provider))
        renamed = RENAMED_TWINS.get(mirror_name)
        if renamed:
            module, cls = renamed.split(":")
            out.append((mirror_name, renamed, mirror, getattr(importlib.import_module(module), cls)))
    return out


@pytest.fixture(scope="module")
def blueprint_provider() -> Any:
    return importlib.import_module("factory_blueprint.dto.issue").Issue


@pytest.fixture(scope="module")
def dhis2_provider() -> Any:
    return importlib.import_module("factory_dhis2.dto.issue").Issue


@pytest.fixture(scope="module")
def intelligence_provider() -> Any:
    return importlib.import_module("factory_intelligence.dto.common").Issue


@pytest.mark.skipif(not ALL_PROVIDERS, reason="needs S2, S3 and S4 installed")
def test_every_mirror_field_kind_matches_its_provider_type() -> None:
    pairs = _pairs()
    assert pairs, "no provider models found to compare against"
    problems: list[str] = []
    for mirror_name, location, mirror, provider in pairs:
        for field, mirror_field in mirror.model_fields.items():
            provider_field = provider.model_fields.get(field)
            if provider_field is None:
                continue  # a narrow mirror is by design; S1 only reads what it declares
            mirror_kind = _kind(mirror_field.annotation)
            provider_kind = _kind(provider_field.annotation)
            if not _compatible(mirror_kind, provider_kind):
                problems.append(
                    f"{mirror_name}.{field}: S1 says {mirror_kind}, {location} says {provider_kind}"
                )
    assert not problems, "mirror field types drifted from the provider:\n" + "\n".join(problems)


@pytest.mark.skipif(not ALL_PROVIDERS, reason="needs S2, S3 and S4 installed")
def test_a_mirror_is_either_compared_or_explained() -> None:
    """The comparison above covers what it covers — and says what it does not."""
    compared = {mirror_name for mirror_name, _loc, _m, _p in _pairs()}
    unexplained = set(_mirror_classes()) - compared - UNCOMPARED_MIRRORS
    assert not unexplained, f"mirrors with neither a provider twin nor a reason: {sorted(unexplained)}"
    stale = UNCOMPARED_MIRRORS & compared
    assert not stale, f"these mirrors now have a twin and should be compared: {sorted(stale)}"
