"""Context construction (AI-RT-003, AI-RT-004, AI-RT-005, AI-RT-006, AI-RT-030).

The order is the requirement, and it is not arbitrary: system rules → stage rules →
approved Blueprint entries → the current input artifacts → relevant Registry entries →
currently open questions.  Reading the order as a *priority* helps when the byte budget
forces a refusal: S3 refuses rather than truncating (AI-POL-003), so the order exists to
make the assembled context deliberate about what a model is told first, not to make
dropping the tail acceptable.

:class:`BuiltContext` is the object the rest of the runtime consumes, and it carries two
things the policy engine needs as separate facts: the **messages** that would be sent, and
the **items** that would be sent with their classifications.  A policy engine that read the
messages would be judging a string, and the whole point of AI-RT-006 is that the judgement
is about identifiable products with known classifications.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from factory_blueprint.api import SelectedEntries
from factory_foundation.api import DataClassification, structure_hash

from factory_intelligence.config import settings
from factory_intelligence.context.fence import fence, plain
from factory_intelligence.context.thresholds import Thresholds
from factory_intelligence.dto.request import AgentRunRequest
from factory_intelligence.enums import ItemSourceKind
from factory_intelligence.errors import ContextContainsPhi, ContextTooLarge
from factory_intelligence.llm.tokens import estimate_messages
from factory_intelligence.llm.types import ChatMessage, ImageInput, ProviderInfo
from factory_intelligence.policy.model import ContextItem
from factory_intelligence.prompts.render import system_message, task_message

#: ``registry`` is S2's own section name; a Registry entry is what an agent cites by name,
#: so it is a segment of its own in AI-RT-003's order rather than part of "blueprint entries".
REGISTRY_SECTION = "registry"
#: the section whose entries are the Blueprint's decision records (FR-MAP-001, BP-DEC-006)
ARCHITECTURE_SECTION = "architecture"


@dataclass
class BuiltContext:
    """A context that is ready to be judged, and then sent."""

    messages: list[ChatMessage] = field(default_factory=list)
    items: list[ContextItem] = field(default_factory=list)
    excluded_phi: list[str] = field(default_factory=list)
    #: every question already open on the stage, so the agent does not re-ask (AI-RT-003)
    open_questions: list[dict[str, Any]] = field(default_factory=list)
    byte_size: int = 0
    token_estimate: int = 0

    @property
    def needs_vision(self) -> bool:
        return any(message.images for message in self.messages)

    def digest(self) -> str:
        """Hash of the message sequence — what a resume compares against (AI-RT-015)."""
        return structure_hash(
            [
                {
                    "role": message.role,
                    "content": message.content,
                    "images": [image.label for image in message.images],
                }
                for message in self.messages
            ]
        )

    def artifact_refs(self) -> list[str]:
        return [item.ref for item in self.items]

    def classifications(self) -> list[DataClassification]:
        return [item.classification for item in self.items]


@dataclass(frozen=True)
class InputArtifact:
    """One input product, already read (reading it is the runtime's external-I/O step)."""

    artifact_id: str
    classification: DataClassification
    content: str
    media_type: str = "application/json"
    content_hash: str | None = None
    images: tuple[ImageInput, ...] = ()

    @property
    def is_image(self) -> bool:
        return bool(self.images)


def build(
    *,
    request: AgentRunRequest,
    active_prompt: Any,
    stage_rules: str,
    entries: SelectedEntries | None,
    inputs: Sequence[InputArtifact],
    thresholds: Thresholds,
    open_questions: Sequence[Mapping[str, Any]] = (),
    similar_records: Sequence[Mapping[str, Any]] = (),
    validation_errors: str | None = None,
    entries_classification: DataClassification = DataClassification.INTERNAL,
    max_bytes: int | None = None,
) -> BuiltContext:
    """Assemble the context in AI-RT-003's order.

    ``active_prompt`` is the :class:`~factory_intelligence.prompts.activation.ActivePrompt`
    the run resolved at start-up; it is passed in rather than looked up here so a corrective
    retry provably reuses the same prompt version (AI-RT-001).
    """
    context = BuiltContext()
    budget = max_bytes or settings().max_context_bytes

    _push(
        context,
        plain(
            kind=ItemSourceKind.SYSTEM_RULES,
            text=system_message(
                active_prompt, language=getattr(request, "language", None)
            ).content,
        ),
        ref=f"prompt:{getattr(active_prompt, 'qualified_name', 'system')}",
        classification=DataClassification.INTERNAL,
        kind=ItemSourceKind.SYSTEM_RULES,
        label="system rules",
    )
    # AI-RT-001 requires each run to carry *both* halves of the prompt -- the system rules and
    # the task prompt -- and only the system half was ever sent.  The task body is the prose
    # that tells the agent what to decide; without it the model gets governance scaffolding but
    # none of the authored instructions, so a prompt measured against its task prompt in
    # evaluation (eval/runner.case_messages sends it) would never actually run with it.  An
    # un-activated placeholder has an empty body, which is skipped exactly as before.
    task_prompt = getattr(active_prompt, "body", "") or ""
    if task_prompt.strip():
        _push(
            context,
            task_message(task_prompt),
            ref=f"prompt:{getattr(active_prompt, 'qualified_name', 'task')}#task",
            classification=DataClassification.INTERNAL,
            # authored by S3, so it is trusted and never fenced -- the same treatment as the
            # system rules it belongs with (AI-RT-030)
            kind=ItemSourceKind.SYSTEM_RULES,
            label="task prompt",
        )
    if stage_rules.strip():
        _push(
            context,
            plain(kind=ItemSourceKind.STAGE_RULES, text=f"Stage rules:\n{stage_rules.strip()}"),
            ref=f"stage:{request.stage}",
            classification=DataClassification.INTERNAL,
            kind=ItemSourceKind.STAGE_RULES,
            label=f"{request.stage} rules",
        )
    # the thresholds are stage rules too (they come from the project's own
    # ``generationPolicy``), so they belong here and not after the questions
    _add_thresholds(context, thresholds)

    _add_entries(context, entries, entries_classification)
    _add_inputs(context, inputs)
    # AI-RT-003 puts the Registry entries after the current input and before the open
    # questions: they are the approved names a decision must reuse, not the material being
    # decided about.  They are a segment of their own because a Registry entry is what an
    # agent cites by name, and mixing them into "blueprint entries" hides that.
    _add_registry(context, entries, entries_classification)
    _add_similar(context, similar_records)
    _add_questions(context, open_questions)
    if validation_errors:
        _push(
            context,
            plain(
                kind=ItemSourceKind.VALIDATION_ERRORS,
                text=(
                    "Your previous answer was refused. Return one JSON object that fixes "
                    "exactly these findings, changing nothing else:\n" + validation_errors
                ),
            ),
            ref="s3:validation-errors",
            classification=DataClassification.INTERNAL,
            kind=ItemSourceKind.VALIDATION_ERRORS,
            label="validation feedback",
        )

    if context.byte_size > budget:
        raise ContextTooLarge(
            f"the constructed context is {context.byte_size} bytes over a limit of {budget}; "
            "narrow the run's scope or raise ai.max_context_bytes — S3 will not truncate a "
            "context in order to send it (AI-POL-003)",
            details={
                "bytes": context.byte_size,
                "limit": budget,
                "items": len(context.items),
                "largest": [
                    (item.ref, item.size_bytes)
                    for item in sorted(context.items, key=lambda each: -each.size_bytes)[:5]
                ],
            },
        )
    context.token_estimate = estimate_messages(context.messages)
    return context


def _push(
    context: BuiltContext,
    message: ChatMessage,
    *,
    ref: str,
    classification: DataClassification,
    kind: ItemSourceKind,
    label: str,
) -> None:
    if classification is DataClassification.PHI_PII:
        # AC-POL-02: refused and reported, never quietly dropped.  An empty
        # ``excluded_phi`` would let a run look like it had no PHI to begin with.
        context.excluded_phi.append(ref)
        return
    size = len(message.content.encode("utf-8")) + sum(
        len(image.data_base64) for image in message.images
    )
    context.messages.append(message)
    context.items.append(
        ContextItem(
            ref=ref,
            classification=classification,
            kind=kind,
            label=label,
            size_bytes=size,
            is_image=bool(message.images),
        )
    )
    context.byte_size += size


def entry_section(entry: Mapping[str, Any]) -> str:
    """The section one selection item belongs to, in whichever spelling S2 used."""
    return str(entry.get("section") or entry.get("_section") or "")


def entry_body(entry: Mapping[str, Any]) -> dict[str, Any]:
    """The entry's own fields.

    S2 answers an ordinary entry as ``{**fields, "_section", "_path"}`` and a whole-object
    section as ``{"section", "value"}``.  Reading only one of the two shapes silently yields
    an empty body — which for a *judgement* (which object resembles which) is worse than an
    error, because it looks like "nothing resembles anything".
    """
    body = entry.get("value")
    if isinstance(body, dict):
        return body
    return {name: value for name, value in entry.items() if not name.startswith("_")}


def registered_decision_ids(entries: SelectedEntries | None) -> tuple[str, ...]:
    """The decision ids the Blueprint this run read already registers as ADRs.

    ``/architecture/decisions`` is where S2 indexes a ``decisionId``, so a mapping or Registry
    entry may cite one of these without the patch repeating the record (BP-VAL-010).  Read
    here rather than inside the check, because a constraint check is a pure function of its
    context and must not perform I/O.
    """
    if entries is None:
        return ()
    out: set[str] = set()
    for entry in entries.entries:
        if entry_section(entry) != ARCHITECTURE_SECTION:
            continue
        decision_id = entry_body(entry).get("decisionId")
        if isinstance(decision_id, str) and decision_id:
            out.add(decision_id)
    return tuple(sorted(out))


def continue_transcript(
    context: BuiltContext,
    prior: Sequence[ChatMessage],
    *,
    classification: DataClassification,
    conversation_start: int | None = None,
) -> None:
    """Add the conversation this run already had, before this turn's answers (AI-RT-015).

    "S3 continues in the same run context" is not a claim about re-reading the same files:
    the model has to see what it said last turn and what it was refused for.  ``prior`` is
    the *stored* transcript, which for a run starts with the context that was built last
    turn.  That prefix is rebuilt from the current sources, so appending it again would send
    two prompts, two sets of stage rules and two copies of every entry — and would let an
    outdated rule outvote the current one.  ``conversation_start`` is the index where the
    stored transcript's conversation begins; only that tail is appended.  Chat callers pass
    no boundary because their ``prior`` is already just the dialogue.
    """
    start = 0 if conversation_start is None else max(0, conversation_start)
    for index, message in enumerate(prior[start:], start=start):
        if conversation_start is None and index == 0 and message.role == "system":
            continue
        _push(
            context,
            message,
            ref=f"transcript:{index}",
            # the transcript carries what was already sent, so it keeps the classification
            # the stored context was written at rather than this function's opinion
            classification=classification,
            kind=ItemSourceKind.USER_ANSWER,
            label=f"prior {message.role}",
        )


def _entry_items(
    entries: SelectedEntries | None, *, registry: bool
) -> list[tuple[str, str, str, Mapping[str, Any]]]:
    """The selected entries as ``(section, key, text, body)``, split by registry or not.

    AI-RT-003's order makes the Registry a segment of its own, so the split has to happen
    where the entries are read rather than twice at the call sites.
    """
    if entries is None:
        return []
    out: list[tuple[str, str, str, Mapping[str, Any]]] = []
    for entry in entries.entries:
        section = entry_section(entry) or "?"
        key = str(
            entry.get("key")
            or entry.get("businessKey")
            or ("*" if entry.get("value") is not None else section)
        )
        if (section == REGISTRY_SECTION) is not registry:
            continue
        out.append((section, key, _json_text(entry_body(entry)), entry_body(entry)))
    return out


def _add_entries(
    context: BuiltContext, entries: SelectedEntries | None, classification: DataClassification
) -> None:
    version = getattr(entries, "version", None) if entries is not None else None
    for section, key, text, _body in _entry_items(entries, registry=False):
        _push(
            context,
            fence(
                kind=ItemSourceKind.APPROVED_ENTRY,
                label=f"{section}/{key}",
                content=text,
                artifact_id=version or "blueprint:draft",
            ),
            ref=f"entry:{section}/{key}",
            # AI-POL-006: an entry takes the classification of the version or draft artifact
            # it belongs to.  The runtime resolves that and passes it in; INTERNAL is only
            # the floor for a caller that supplied nothing.
            classification=classification,
            kind=ItemSourceKind.APPROVED_ENTRY,
            label=f"{section}/{key}",
        )


def _add_registry(
    context: BuiltContext, entries: SelectedEntries | None, classification: DataClassification
) -> None:
    """The Registry entries the run may cite, as their own segment (AI-RT-003)."""
    version = getattr(entries, "version", None) if entries is not None else None
    for section, key, text, _body in _entry_items(entries, registry=True):
        _push(
            context,
            fence(
                kind=ItemSourceKind.REGISTRY_ENTRY,
                label=f"{section}/{key}",
                content=text,
                artifact_id=version or "blueprint:draft",
            ),
            ref=f"registry:{section}/{key}",
            classification=classification,
            kind=ItemSourceKind.REGISTRY_ENTRY,
            label=f"{section}/{key}",
        )


def _add_inputs(context: BuiltContext, inputs: Sequence[InputArtifact]) -> None:
    for item in inputs:
        if item.classification is DataClassification.PHI_PII:
            context.excluded_phi.append(item.artifact_id)
            continue
        message = fence(
            kind=ItemSourceKind.INPUT_ARTIFACT,
            label=item.artifact_id,
            content=item.content,
            artifact_id=item.artifact_id,
            content_hash=item.content_hash,
            images=item.images,
        )
        _push(
            context,
            message,
            ref=f"artifact:{item.artifact_id}",
            classification=item.classification,
            kind=ItemSourceKind.INPUT_ARTIFACT,
            label=item.artifact_id,
        )


def _add_similar(context: BuiltContext, records: Sequence[Mapping[str, Any]]) -> None:
    if not records:
        return
    text = (
        "Objects that already exist in the design-basis environment and resemble something "
        "you may want to create. For each one you must decide REUSE or CREATE and give a "
        "reason (AI-AG-011); an exact code match is not a judgement call.\n\n"
        + "\n".join(_json_text(record) for record in records)
    )
    _push(
        context,
        fence(
            kind=ItemSourceKind.SIMILAR_OBJECTS,
            label="discovery similarity search",
            content=text,
            artifact_id="s3:similar-search",
        ),
        ref="s3:similar-search",
        classification=DataClassification.INTERNAL,
        kind=ItemSourceKind.SIMILAR_OBJECTS,
        label="similar objects",
    )


def _add_questions(context: BuiltContext, questions: Sequence[Mapping[str, Any]]) -> None:
    if not questions:
        return
    context.open_questions = [dict(item) for item in questions]
    text = (
        "Questions already open on this stage. Do not ask them again; answer them if your "
        "new work resolves them, or leave them be.\n\n"
        + "\n".join(_json_text(item) for item in questions)
    )
    _push(
        context,
        fence(
            kind=ItemSourceKind.OPEN_QUESTION,
            label="open questions",
            content=text,
            artifact_id="s3:open-questions",
        ),
        ref="s3:open-questions",
        classification=DataClassification.INTERNAL,
        kind=ItemSourceKind.OPEN_QUESTION,
        label="open questions",
    )


def _add_thresholds(context: BuiltContext, thresholds: Thresholds) -> None:
    _push(
        context,
        plain(
            kind=ItemSourceKind.STAGE_RULES,
            text=(
                "Confidence thresholds for this project (source: "
                f"{thresholds.source}): extract at or above {thresholds.extraction} to mark "
                f"an item confirmed; a mapping decision below {thresholds.decision}, or "
                "of HIGH impact, must be raised as a question and never adopted by you."
            ),
        ),
        ref="s3:thresholds",
        classification=DataClassification.INTERNAL,
        kind=ItemSourceKind.STAGE_RULES,
        label="thresholds",
    )


def _json_text(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def provider_supports(provider: ProviderInfo, context: BuiltContext) -> str | None:
    """Why this provider cannot serve the context, or ``None`` when it can.

    The capability half of AI-POL-007, kept next to the context so a vision input is checked
    against the context that actually contains it rather than against a flag the caller
    remembered to set.
    """
    if context.needs_vision and not provider.capabilities.vision:
        return "the context carries an image and this model declares no vision capability"
    if not provider.capabilities.structured_output:
        return "the answer must be schema-constrained and this model declares no structured output"
    if context.token_estimate > provider.capabilities.max_context_tokens:
        return (
            f"the context is an estimated {context.token_estimate} tokens and this model's "
            f"window is {provider.capabilities.max_context_tokens}"
        )
    return None


def assert_phi_excluded(items: Sequence[ContextItem]) -> None:
    """Belt-and-braces for the runtime: a built context must contain no PHI_PII item."""
    offenders = [item.ref for item in items if item.classification is DataClassification.PHI_PII]
    if offenders:
        raise ContextContainsPhi(
            "a built context contained a PHI_PII item; the builder must have excluded it",
            details={"refs": offenders},
        )


__all__ = [
    "continue_transcript",
    "BuiltContext",
    "InputArtifact",
    "build",
    "provider_supports",
    "assert_phi_excluded",
]
