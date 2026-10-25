"""The Profile's say over one compile (FR-CAP-001/003, BP-CMP-030, AC-CAP-01).

A compile against ``Dhis242Profile`` must differ from one against ``Dhis241Profile``
because the *data* differs, not because a compiler branches on a version string
(FR-CAP-001).  This module is where that data is read: the shipped type descriptor is
the baseline, the Profile is what the target instance actually accepts, and every
disagreement between the two is both enforced and recorded so the Diff can show it.

Two of S4's statements are load-bearing here and both come from its published
artifact rather than from guesswork (DX-CAP-003):

* ``schemas`` already has the Profile's ``descriptorOverrides`` folded in, so applying
  them again would be a second opinion S4 never asked for;
* ``deprecated`` / ``removed`` use the kinds ``endpoint``, ``field`` and ``parameter``,
  and a ``field`` entry is named either ``<dhis2Type>.<property>`` or bare ``<property>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.capability.language import (
    DEPRECATED,
    FORBIDDEN,
    REMOVED,
    SUPPORTED,
    FieldVerdict,
    difference,
    severity_of,
)
from factory_blueprint.descriptors.model import TypeDescriptor
from factory_blueprint.dto.issue import Issue, make_issue
from factory_blueprint.enums import IssueLevel, Section
from factory_blueprint.s4.contract import CapabilityProfile

RULE_TYPE_OUT_OF_RANGE = "BPV-CAP-001"
RULE_FIELD_REMOVED = "BPV-CAP-002"
RULE_FIELD_FORBIDDEN = "BPV-CAP-003"
RULE_IMPORT_CONSTRAINT = "BPV-CAP-004"
RULE_FIELD_DEPRECATED = "BPV-CAP-005"

#: the formats the shipped descriptor set was written against; a Profile that says
#: otherwise is telling us this environment stores that part of an object elsewhere
BASELINE_TRANSLATION_FORMAT = "translations"
BASELINE_SHARING_FORMAT = "sharing"

#: the Profile statements that constrain what S2 itself emits.  A rule whose subject is
#: another subsystem travels to the manifest instead of being evaluated here.
IMPORTED_DEFAULTS_PATH = ("metadata", "defaults")


def series(version: str) -> str:
    """``2.41.10`` -> ``2.41``: S4 identifies a series by its first two components.

    Patch releases do not move endpoints or schemas, so a descriptor range is written
    per series and compared per series (DX-CAP-005).
    """
    parts = version.strip().split(".")
    return ".".join(parts[:2]) if len(parts) >= 2 else version.strip()


@dataclass(frozen=True)
class ProfileView:
    """One Profile read for one compile: comparisons, never decisions elsewhere.

    The verdicts and difference lists are memoized per ``(dhis2Type, property)``, because
    a compile asks this for every field of every object and a package holds thousands of
    them (BP-NFR-01, NFR-CAP-001).
    """

    profile: CapabilityProfile
    _verdicts: dict[tuple[str, str], FieldVerdict] = field(
        default_factory=dict, repr=False, compare=False
    )
    _differences: dict[str, list[dict[str, Any]]] = field(
        default_factory=dict, repr=False, compare=False
    )

    @property
    def profile_version(self) -> str:
        return self.profile.profile_version

    @property
    def dhis2_version(self) -> str:
        return self.profile.version

    @property
    def dhis2_series(self) -> str:
        return series(self.profile.version)

    @property
    def translation_property(self) -> str:
        """The property this version reads translations from."""
        return self.profile.translation_format or BASELINE_TRANSLATION_FORMAT

    @property
    def sharing_property(self) -> str:
        """The property this version reads a complete-object share from."""
        return self.profile.sharing_format or BASELINE_SHARING_FORMAT

    def uncomparable_fields(self, dhis2_type: str) -> frozenset[str]:
        """Properties that carry no information about a managed change, in this version.

        S4 excludes exactly this set when it computes the field level of its own Diff
        (DX-DSC-007): a rewritten ``lastUpdated`` is not a change to anything the project
        manages.  S2 has to use the same set or the two Diffs of one deploy disagree.
        """
        return frozenset(self.profile.volatile_fields) | frozenset(
            self.profile.forbidden_fields(dhis2_type)
        )

    def supports(self, descriptor: TypeDescriptor) -> bool:
        """Whether this descriptor's version range covers the target series."""
        return descriptor.accepts_version(self.dhis2_series)

    def field(self, dhis2_type: str, name: str) -> FieldVerdict:
        """What this version says about one payload property."""
        key = (dhis2_type, name)
        cached = self._verdicts.get(key)
        if cached is not None:
            return cached
        verdict = self._read_field(dhis2_type, name)
        self._verdicts[key] = verdict
        return verdict

    def _read_field(self, dhis2_type: str, name: str) -> FieldVerdict:
        for candidate in (f"{dhis2_type}.{name}", name):
            if self.profile.is_removed("field", candidate):
                return FieldVerdict(name, REMOVED, f"removed in {self.dhis2_series}")
            if self.profile.is_deprecated("field", candidate):
                return FieldVerdict(name, DEPRECATED, f"deprecated in {self.dhis2_series}")
        schema = self.profile.object_schema(dhis2_type)
        if schema is not None and name in set(schema.forbidden):
            return FieldVerdict(name, FORBIDDEN, f"{self.profile_version} forbids it in a payload")
        return FieldVerdict(name, SUPPORTED, "")

    # --- enforcement --------------------------------------------------------

    def type_issues(self, descriptor: TypeDescriptor) -> list[Issue]:
        """A type this version does not have cannot be compiled to it (BP-CMP-030)."""
        if self.supports(descriptor):
            return []
        low, high = descriptor.version_range
        return [
            make_issue(
                RULE_TYPE_OUT_OF_RANGE,
                IssueLevel.BLOCKING,
                f"{descriptor.dhis2_type} is compiled from the descriptor for DHIS2 "
                f"{low}..<{high}, but this environment runs {self.dhis2_series}; the object "
                "would be imported against a type that version does not offer",
                dhis2_type=descriptor.dhis2_type,
                section=Section.REGISTRY,
                remediation="pick a Profile for the target series, or add the descriptor "
                "range this version needs (FR-CAP-001 adds versions as data)",
            )
        ]

    def field_issues(
        self, descriptor: TypeDescriptor, business_key: str, name: str
    ) -> list[Issue]:
        """A property the target version removed or forbids must not reach the payload."""
        verdict = self.field(descriptor.dhis2_type, name)
        if verdict.verdict == SUPPORTED:
            return []
        if verdict.verdict == DEPRECATED:
            return [
                make_issue(
                    RULE_FIELD_DEPRECATED,
                    IssueLevel.WARNING,
                    f"{descriptor.dhis2_type} {business_key}: {verdict.detail}, so the value "
                    "is still sent but the instance may stop accepting it in a later version",
                    business_key=business_key,
                    dhis2_type=descriptor.dhis2_type,
                    location=name,
                    section=Section.MAPPING,
                    remediation="move the value to the replacement property this Profile offers",
                )
            ]
        level = IssueLevel.BLOCKING
        rule = RULE_FIELD_REMOVED if verdict.verdict == REMOVED else RULE_FIELD_FORBIDDEN
        return [
            make_issue(
                rule,
                level,
                f"{descriptor.dhis2_type} {business_key}: {verdict.detail}, so "
                f"{name!r} cannot be compiled into this environment's payload",
                business_key=business_key,
                dhis2_type=descriptor.dhis2_type,
                location=name,
                section=Section.MAPPING,
                remediation="drop the field from the mapping, or target a version that has it",
            )
        ]

    def constraint_issues(self) -> list[Issue]:
        """The Profile's rules about S2's own output, at the severity the Profile states.

        A rule this compile cannot speak to is left for its owner: S4 evaluates the
        execution-side rules against the same Profile, so reporting them here would
        double-book a check and let a false pass stand in for a real one.
        """
        rules = self.profile.validation_rules.get("rules")
        if not isinstance(rules, dict):
            return []
        issues: list[Issue] = []
        for rule_id in sorted(rules):
            rule = rules.get(rule_id)
            check = _IMPORT_CONSTRAINTS.get(rule_id)
            if check is None or not isinstance(rule, dict):
                continue
            failing = check(self, rule_id, rule)
            if failing is not None:
                issues.append(failing)
        return issues

    # --- what the Diff shows ------------------------------------------------

    def differences(self, descriptor: TypeDescriptor) -> list[dict[str, Any]]:
        """Every disagreement between the shipped descriptor and this environment.

        These are recorded whether or not they block, because AC-CAP-01 asks for the
        difference to be *visible*: a reviewer comparing two environments' Diffs must
        be able to tell a Profile-driven change from a Blueprint change.
        """
        cached = self._differences.get(descriptor.dhis2_type)
        if cached is not None:
            return cached
        out = self._read_differences(descriptor)
        self._differences[descriptor.dhis2_type] = out
        return out

    def _read_differences(self, descriptor: TypeDescriptor) -> list[dict[str, Any]]:
        schema = self.profile.object_schema(descriptor.dhis2_type)
        if schema is None:
            # the Profile says nothing about this type, so the shipped descriptor decided
            # it: that is a coverage fact, not a difference, and listing it would bury the
            # few entries that actually say this version changed something
            return []
        out = [
            difference(
                "REQUIRED_ADDED",
                descriptor.dhis2_type,
                name,
                f"{self.profile_version} requires it, the descriptor baseline does not",
            )
            for name in sorted({str(item) for item in schema.required} - set(descriptor.required_fields))
        ]
        out += [
            difference(
                "REQUIRED_DROPPED",
                descriptor.dhis2_type,
                name,
                f"the descriptor baseline requires it, {self.profile_version} does not",
            )
            for name in sorted(set(descriptor.required_fields) - {str(item) for item in schema.required})
        ]
        out += [
            difference(
                "FIELD_FORBIDDEN",
                descriptor.dhis2_type,
                name,
                f"{self.profile_version} rejects it in a payload",
            )
            for name in sorted(
                {str(item) for item in schema.forbidden} - set(descriptor.forbidden_fields)
            )
        ]
        out += [
            difference(
                "FIELD_UNCONTROLABLE",
                descriptor.dhis2_type,
                name,
                f"the descriptor baseline manages it, {self.profile_version} does not "
                "list it as a controlable field",
            )
            for name in sorted(
                set(descriptor.controlable_fields) - {str(item) for item in schema.controlable_fields}
            )
        ]
        out += [
            difference(
                "FIELD_CONTROLABLE",
                descriptor.dhis2_type,
                name,
                f"{self.profile_version} exposes it as a controlable field this environment "
                "may write",
            )
            for name in sorted(
                {str(item) for item in schema.controlable_fields} - set(descriptor.controlable_fields)
            )
        ]
        return out

    def format_differences(self) -> list[dict[str, Any]]:
        """Where this version stores translations and sharing differently."""
        out: list[dict[str, Any]] = []
        if self.profile.translation_format != BASELINE_TRANSLATION_FORMAT:
            out.append(
                difference(
                    "TRANSLATION_FORMAT",
                    None,
                    self.profile.translation_format,
                    f"the descriptor baseline writes {BASELINE_TRANSLATION_FORMAT!r}; this "
                    f"environment writes {self.profile.translation_format!r}",
                )
            )
        if self.profile.sharing_format != BASELINE_SHARING_FORMAT:
            out.append(
                difference(
                    "SHARING_FORMAT",
                    None,
                    self.profile.sharing_format,
                    f"the descriptor baseline writes {BASELINE_SHARING_FORMAT!r}; this "
                    f"environment writes {self.profile.sharing_format!r}",
                )
            )
        return out

    def import_default(self, name: str) -> Any:
        """One default the Profile states for the metadata import."""
        section: Any = self.profile.import_parameters
        for step in IMPORTED_DEFAULTS_PATH:
            if not isinstance(section, dict):
                return None
            section = section.get(step)
        if not isinstance(section, dict):
            return None
        return section.get(name)


def _issue_for(
    view: ProfileView, rule_id: str, rule: dict[str, Any], message: str
) -> Issue:
    description = rule.get("description")
    return make_issue(
        RULE_IMPORT_CONSTRAINT,
        severity_of(rule),
        f"{rule_id}: {message}",
        section=Section.REGISTRY,
        remediation="the Profile states this constraint for the target version; the "
        "compile must satisfy it or the import will not mean what the Diff says",
        details={
            "profileVersion": view.profile_version,
            "rule": str(description) if isinstance(description, str) else rule_id,
        },
    )


def _requires_assigned_identifier(
    view: ProfileView, rule_id: str, rule: dict[str, Any]
) -> Issue | None:
    """S2 addresses every object by an assigned UID, so the import must accept that."""
    stated = view.import_default("identifier")
    if stated is None or str(stated).upper() == "ASSIGNED":
        return None
    return _issue_for(
        view,
        rule_id,
        rule,
        f"this Profile imports metadata addressed by identifier={stated!r}, but every UID "
        "in the payload was allocated by S2 and the drift check and post-verification both "
        "address objects by that UID",
    )


def _atomic_by_default(view: ProfileView, rule_id: str, rule: dict[str, Any]) -> Issue | None:
    stated = view.import_default("atomicMode")
    if stated is None or str(stated).upper() == "ALL":
        return None
    return _issue_for(
        view,
        rule_id,
        rule,
        f"this Profile defaults to atomicMode={stated!r}, so part of one compile can take "
        "effect and the Diff no longer describes what the instance holds",
    )


_IMPORT_CONSTRAINTS: dict[str, Any] = {
    "metadata.import.requiresUidIdentifier": _requires_assigned_identifier,
    "metadata.import.atomicByDefault": _atomic_by_default,
}


def view_of(profile: CapabilityProfile | None) -> ProfileView | None:
    """The read-side lens for one compile; None when no Profile was given.

    A baseline compile has no target environment, so nothing here may constrain it —
    the products must stay environment-independent (D-01, AC-ENV-01).
    """
    return ProfileView(profile) if profile is not None else None
