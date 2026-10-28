"""The Prompt Registry reads its sources (AI-REG-001, AI-REG-002).

Nothing here calls a model: what is pinned is that a prompt version exists as *one file with
two halves* — prose a reviewer diffs, and a declaration a program enforces — and that reading
that file is enough to register it.  AI-REG-001 asks for both halves plus a content hash that
names the bytes they came from, so the positive assertions are made against the shipped tree
rather than against a fixture somebody could keep in step by hand, and the malformed cases are
the price of the structured half being structured: a header that parses into nothing must not
become a version with no prohibitions and a legitimate-looking hash.

``reload_sources`` belongs here because it is the load step of the same requirement — a
version that cannot be re-registered after a wheel upgrade, or that takes effect merely by
existing, breaks AI-REG-002.  Switching a version needs evaluation evidence and is pinned in
``tests/integration/test_prompt_activation.py``.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest
from factory_foundation.api import (
    NotFoundError,
    content_hash,
    has_active_uow,
    unit_of_work,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_intelligence.agents.spec import AGENT_SPECS
from factory_intelligence.enums import AgentType
from factory_intelligence.errors import PromptNotFound
from factory_intelligence.models.registry import PromptActivationRow, PromptVersionRow
from factory_intelligence.prompts.registry import (
    PROMPT_SOURCE_ROOT,
    PromptDeclaration,
    discover_sources,
    get_prompt,
    list_prompts,
    parse_source,
    prompt_body,
    reload_sources,
    split_qualified_prompt,
)
from factory_intelligence.testing.harness import IntelligenceHarness

#: The agents whose prompt **and** evaluation set ship in this build.  SRS 12 puts REVIEW,
#: API, UI and Fixture in M3 with in-stage chat and the page agents; REVIEW ships because
#: AI-CHAT-001 answers through it, and the PAGE_DESIGN three ship because the stage is on the
#: current design line and mode-B HANDOFF depends on it (see
#: :data:`factory_intelligence.feature.DELIVERED_AGENTS`).
SHIPPED_AGENTS: tuple[AgentType, ...] = (
    AgentType.REQUIREMENTS,
    AgentType.DOMAIN,
    AgentType.MAPPING,
    AgentType.METADATA_DESIGN,
    AgentType.API,
    AgentType.UI,
    AgentType.FIXTURE,
    AgentType.REVIEW,
)

#: Every packaged source, spelled out.  A literal rather than a walk of the tree, because the
#: tests below assert that start-up registered *these* files: deriving the expectation from
#: ``discover_sources()`` would make the assertion circular (AI-REG-002).
SHIPPED_QUALIFIED_NAMES: frozenset[str] = frozenset(
    {
        "requirements.consolidate.v1",
        "domain.model.v1",
        "mapping.decide.v1",
        "metadata_design.expression.v1",
        "api.design.v1",
        "ui.design.v1",
        "fixture.specify.v1",
        "review.explain.v1",
        "review.explain.v2",
    }
)

FRONT = "+++"
BODY = "# Deciding the mapping\n\nSay what each element becomes, and never write a uid.\n"
HEADER = "\n".join(
    [
        'name = "mapping.decide"',
        "version = 3",
        'agent_type = "MAPPING"',
        'stages = ["MAPPING"]',
        'input_schema_ref = "agent-context"',
        'output_schema_ref = "agent-output"',
        'blueprint_schema_range = ">=1"',
        'dhis2_version_range = ">=2.40"',
        'system_rules = """\nDecide the mapping; a human approves it.\n"""',
        "",
        "[[prohibitions]]",
        'id = "uid_ban"',
        'description = """\nNever write a uid in any patch value.\n"""',
    ]
)
#: A declaration that puts its prohibitions in an inline array instead of tables.
FLAT_PROHIBITIONS = HEADER.split("[[prohibitions]]")[0] + 'prohibitions = ["never a uid"]\n'


class _Rollback(RuntimeError):
    """Raised inside a caller's transaction to prove registration left it alone."""


def header_saying(key: str, line: str) -> str:
    """One declaration line replaced, everything else as written above."""
    return "\n".join(line if item.startswith(f"{key} =") else item for item in HEADER.splitlines())


def source(header: str = HEADER, body: str = BODY) -> str:
    return f"{FRONT}\n{header}\n{FRONT}\n{body}"


def source_at_version(version: int, *, name: str = "mapping.decide") -> str:
    header = header_saying("version", f"version = {version}").replace(
        'name = "mapping.decide"', f'name = "{name}"', 1
    )
    return source(header)


def write_source(root: Path, agent_type: AgentType, text: str, filename: str) -> Path:
    directory = root / agent_type.value.lower()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(text, encoding="utf-8")
    return path


def registered_names(session: Session) -> set[str]:
    return {
        f"{row.name}.v{row.version}"
        for row in session.execute(select(PromptVersionRow)).scalars().all()
    }


def agent_types_activated(session: Session) -> list[str]:
    return [row.agent_type for row in session.execute(select(PromptActivationRow)).scalars()]


# --- one file, two halves (AI-REG-001) --------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "requirements/requirements.consolidate.v1.md",
        "domain/domain.model.v1.md",
        "mapping/mapping.decide.v1.md",
        "metadata_design/metadata_design.expression.v1.md",
        "api/api.design.v1.md",
        "ui/ui.design.v1.md",
        "fixture/fixture.specify.v1.md",
        "review/review.explain.v1.md",
        "review/review.explain.v2.md",
    ],
    ids=[
        "requirements.consolidate.v1",
        "domain.model.v1",
        "mapping.decide.v1",
        "metadata_design.expression.v1",
        "api.design.v1",
        "ui.design.v1",
        "fixture.specify.v1",
        "review.explain.v1",
        "review.explain.v2",
    ],
)
def test_a_shipped_source_carries_the_prose_and_the_declaration(relative: str) -> None:
    """AI-REG-001: every version declares its input and output schema, its Blueprint schema
    range, its DHIS2 range, its stages and its prohibitions — as data, beside the prose that
    says the same things in words."""
    text = (PROMPT_SOURCE_ROOT / relative).read_text(encoding="utf-8")
    declaration = parse_source(text, path=relative)

    directory, filename = relative.split("/")
    assert declaration.agent_type == directory.upper()
    assert declaration.source_path == relative
    assert filename == f"{declaration.name}.v{declaration.version}.md"
    assert declaration.qualified_name == filename.removesuffix(".md")

    assert declaration.input_schema_ref, "AI-REG-001 asks for the input schema"
    assert declaration.output_schema_ref == "agent-output"
    assert declaration.blueprint_schema_range.startswith(">=")
    assert declaration.dhis2_version_range
    assert declaration.applicable_stages
    assert declaration.system_rules.strip()

    assert declaration.prohibitions, "the structured half is not optional"
    for prohibition in declaration.prohibitions:
        assert set(prohibition) >= {"id", "description"}
        assert str(prohibition["description"]).strip()

    assert declaration.body.startswith("# "), "the prose half must read as prose"
    assert FRONT not in declaration.body
    assert 'name = "' not in declaration.body, "the body must not carry the declaration"
    assert declaration.source_hash.startswith("sha256:")


@pytest.mark.parametrize(
    "relative",
    [
        "requirements/requirements.consolidate.v1.md",
        "domain/domain.model.v1.md",
        "mapping/mapping.decide.v1.md",
        "metadata_design/metadata_design.expression.v1.md",
        "api/api.design.v1.md",
        "ui/ui.design.v1.md",
        "fixture/fixture.specify.v1.md",
    ],
    ids=[
        "requirements.consolidate.v1",
        "domain.model.v1",
        "mapping.decide.v1",
        "metadata_design.expression.v1",
        "api.design.v1",
        "ui.design.v1",
        "fixture.specify.v1",
    ],
)
def test_a_question_prompt_asks_for_a_recommended_answer(relative: str) -> None:
    """UI-Q-001: the review screen adopts the model's recommendation in one click, so every
    question must arrive with complete candidate answers and an explicit recommendation."""
    text = (PROMPT_SOURCE_ROOT / relative).read_text(encoding="utf-8")
    body = parse_source(text, path=relative).body
    assert "2–5 `suggestions`" in body
    assert "recommended: true" in body
    assert "exactly one" in body, "a SINGLE question needs one recommendation"
    assert "at least one" in body, "a MULTIPLE question needs at least one recommendation"
    assert "DHIS2" in body, "the options must be grounded in DHIS2 modelling, not generic prose"


def test_review_explain_v2_is_explain_only_and_carries_the_answer_envelope() -> None:
    """AI-AG-008 read against AI-RT-013: the Review Assistant is ``EXPLAIN_ONLY`` — its spec
    reports ``accepts_patch()`` False — so its v2 prompt must stop guiding the model to author a
    patch (v1 told it to return one against the draft) and must state the shared five-key answer
    envelope with an always-empty ``patch``.

    The v1 file is left in place untouched so an already-activated version keeps the hash it was
    measured against; this test is the guard that v2 no longer misleads the model.
    """
    text = (PROMPT_SOURCE_ROOT / "review" / "review.explain.v2.md").read_text(encoding="utf-8")
    declaration = parse_source(text, path="review/review.explain.v2.md")
    body = declaration.body

    assert declaration.version == 2 and declaration.agent_type == "REVIEW"
    assert "## The answer" in body, "the five-key envelope section is present"
    for key in ("summary", "decisions", "patch", "questions", "confidence"):
        assert f'"{key}"' in body, f"the envelope names {key}"

    lowered = body.lower()
    assert "explain-only" in lowered, "the answer says plainly that this agent explains only"
    assert "return it in" not in lowered, "v2 must not tell the model to author a patch"
    assert "return a patch" not in lowered
    assert "patch.operations` is always the empty array" in body


def test_the_stored_hash_covers_the_declaration_and_the_prose_together() -> None:
    """A registry row is traceable to a release only if its hash is of the whole file: an
    edit to the header alone has to move it, while leaving the prose alone (AI-REG-001)."""
    declaration = parse_source(source())
    assert declaration.source_hash == content_hash(source().encode("utf-8"))

    edited_header = parse_source(source(header_saying("version", "version = 4")))
    assert edited_header.body == declaration.body
    assert edited_header.source_hash != declaration.source_hash

    edited_prose = parse_source(source(body=BODY + "One more sentence.\n"))
    assert edited_prose.prohibitions == declaration.prohibitions
    assert edited_prose.source_hash != declaration.source_hash


def test_the_prose_is_stored_as_written_and_the_hash_is_reproducible() -> None:
    """The body a model is sent is the body of the file, so a prompt review reads the words
    the runtime sends; only the blank lines the delimiters need are the parser's."""
    declaration = parse_source(source(HEADER, "\n\n# Heading\n\nLine one.\n\n"))
    assert declaration.body == "# Heading\n\nLine one."
    assert parse_source(source()).source_hash == parse_source(source()).source_hash


@pytest.mark.parametrize("agent_type", SHIPPED_AGENTS, ids=[a.value.lower() for a in SHIPPED_AGENTS])
def test_what_a_prompt_forbids_in_words_is_what_the_runtime_checks(
    agent_type: AgentType,
) -> None:
    """AI-REG-001 read with AI-RT-012: the prohibition list *is* the constraint-check list.
    Two names for one fact drift apart, and only the check would remember."""
    declarations = [each for each in discover_sources() if each.agent_type == agent_type.value]
    assert declarations, f"{agent_type.value} ships no prompt source"
    for declaration in declarations:
        declared = {str(item["id"]) for item in declaration.prohibitions}
        enforced = set(AGENT_SPECS[agent_type].checks)
        assert declared == enforced, (
            f"{declaration.qualified_name} forbids {sorted(declared)} while the "
            f"{agent_type.value} spec checks {sorted(enforced)} (AI-REG-001, AI-RT-012)"
        )


# --- the qualified spelling (AI-REG-001, SRS 7) ------------------------------------


@pytest.mark.parametrize(
    ("qualified", "expected"),
    [
        ("mapping.decide.v3", ("mapping.decide", 3)),
        ("requirements.consolidate.v12", ("requirements.consolidate", 12)),
        ("metadata_design.expression.v1", ("metadata_design.expression", 1)),
        ("agent.v2.model.v7", ("agent.v2.model", 7)),
        ("legacy.v1.v1", ("legacy.v1", 1)),
    ],
    ids=[
        "mapping-decide-v3",
        "dotted-name-v12",
        "metadata-design-expression-v1",
        "name-holding-a-version-looking-segment",
        "repeated-version-segment",
    ],
)
def test_split_qualified_prompt_takes_only_the_trailing_version(
    qualified: str, expected: tuple[str, int]
) -> None:
    """SRS 7 hands one string where the registry keys on two columns, and a prompt name may
    contain dots, so only a trailing ``.v<digits>`` is a version (AI-REG-001)."""
    assert split_qualified_prompt(qualified) == expected


@pytest.mark.parametrize(
    "qualified",
    [
        "mapping.decide",
        "mapping.decide.",
        "mapping.decide.v",
        "mapping.decide.vX",
        "mapping.decide.v-3",
        "mapping.decide.v3x",
        "mapping.decide.v1.2",
        "mapping.decide.V3",
        "v3",
        "",
    ],
    ids=[
        "no-version",
        "trailing-dot",
        "missing-digits",
        "non-numeric-version",
        "negative-version",
        "version-with-a-suffix",
        "dotted-version",
        "capital-v",
        "version-without-a-name",
        "empty-string",
    ],
)
def test_a_string_that_is_not_a_qualified_version_is_refused_before_lookup(
    qualified: str,
) -> None:
    with pytest.raises(PromptNotFound) as refused:
        split_qualified_prompt(qualified)
    assert refused.value.code == "AI_PROMPT_NOT_FOUND"
    assert refused.value.details["qualified"] == qualified
    assert ".v<int>" in str(refused.value)


# --- malformed declarations --------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("# Just prose\n\nNo header at all.\n", "no TOML front matter"),
        (f"{FRONT}\n{HEADER}\n", "not closed"),
        (source("\n".join(i for i in HEADER.splitlines() if not i.startswith("name ="))), "'name'"),
        (
            source(
                "\n".join(i for i in HEADER.splitlines() if not i.startswith("agent_type ="))
            ),
            "'agent_type'",
        ),
        (
            source(
                "\n".join(i for i in HEADER.splitlines() if not i.startswith("version ="))
            ),
            "version must be an integer",
        ),
        (source(header_saying("version", 'version = "three"')), "version must be an integer"),
        (source(header_saying("version", "version = 0")), "version must be an integer"),
        (source(header_saying("stages", 'stages = "MAPPING"')), "'stages'"),
        (source(FLAT_PROHIBITIONS), "[[prohibitions]]"),
        (source(header_saying("input_schema_ref", "input_schema_ref = 12")), "'input_schema_ref'"),
        (source(header_saying("output_schema_ref", "output_schema_ref = 12")), "'output_schema_ref'"),
    ],
    ids=[
        "no-front-matter",
        "front-matter-never-closed",
        "missing-name",
        "missing-agent-type",
        "missing-version",
        "version-is-a-string",
        "version-below-one",
        "stages-not-a-list",
        "prohibitions-as-inline-strings",
        "input-schema-ref-not-a-string",
        "output-schema-ref-not-a-string",
    ],
)
def test_a_malformed_declaration_is_refused_rather_than_registered_thinly(
    text: str, fragment: str
) -> None:
    """AI-REG-001: a header that silently parses into no prohibitions would register a
    version whose constraints are missing and whose hash still looks legitimate."""
    with pytest.raises(ValueError) as refused:
        parse_source(text, path="mapping/mapping.decide.v3.md")
    assert fragment in str(refused.value)


def test_a_refusal_names_the_file_it_came_from() -> None:
    """A tree of four agents and several versions is walked at start-up; the operator has to
    know which file to fix (AI-REG-002)."""
    with pytest.raises(ValueError) as refused:
        parse_source(source(header_saying("stages", "stages = 3")), path="mapping/x.md")
    assert "mapping/x.md" in str(refused.value)
    with pytest.raises(ValueError) as anonymous:
        parse_source("no header")
    assert "<string>" in str(anonymous.value)


def test_an_agent_type_nobody_declared_is_refused_by_name() -> None:
    """Fail loudly: an unknown agent has no spec, no checks and no model list to run with,
    so the file names the agent that is wrong."""
    with pytest.raises(ValueError) as refused:
        parse_source(source(header_saying("agent_type", 'agent_type = "SOMETHING_ELSE"')))
    assert "SOMETHING_ELSE" in str(refused.value)
    assert not hasattr(refused.value, "code"), "this is a source fault, not a service error"


def test_a_boolean_version_is_not_a_version() -> None:
    """``version = true`` parses in TOML and satisfies ``>= 1`` as a Python int, which would
    register a file whose qualified name reads ``mapping.decide.vTrue`` onto v1's row — the
    one declaration half a program is supposed to enforce (AI-REG-001)."""
    with pytest.raises(ValueError):
        parse_source(source(header_saying("version", "version = true")))


def test_a_declaration_saved_with_a_byte_order_mark_still_parses() -> None:
    """Sources are edited by people whose editors prepend U+FEFF; the front matter is still
    there and refusing the file would lose a whole version (AI-REG-002)."""
    declaration = parse_source("\ufeff" + source())
    assert declaration.qualified_name == "mapping.decide.v3"


def test_an_optional_slot_may_be_absent_but_not_the_wrong_type() -> None:
    without = source("\n".join(i for i in HEADER.splitlines() if not i.startswith("input_schema_ref =")))
    declaration = parse_source(without)
    assert declaration.input_schema_ref is None
    assert declaration.output_schema_ref == "agent-output"
    assert declaration.system_rules.strip()
    with pytest.raises(ValueError):
        parse_source(source(header_saying("system_rules", "system_rules = 5")))


def test_a_declaration_record_is_a_value_object() -> None:
    """The parsed source is frozen data: whatever is judging a prompt must not be able to
    rewrite it while holding it (AI-REG-001)."""
    declaration = parse_source(source())
    assert isinstance(declaration, PromptDeclaration)
    with pytest.raises(FrozenInstanceError):
        declaration.body = "rewritten"  # type: ignore[misc]
    assert declaration.prohibitions[0]["id"] == "uid_ban"


# --- the packaged tree (AI-REG-002) ------------------------------------------------


def test_discover_sources_reads_the_shipped_tree_in_a_stable_order() -> None:
    """AI-REG-002: the sources ship with S3 and registration is a walk of them."""
    declarations = discover_sources()
    assert [each.source_path for each in declarations] == sorted(
        str(path.relative_to(PROMPT_SOURCE_ROOT)) for path in PROMPT_SOURCE_ROOT.rglob("*.md")
    )
    assert {each.agent_type for each in declarations} == {each.value for each in SHIPPED_AGENTS}
    assert [each.qualified_name for each in discover_sources()] == [
        each.qualified_name for each in declarations
    ], "registration order must not depend on the filesystem's mood"

    undelivered = {each.value for each in AgentType} - {each.value for each in SHIPPED_AGENTS}
    assert not [each for each in declarations if each.agent_type in undelivered], (
        "an M3 agent must not ship a prompt nobody has an evaluation set for (AI-EVAL-001)"
    )


def test_discover_sources_over_a_missing_or_empty_root_finds_nothing(tmp_path: Path) -> None:
    assert discover_sources(tmp_path / "absent") == []
    (tmp_path / "empty").mkdir()
    assert discover_sources(tmp_path / "empty") == []


def test_two_versions_of_one_name_are_two_declarations(tmp_path: Path) -> None:
    """AI-REG-001 versions prompts: v2 of a name is a second version, never an overwrite."""
    write_source(tmp_path, AgentType.MAPPING, source_at_version(1), "mapping.decide.v1.md")
    write_source(tmp_path, AgentType.MAPPING, source_at_version(2), "mapping.decide.v2.md")

    declarations = discover_sources(tmp_path)
    assert [each.qualified_name for each in declarations] == [
        "mapping.decide.v1",
        "mapping.decide.v2",
    ]
    assert {each.name for each in declarations} == {"mapping.decide"}
    assert declarations[0].source_path == "mapping/mapping.decide.v1.md"


# --- registration (AI-REG-001, AI-REG-002) -----------------------------------------


def test_registration_stores_both_halves_and_takes_effect_only_through_activation(
    harness: IntelligenceHarness, tmp_path: Path
) -> None:
    """AI-REG-001 stores the body *and* the declaration per version; AI-REG-002 separates "a
    version exists" from "this version is in use", so registering a second version leaves
    ``ai.prompt_activation`` exactly as it was."""
    _ = harness
    write_source(tmp_path, AgentType.MAPPING, source_at_version(2), "mapping.decide.v2.md")

    with unit_of_work() as session:
        before = registered_names(session)
        assert before == SHIPPED_QUALIFIED_NAMES, "initialize() registers the shipped sources (AI-REG-002)"
        # the harness seeds a baseline activation per agent so a run is not refused
        # (AI-REG-002); the fact here is that registering a version *changes none of them*
        activated_before = set(agent_types_activated(session))
        assert activated_before, "the harness seeds activations"

    rows = reload_sources(root=tmp_path)
    assert [f"{row.name}.v{row.version}" for row in rows] == ["mapping.decide.v2"]

    with unit_of_work() as session:
        assert registered_names(session) == before | {"mapping.decide.v2"}
        assert set(agent_types_activated(session)) == activated_before, (
            "registering a version must not activate it (AI-REG-002, AI-REG-003)"
        )
        fresh = get_prompt("mapping.decide", 2, session=session)
        assert fresh.body == BODY.strip()
        assert "a human approves it" in str(fresh.system_rules)
        assert fresh.input_schema_ref == "agent-context"
        assert fresh.output_schema_ref == "agent-output"
        assert fresh.blueprint_schema_range == ">=1"
        assert fresh.dhis2_version_range == ">=2.40"
        assert fresh.applicable_stages == ["MAPPING"]
        assert [item["id"] for item in fresh.prohibitions] == ["uid_ban"]
        assert fresh.source_hash == content_hash(
            (tmp_path / "mapping" / "mapping.decide.v2.md").read_bytes()
        )


def test_reregistering_a_version_refreshes_it_in_place_after_the_file_changed(
    harness: IntelligenceHarness, tmp_path: Path
) -> None:
    """AI-REG-002: a wheel that ships an edited prompt has to be observable — the same
    (name, version) keeps one row, whose body and hash say which bytes it came from."""
    _ = harness
    with unit_of_work() as session:
        original = get_prompt("mapping.decide", 1, session=session)
        original_id, original_hash, original_body = original.id, original.source_hash, original.body

    text = (PROMPT_SOURCE_ROOT / "mapping" / "mapping.decide.v1.md").read_text(encoding="utf-8")
    edited = text.replace("# Deciding the mapping", "# Deciding the mapping, second draft")
    assert edited != text
    write_source(tmp_path, AgentType.MAPPING, edited, "mapping.decide.v1.md")

    reload_sources(root=tmp_path)
    with unit_of_work() as session:
        refreshed = get_prompt("mapping.decide", 1, session=session)
        assert refreshed.id == original_id, "one (name, version) is one row"
        assert refreshed.source_hash != original_hash
        assert refreshed.body != original_body
        assert refreshed.body.startswith("# Deciding the mapping, second draft")
        assert prompt_body(original_id, session=session) == refreshed.body
        assert registered_names(session) == SHIPPED_QUALIFIED_NAMES


def test_reads_see_every_registered_version_newest_first(
    harness: IntelligenceHarness, tmp_path: Path
) -> None:
    """``list_prompts`` is the operator's view of what exists, including the versions nobody
    activated.  Every agent of SRS 3.3 now ships a source, so there is no longer an agent with
    nothing to list — what a version that does not exist does is pinned by
    :func:`test_an_unregistered_version_is_named_as_missing`."""
    _ = harness
    write_source(tmp_path, AgentType.MAPPING, source_at_version(7), "mapping.decide.v7.md")
    reload_sources(root=tmp_path)

    with unit_of_work() as session:
        mapping = list_prompts(AgentType.MAPPING, session=session)
        assert [row.version for row in mapping] == [7, 1], "newest first (AI-REG-001)"
        assert {row.name for row in mapping} == {"mapping.decide"}
        assert len(list_prompts("METADATA_DESIGN", session=session)) == 1
        assert [row.name for row in list_prompts(AgentType.API, session=session)] == [
            "api.design"
        ]
        assert [row.name for row in list_prompts(AgentType.UI, session=session)] == ["ui.design"]
        assert [row.name for row in list_prompts(AgentType.FIXTURE, session=session)] == [
            "fixture.specify"
        ]
        # every shipped version (review.explain ships v1 and v2), plus the v7 this test wrote
        assert len(list_prompts(session=session)) == len(discover_sources()) + 1


def test_an_unregistered_version_is_named_as_missing(harness: IntelligenceHarness) -> None:
    """The registry answers for versions that do not exist, which is what makes a typo in a
    qualified name visible to whoever typed it (AI-REG-001)."""
    _ = harness
    with unit_of_work() as session:
        with pytest.raises(PromptNotFound) as refused:
            get_prompt("mapping.decide", 99, session=session)
        assert refused.value.code == "AI_PROMPT_NOT_FOUND"
        assert refused.value.details == {"name": "mapping.decide", "version": 99}
        with pytest.raises(NotFoundError):
            prompt_body("apvr-never-registered", session=session)


def test_registration_enlists_in_the_callers_transaction_and_commits_nothing(
    harness: IntelligenceHarness, tmp_path: Path
) -> None:
    """00 doc 6.2: S1 may hold a transaction across a governance call, so a registration
    that committed early would survive the rollback that must undo it (AI-REG-002)."""
    _ = harness
    write_source(tmp_path, AgentType.MAPPING, source_at_version(3), "mapping.decide.v3.md")
    with pytest.raises(_Rollback), unit_of_work() as session:
        reload_sources(root=tmp_path, session=session)
        assert has_active_uow()
        assert "mapping.decide.v3" in registered_names(session), "the caller reads its own write"
        raise _Rollback("the caller's transaction failed for an unrelated reason")

    with unit_of_work() as session:
        assert "mapping.decide.v3" not in registered_names(session)
    assert not has_active_uow()


def test_registration_outside_a_transaction_opens_and_closes_its_own(
    harness: IntelligenceHarness,
) -> None:
    """``initialize()`` is called argument-less at process start, so the load step must
    commit by itself and leave nothing open (AI-REG-002)."""
    _ = harness
    assert len(reload_sources()) == len(discover_sources())
    assert not has_active_uow()
    with unit_of_work() as session:
        assert registered_names(session) == {
            f"{each.name}.v{each.version}" for each in discover_sources()
        }
