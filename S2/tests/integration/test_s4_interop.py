"""S2 ↔ S4 real interoperation (SRS chapter 8, BP-REG-008/008a/009, BP-ASM-03).

S2 used to talk to an in-memory stand-in for S4.  S4 is delivered now, so these tests
pin the seam against the real subsystem rather than against S2's own guess of it:

* the default read-only implementation is ``S4Adapter`` over ``factory_dhis2.api``, and
  the in-memory stub only exists where a test installed it on purpose;
* the four calls bind to S4's **published signatures**, so a rename or a reordered
  parameter on either side fails here instead of at a customer's freeze;
* S2 parses the ``CAPABILITY_PROFILE`` and ``DISCOVERY_RESULT`` documents that S4's own
  builders produce — the formats S4 owns (BP-ASM-03) and S2 only reads;
* S4's answer objects, whose truthiness is deliberately not "non-empty", are read as the
  occupant they describe;
* the ``METADATA_PACKAGE`` S2 publishes is read back by S4's own package reader, so the
  sections S4 says it cannot execute without (BP-CMP-027a, DX-ASM-02) are checked against
  what S2 really writes rather than against either side's fixture.

The last group is the one that only shows up with a real S4: an UPDATE entry's UID is
**supposed** to exist in the environment, so a bare "does this exist" would report every
Brownfield update as a conflict.
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import factory_dhis2.api as s4_api
import pytest
from factory_dhis2.contract.capability_profile import (
    as_artifact_content as profile_artifact_content,
)
from factory_dhis2.contract.capability_profile import build_capability_profile_document
from factory_dhis2.contract.discovery_result import (
    DiscoveredObjectDocument,
    build_discovery_result_document,
)
from factory_dhis2.contract.discovery_result import (
    as_artifact_content as discovery_artifact_content,
)
from factory_dhis2.execution.drift import drift_watch
from factory_dhis2.payload.package import (
    REQUIRED_MANIFEST_SECTIONS,
    MetadataPackage,
    require_complete,
)
from factory_dhis2.profiles import registry as s4_profiles
from factory_dhis2.queries.associated_data import AssociatedDataVerdict
from factory_dhis2.queries.existence import CodeOccupancy as S4CodeOccupancy
from factory_dhis2.queries.existence import UidOccupancy as S4UidOccupancy

from factory_blueprint import deps
from factory_blueprint.compilers import envbound, files
from factory_blueprint.compilers.context import CompileInputs
from factory_blueprint.content.document import BlueprintDocument
from factory_blueprint.dto.build import BuildContext
from factory_blueprint.dto.issue import Issue
from factory_blueprint.registry import conflicts
from factory_blueprint.s4.adapter import S4Adapter
from factory_blueprint.s4.contract import discovery_from_json, profile_from_json
from factory_blueprint.s4.protocol import CodeOccupancy, UidOccupancy
from factory_blueprint.s4.stub import (
    StubDhis2,
    discovered_object,
    discovery_document,
    profile_document,
)
from factory_blueprint.validators.expressions.grammar import Grammar
from tests.unit import helpers

READ_ONLY_CALLS = {
    "generate_uids": ("DEV", 5),
    "uid_exists": ("DEV", ["f9bnacNsnp1"]),
    "code_exists": ("DEV", "dataElement", ["MC_DE_WEIGHT"]),
    "has_associated_data": ("DEV", "dataElement", "f9bnacNsnp1"),
}


# --- the wiring is real --------------------------------------------------


def test_the_default_read_only_implementation_is_the_real_subsystem() -> None:
    deps.reset_providers()
    try:
        implementation = deps.get_dhis2()
        assert isinstance(implementation, S4Adapter), (
            "a deployed S2 must not fall back to invented answers about DHIS2"
        )
        assert implementation.module is s4_api
    finally:
        deps.reset_providers()


def test_the_stub_only_exists_where_a_caller_installed_it() -> None:
    deps.set_dhis2(StubDhis2())
    try:
        assert isinstance(deps.get_dhis2(), StubDhis2)
    finally:
        deps.reset_providers()
    assert not isinstance(deps.get_dhis2(), StubDhis2)
    deps.set_dhis2(None)


def test_the_adapter_binds_to_s4s_published_signatures() -> None:
    """S4's api module is frozen for S2; a drift here is a break in a running system."""
    for name, args in READ_ONLY_CALLS.items():
        function = getattr(s4_api, name)
        signature = inspect.signature(function)
        signature.bind(*args)  # positional order S2 uses, in one call
        assert getattr(function, "__wrapped__", function) is not function, (
            f"factory_dhis2.api.{name} lost its @external_io marker; S2 calls it outside a unit of work"
        )


def test_s2_imports_s4_only_through_its_public_module() -> None:
    """00 doc 6.1: a subsystem's only entry point is its ``api``."""
    adapter = S4Adapter()
    assert adapter.module.__name__ == "factory_dhis2.api"


def test_a_missing_s4_is_named_rather_than_worked_around(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S2 stays importable without S4, but a call says so instead of inventing an answer."""
    import sys

    from factory_foundation.api import FactoryError

    # a None entry in sys.modules makes ``import factory_dhis2.api`` fail, which is
    # exactly the state of a deployment that installed factory-blueprint without [s4]
    monkeypatch.setitem(sys.modules, "factory_dhis2.api", None)
    with pytest.raises(FactoryError) as caught:
        S4Adapter().generate_uids("DEV", 1)
    assert caught.value.details["dependency"] == "factory_dhis2"
    assert "not installed" in str(caught.value)


# --- S4's documents, read by S2 (BP-ASM-03) ------------------------------


@pytest.fixture(scope="module")
def real_profile() -> dict[str, Any]:
    s4_profiles.load_builtin_profiles()
    profile = s4_profiles.profile_for_version("2.41")
    document = build_capability_profile_document(
        profile, environment_key="DEV", dhis2_version="2.41.10", probed={"reachable": True}
    )
    return profile_artifact_content(document)


def test_s2_reads_a_capability_profile_that_s4_wrote(real_profile: dict[str, Any]) -> None:
    profile = profile_from_json(real_profile)
    assert profile.version == "2.41.10"
    assert profile.profile_version == "Dhis241Profile"
    assert profile.environment_key == "DEV"

    # the resource catalogue resolves logical names, which is how S2 picks endpoints
    assert profile.resource("system.id") is not None, "BP-REG-008 asks for it by logical name"
    assert profile.resource("metadata.dataElement") is not None
    assert profile.object_schema("dataElement") is not None

    # a controlled field must come from what the version really allows (BP-REG-003)
    allowed = profile.controlable_fields("dataElement")
    assert "name" in allowed and "valueType" in allowed
    assert "lastUpdated" not in allowed


def test_the_expression_grammar_s4_names_is_one_s2_ships(real_profile: dict[str, Any]) -> None:
    """BP-VAL-021: S2 picks its parser by the version string S4 publishes."""
    named = profile_from_json(real_profile).expression_grammar_version
    assert named == "dhis2-2.41"
    grammar = Grammar.load(str(named))
    assert grammar.name == named


def test_s2_reports_an_unknown_grammar_instead_of_guessing(real_profile: dict[str, Any]) -> None:
    from factory_blueprint.errors import ExpressionGrammarMissing

    drifted = dict(real_profile, expressionGrammarVersion="dhis2-9.99")
    with pytest.raises(ExpressionGrammarMissing):
        Grammar.load(str(profile_from_json(drifted).expression_grammar_version))


def test_s2_reads_a_discovery_result_that_s4_wrote() -> None:
    s4_profiles.load_builtin_profiles()
    document = build_discovery_result_document(
        environment_key="TEST",
        discovered_at="2026-09-01T00:00:00.000Z",
        version="2.41.10",
        scope={"types": ["dataElement"]},
        objects=[
            DiscoveredObjectDocument(
                dhis2_type="dataElement",
                uid="f9bnacNsnp1",
                code="DE_WEIGHT",
                name="Weight",
                last_updated="2026-08-01T00:00:00.000Z",
                content_hash="sha256:" + "a" * 64,
                content={"id": "f9bnacNsnp1", "name": "Weight", "valueType": "NUMBER"},
                references={"categoryCombo": "bjDvmb4bfuf"},
            )
        ],
        defaults={"categoryCombo": "bjDvmb4bfuf"},
    )
    discovery = discovery_from_json(discovery_artifact_content(document))

    assert discovery.environment_key == "TEST"
    found = discovery.find_by_uid("dataElement", "f9bnacNsnp1")
    assert found is not None
    # UPDATE merging needs the current full content (BP-ASM-03), not just an identity
    assert found.content is not None and found.content["valueType"] == "NUMBER"
    assert found.content_hash == "sha256:" + "a" * 64
    assert found.references == {"categoryCombo": "bjDvmb4bfuf"}
    assert [item.uid for item in discovery.find_by_code("dataElement", "DE_WEIGHT")] == ["f9bnacNsnp1"]
    # the implicit default a DataElement without a CategoryCombo must reference (FR-COV-007)
    assert discovery.default_category_combo_uid() == "bjDvmb4bfuf"


def test_s2_tolerates_a_field_s4_added_later(real_profile: dict[str, Any]) -> None:
    """Extra = allow, because a newer S4 must not break an older S2 mid-deployment."""
    profile = profile_from_json(dict(real_profile, someNewField={"nested": [1, 2]}))
    assert profile.version == real_profile["version"]


# --- S4's answer objects, read as the occupant they describe -------------


class _Answers:
    """A module-shaped double that hands back S4's *own* classes."""

    def __init__(
        self,
        uids: dict[str, S4UidOccupancy],
        codes: dict[str, S4CodeOccupancy],
        verdict: AssociatedDataVerdict,
        issued: list[str],
    ) -> None:
        self._uids, self._codes, self._verdict, self._issued = uids, codes, verdict, issued

    def generate_uids(self, environment_key: str, count: int) -> list[str]:
        assert environment_key == "DEV"
        return self._issued[:count]

    def uid_exists(self, environment_key: str, uids: Sequence[str]) -> dict[str, S4UidOccupancy]:
        return {uid: self._uids[uid] for uid in uids}

    def code_exists(
        self, environment_key: str, dhis2_type: str, codes: Sequence[str]
    ) -> dict[str, S4CodeOccupancy]:
        assert dhis2_type == "dataElement"
        return {code: self._codes[code] for code in codes}

    def has_associated_data(
        self, environment_key: str, dhis2_type: str, uid: str, *, program: str | None = None
    ) -> AssociatedDataVerdict:
        return self._verdict


def _answers() -> _Answers:
    return _Answers(
        uids={
            "ours0000001": S4UidOccupancy(True, dhis2_type="dataElement", code="DE_WEIGHT"),
            "free00000001": S4UidOccupancy(False),
        },
        codes={"DE_WEIGHT": S4CodeOccupancy(True, uid="ours0000001")},
        verdict=AssociatedDataVerdict(False, "no events reference it"),
        issued=["f9bnacNsnp1"],
    )


def test_an_occupied_uid_keeps_who_holds_it() -> None:
    adapter = S4Adapter(module=_answers())
    answer = adapter.uid_exists("DEV", ["ours0000001", "free00000001"])
    assert answer["ours0000001"] == UidOccupancy(
        exists=True, dhis2_type="dataElement", code="DE_WEIGHT", name=None
    )
    assert answer["free00000001"].exists is False
    # S4's mapping is truthy on ``exists`` alone; S2 must not carry that ambiguity on
    assert bool(answer["free00000001"]) is False


def test_a_code_occupant_keeps_its_uid() -> None:
    answer = S4Adapter(module=_answers()).code_exists("DEV", "dataElement", ["DE_WEIGHT"])
    assert answer["DE_WEIGHT"] == CodeOccupancy(exists=True, uid="ours0000001", name=None)


def test_a_free_uid_and_a_deletable_object_read_as_said() -> None:
    adapter = S4Adapter(module=_answers())
    assert adapter.generate_uids("DEV", 1) == ["f9bnacNsnp1"]
    assert adapter.has_associated_data("DEV", "dataElement", "ours0000001") is False


def test_a_verdict_that_cannot_be_judged_means_do_not_delete() -> None:
    """S4 answers conservatively (DX-QRY-004); S2 has to hear it as "has data"."""
    blocking = _Answers({}, {}, AssociatedDataVerdict(True, "判定端点不可用"), [])
    assert S4Adapter(module=blocking).has_associated_data("DEV", "dataElement", "x") is True


def test_an_unreadable_answer_fails_rather_than_becoming_a_default() -> None:
    from factory_foundation.api import FactoryError

    class _Broken:
        def uid_exists(self, environment_key: str, uids: Sequence[str]) -> Any:
            return ["not", "a", "mapping"]

    with pytest.raises(FactoryError):
        S4Adapter(module=_Broken()).uid_exists("DEV", ["x"])


def test_a_partial_answer_is_an_error_not_a_free_number() -> None:
    """Asking about three and answering two must not read as "the third is free"."""
    from factory_foundation.api import FactoryError

    class _Partial:
        def uid_exists(self, environment_key: str, uids: Sequence[str]) -> Any:
            return {uids[0]: S4UidOccupancy(False)}

    with pytest.raises(FactoryError, match="1 of the 3"):
        S4Adapter(module=_Partial()).uid_exists("DEV", ["a0000000001", "b0000000001", "c0000000001"])


def test_an_unreadable_entry_is_not_read_as_an_unoccupied_uid() -> None:
    """Dropping the row would tell S2 the number is free, which is the dangerous direction."""
    from factory_foundation.api import FactoryError

    class _HalfAnswer:
        def uid_exists(self, environment_key: str, uids: Sequence[str]) -> Any:
            return {
                "good0000001": S4UidOccupancy(True, dhis2_type="dataElement"),
                "bad00000001": None,
            }

    with pytest.raises(FactoryError, match="bad00000001"):
        S4Adapter(module=_HalfAnswer()).uid_exists("DEV", ["good0000001", "bad00000001"])


# --- the semantics that only a real S4 exposes ---------------------------


def _registry_document(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"registry": {"entries": entries}}


def _entry(key: str, uid: str, disposition: str, dhis2_type: str = "dataElement") -> dict[str, Any]:
    return {
        "businessKey": key,
        "dhis2Type": dhis2_type,
        "uid": uid,
        "code": f"CODE_{key.upper()}",
        "disposition": disposition,
    }


def _occupied_by(environment_key: str, uid: str, dhis2_type: str | None) -> StubDhis2:
    stub = StubDhis2()
    stub.add_existing_uid(environment_key, uid, dhis2_type=dhis2_type)
    return stub


def _rules(issues: list[Issue]) -> set[str]:
    return {issue.rule_id for issue in issues}


def test_an_update_entry_holding_its_own_uid_is_not_a_conflict() -> None:
    """The object an UPDATE targets already exists — that is the point of the disposition.

    A real S4 answers "yes, occupied" for it; reading that as a conflict would block
    every Brownfield freeze, which is exactly what the in-memory stub never caught.
    """
    document = _registry_document([_entry("weight", "f9bnacNsnp1", "UPDATE")])
    deps.set_dhis2(_occupied_by("TEST", "f9bnacNsnp1", "dataElement"))
    try:
        issues = conflicts.check_uid_conflicts(document, ["TEST"])
    finally:
        deps.set_dhis2(None)
    assert issues == [], "same UID, same type: that is our own target object"


def test_a_create_entry_whose_uid_already_exists_is_blocked() -> None:
    document = _registry_document([_entry("weight", "f9bnacNsnp1", "CREATE")])
    deps.set_dhis2(_occupied_by("TEST", "f9bnacNsnp1", "dataElement"))
    try:
        issues = conflicts.check_uid_conflicts(document, ["TEST"])
    finally:
        deps.set_dhis2(None)
    assert _rules(issues) == {"BPV-ENV-001"}
    assert issues[0].business_key == "weight"
    assert issues[0].details["holderType"] == "dataElement"


def test_an_update_entry_held_by_another_type_is_blocked() -> None:
    """Same number, different kind of object: two concepts on one UID (BP-REG-005)."""
    document = _registry_document([_entry("weight", "f9bnacNsnp1", "UPDATE")])
    deps.set_dhis2(_occupied_by("TEST", "f9bnacNsnp1", "optionSet"))
    try:
        issues = conflicts.check_uid_conflicts(document, ["TEST"])
    finally:
        deps.set_dhis2(None)
    assert _rules(issues) == {"BPV-ENV-001"}
    assert "not by the dataElement" in issues[0].message


def test_an_update_entry_whose_code_is_its_own_is_not_a_conflict() -> None:
    document = _registry_document([_entry("weight", "f9bnacNsnp1", "UPDATE")])
    stub = StubDhis2()
    stub.add_existing_code("TEST", "dataElement", "CODE_WEIGHT", uid="f9bnacNsnp1")
    deps.set_dhis2(stub)
    try:
        issues = conflicts.check_code_conflicts(document, ["TEST"])
    finally:
        deps.set_dhis2(None)
    assert issues == [], "the object we update already carries our code"


def test_an_update_entry_whose_code_belongs_to_another_object_is_blocked() -> None:
    document = _registry_document([_entry("weight", "f9bnacNsnp1", "UPDATE")])
    stub = StubDhis2()
    stub.add_existing_code("TEST", "dataElement", "CODE_WEIGHT", uid="other000001")
    deps.set_dhis2(stub)
    try:
        issues = conflicts.check_code_conflicts(document, ["TEST"])
    finally:
        deps.set_dhis2(None)
    assert _rules(issues) == {"BPV-ENV-002"}
    assert issues[0].details["holderUid"] == "other000001"


def test_s2_asks_s4_only_the_four_read_only_questions() -> None:
    """C-07 and chapter 8: S2 never writes to DHIS2 and never reaches for more."""
    stub = StubDhis2()
    document = _registry_document(
        [_entry("weight", "f9bnacNsnp1", "CREATE"), _entry("clinic", "ou000000001", "UPDATE")]
    )
    deps.set_dhis2(stub)
    try:
        conflicts.check_uid_conflicts(document, ["TEST"])
    finally:
        deps.set_dhis2(None)
    names = {name for name, _args in stub.calls}
    assert names == {"uid_exists"}
    asked = stub.calls[0][1][1]
    assert set(asked) == {"f9bnacNsnp1", "ou000000001"}, "REUSE and REFERENCE are not claimed"


# --- what S2 publishes for S4 to read ------------------------------------

UAT = "UAT"
#: what the design environment showed when the version was frozen …
DESIGN_HASH = "sha256:" + "a" * 64
#: … and what UAT holds now.  Only the second one may reach UAT's package.
UAT_HASHES = {
    "deUAT001": "sha256:" + "f" * 64,
    "sexUAT001": "sha256:" + "d" * 64,
    "OUUAT001": "sha256:" + "e" * 64,
    # the environment's own default CategoryCombo: referenced implicitly, by two objects
    "catComboUa1": "sha256:" + "9" * 64,
}
WATCHED_DISPOSITIONS = ("REUSE", "REFERENCE", "UPDATE")


def _uat_products() -> tuple[dict[str, Any], dict[str, Any]]:
    """Compile one version for UAT the way S2 does, and return its manifest and payload."""
    document = helpers.empty_document()
    entries = [
        helpers.registry_entry("weight", "dataElement", "CREATE", uid="deWeight0001"),
        helpers.registry_entry(
            "clinicname", "dataElement", "UPDATE", observed_uid="deUAT001",
            controlled_fields=["name"],
        ),
        helpers.registry_entry("sex", "optionSet", "REUSE", observed_uid="sexUAT001"),
        helpers.registry_entry(
            "facility", "organisationUnit", "REFERENCE", observed_uid="OUUAT001"
        ),
    ]
    for entry in entries:
        if entry.get("observedUid"):
            entry["observedHash"] = DESIGN_HASH
            entry["observedLastUpdated"] = "2026-01-01T00:00:00Z"
    document["registry"]["entries"] = entries
    document["mapping"]["entries"] = [
        helpers.mapping_entry("weight", "dataElement", fields={"name": {"default": "Weight"}}),
        helpers.mapping_entry(
            "clinicname", "dataElement", fields={"name": {"default": "Clinic name"}}
        ),
    ]
    discovery = discovery_document(
        UAT,
        [
            discovered_object(
                "optionSet", "sexUAT001", name="Sex", content_hash=UAT_HASHES["sexUAT001"]
            ),
            discovered_object(
                "organisationUnit", "OUUAT001", name="Facility",
                content_hash=UAT_HASHES["OUUAT001"],
            ),
            discovered_object(
                "dataElement", "deUAT001", name="Facility name",
                content={"id": "deUAT001", "name": "Facility name", "valueType": "TEXT"},
                content_hash=UAT_HASHES["deUAT001"],
            ),
            discovered_object(
                "categoryCombo", "catComboUa1", name="Default",
                content_hash=UAT_HASHES["catComboUa1"],
            ),
        ],
        defaults={"categoryCombo": "catComboUa1"},
    )
    product = envbound.compile_for_environment(
        CompileInputs(
            project_id="xcheck",
            blueprint_version="0.1.0",
            blueprint_hash=BlueprintDocument(document).content_hash,
            document=document,
            build_context=BuildContext(
                built_at=datetime(2026, 5, 5, tzinfo=UTC), seed=1, requester="xcheck"
            ),
            blueprint_artifact_id="art-01XCHECK000000000000000000",
            profile=profile_from_json(profile_document(version="2.41")),
            discovery=discovery_from_json(discovery),
            environment_key=UAT,
            bindings={
                "weight": "deWeight0001",
                "clinicname": "deUAT001",
                "sex": "sexUAT001",
                "facility": "OUUAT001",
            },
            observed={
                "deUAT001": {"id": "deUAT001", "name": "Facility name", "valueType": "TEXT"}
            },
            # what build/service.py hands the compile for one environment: the Registry
            # rows this project creates, updates or deprecates *there* (BP-REG-011)
            managed_scope=[
                {
                    "business_key": "weight",
                    "dhis2_type": "dataElement",
                    "uid": "deWeight0001",
                    "disposition": "CREATE",
                    "code": "MC_DE_WEIGHT",
                    "controlled_fields": [],
                },
                {
                    "business_key": "clinicname",
                    "dhis2_type": "dataElement",
                    "uid": "deUAT001",
                    "disposition": "UPDATE",
                    "code": "MC_DE_CLINICNAME",
                    "controlled_fields": ["name"],
                    "observed_hash": DESIGN_HASH,
                    "observed_last_updated": "2026-01-01T00:00:00Z",
                },
            ],
        )
    )
    manifest: dict[str, Any] = json.loads(product.files[files.MANIFEST_FILE])
    payload: dict[str, Any] = json.loads(product.files[files.METADATA_FILE])
    return manifest, payload


def _package() -> MetadataPackage:
    """The package as S4's own reader builds it (DX-ASM-02 completeness included)."""
    manifest, payload = _uat_products()
    return MetadataPackage(
        artifact_id="art-01XCHECK000000000000000001",
        artifact_hash="sha256:" + "0" * 64,
        payload=payload,
        manifest=manifest,
        missing_sections=[
            name for name in REQUIRED_MANIFEST_SECTIONS if name not in manifest
        ],
    )


def test_the_manifest_holds_every_baseline_s4_is_left_to_read() -> None:
    """BP-CMP-027a read by S4: S2 does not call S4 at execution time, so the manifest has
    to carry the observation baseline of each REUSE, REFERENCE and UPDATE object — the
    drift check, the Snapshot and the post-commit readback have nothing else to work from."""
    package = _package()
    require_complete(package, "the drift check")
    baselines = {str(row["businessKey"]): row for row in package.observation_baselines()}
    assert set(baselines) == {"clinicname", "sex", "facility"}, (
        "an object that already exists owes a baseline; the one this version creates has "
        "nothing to have observed yet, and the implicitly referenced default CategoryCombo "
        "is not a Registry entry, so it travels in the out-of-scope references instead"
    )


def test_a_baseline_is_stamped_at_the_uid_the_environment_resolve_bound_it_to() -> None:
    """S4 joins ``observationBaselines`` by businessKey and reads the instance by UID, so
    those two have to name the same object or one deploy compares another's content."""
    package = _package()
    baselines = {str(row["businessKey"]): row for row in package.observation_baselines()}
    for row in package.uid_map():
        disposition = str(row.get("disposition"))
        if disposition not in WATCHED_DISPOSITIONS:
            continue
        baseline = baselines[str(row["businessKey"])]
        assert baseline["uid"] == row["uid"], "the join must not mix two resolutions"
        assert baseline["dhis2Type"] == row["dhis2Type"]
        assert baseline["observedHash"] == UAT_HASHES[row["uid"]], (
            "the baseline is what this environment holds, not what the design environment "
            "showed at freeze time"
        )


def test_s4_can_compare_every_object_s2_declares_observed() -> None:
    """No watched object comes back without a baseline.

    A missing baseline is reported as ``NO_BASELINE`` and never counts as drift, so an
    incomplete manifest does not fail loudly — it silently stops protecting the very
    objects a Brownfield deploy can overwrite.
    """
    watch = drift_watch(_package())
    assert [row for row in watch if not row.get("observedHash")] == []
    assert not [row for row in watch if row.get("disposition") == "CREATE"]
    keys = [(str(row["uid"]), str(row["businessKey"])) for row in watch]
    assert len(keys) == len(set(keys)), (
        "one object is monitored once: it reaches the watch list both as a Registry entry "
        "and as a reference outside the managed scope"
    )
    assert ("catComboUa1", "default.categoryCombo") in keys, (
        "the environment's default CategoryCombo is referenced implicitly and is in no "
        "Registry entry, so the out-of-scope section is the only source that carries it"
    )


def test_the_manifest_states_what_this_project_manages_in_this_environment() -> None:
    """SRS 12.4 / BP-CMP-027a: ``metadata_manifest.json`` owes S4 the Managed Scope.

    S4 never calls S2 while executing: the Snapshot's scope definition (FR-SNP-001) and the
    "does this payload touch an object we do not own" check read the package alone.  An
    empty section is not a neutral omission — it shrinks every Snapshot to the payload
    objects and lets an out-of-scope write pass as managed.
    """
    package = _package()
    rows = package.managed_scope()
    assert sorted(str(row["businessKey"]) for row in rows) == ["clinicname", "weight"], (
        "CREATE and UPDATE are managed here; REUSE and REFERENCE are dependencies, not "
        "objects this project owns"
    )
    for row in rows:
        assert {"dhis2Type", "uid", "disposition"} <= set(row), (
            "S4 reads exactly these keys, in the manifest's own camelCase"
        )
    update = next(row for row in rows if row["businessKey"] == "clinicname")
    assert update["controlledFields"] == ["name"], (
        "the controlled fields travel with the scope entry, or the readback compares "
        "fields nobody authorized this project to change"
    )
    assert package.managed_scope() == sorted(
        package.managed_scope(), key=lambda row: (str(row["dhis2Type"]), str(row["businessKey"]))
    ), "the section is ordered, so two compiles of one version write the same bytes"


def test_the_payload_carries_no_factory_control_field() -> None:
    """FR-CMP-006: the import payload holds DHIS2 fields and nothing else.

    A deletion is a manifest candidate that S4 runs as its own step, not a key inside the
    payload: ``businessKey`` means nothing to DHIS2, and putting it in the payload would
    put a Factory-only identifier into the bytes the gate hashes and the instance receives.
    """
    manifest, payload = _uat_products()
    assert "@DELETE" not in payload
    assert payload, "the scenario does compile objects, so this is not an empty-payload pass"
    for collection in payload.values():
        for obj in collection:
            assert "businessKey" not in obj
            assert not any(key.startswith("_") for key in obj)
    assert manifest["managedScope"], (
        "the sections the payload must not carry belong to the manifest, which does carry them"
    )
