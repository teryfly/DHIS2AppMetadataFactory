"""Model Registry: providers, models and the candidate list (AI-REG-004, AI-POL-007).

The registry is the only place a credential reference is stored, and the only place one is
resolved — inside the adapter, at request time (AI-LLM-005).  Keeping registration here
rather than in configuration is what makes AI-REG-004's "单价" and "适用的 Agent 类型与优先
序" queryable, which in turn is what AI-COST-001's rollup and AI-POL-007's model walk run
on: cost cannot be attributed to a model that has no price, and a model cannot be preferred
by an agent that never declared it.

:class:`RegisteredModel` is the join of a model row and its provider row.  It is a plain
record so the selection logic in :mod:`factory_intelligence.policy.selection` never needs a
database session, and so an operator can see the candidate list an agent will walk without
running anything.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from factory_foundation.api import (
    NotFoundError,
    ValidationError,
    current_session,
    has_active_uow,
    new_id,
    unit_of_work,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from factory_intelligence import audit_log
from factory_intelligence.enums import AgentType, ProviderClass, as_provider_class
from factory_intelligence.llm.types import Capabilities, ProviderInfo
from factory_intelligence.models.registry import ModelRow, ProviderRow
from factory_intelligence.policy.selection import CandidateModel


@dataclass(frozen=True)
class RegisteredModel:
    """A model row joined with its provider, the unit the policy engine chooses from."""

    model: str
    provider: str
    provider_id: str
    provider_class: ProviderClass
    capabilities: Capabilities
    enabled: bool
    unit_price_input_per_1k: float
    unit_price_output_per_1k: float
    applicable_agents: dict[str, int]
    endpoint: str | None
    api_key_secret_ref: str | None
    adapter: str
    model_id: str
    #: the newest evaluation registered against this model (AI-REG-005); the full history
    #: stays in ``ai.eval_run``, this is the "current standing" the registry reports
    last_eval_score: float | None = None
    last_eval_run_id: str | None = None

    @property
    def ref(self) -> str:
        """``provider/model`` — the spelling a call record and a cost line use."""
        return f"{self.provider}/{self.model}"

    def provider_info(self) -> ProviderInfo:
        return ProviderInfo(
            provider=self.provider,
            provider_class=self.provider_class,
            model=self.model,
            capabilities=self.capabilities,
            endpoint=self.endpoint,
            api_key_secret_ref=self.api_key_secret_ref,
        )

    def candidate(self, *, priority: int, needed_tokens: int = 0) -> CandidateModel:
        return CandidateModel(
            model=self.model,
            provider=self.provider,
            provider_class=self.provider_class,
            structured_output=self.capabilities.structured_output,
            vision=self.capabilities.vision,
            max_context_tokens=self.capabilities.max_context_tokens,
            enabled=self.enabled,
            priority=priority,
            context_tokens=needed_tokens,
        )

    def price_for(self, input_tokens: int, output_tokens: int) -> float:
        """Estimated cost of one call (AI-LLM-004, AI-COST-001)."""
        return (
            input_tokens / 1000.0 * self.unit_price_input_per_1k
            + output_tokens / 1000.0 * self.unit_price_output_per_1k
        )


#: Column name -> the camelCase spelling `register_provider` puts in the audit payload.  One
#: vocabulary for both writes, or a reader of `after.providerClass` sees a registration and an
#: update speak different languages about the same column.
_WIRE = {
    "provider_class": "providerClass",
    "endpoint": "endpoint",
    "api_key_secret_ref": "apiKeySecretRef",
    "adapter": "adapter",
    "enabled": "enabled",
    "vendor": "vendor",
    "suggested_models": "suggestedModels",
}

#: The same vocabulary for the model row, or an audit reader sees a provider update and a model
#: update spell the same two shared columns (`endpoint`, `enabled`) in two different ways.
_MODEL_WIRE = {
    **_WIRE,
    "capabilities": "capabilities",
    "unit_price_input_per_1k": "unitPriceInputPer1k",
    "unit_price_output_per_1k": "unitPriceOutputPer1k",
    "applicable_agents": "applicableAgents",
}


@dataclass(frozen=True)
class RegisteredProvider:
    id: str
    name: str
    provider_class: ProviderClass
    endpoint: str | None
    enabled: bool
    adapter: str
    api_key_secret_ref: str | None
    vendor: str = "custom"
    suggested_models: list[str] = field(default_factory=list)


def _require_secret_ref(ref: str) -> None:
    """AI-LLM-005: a registration may name a Secret, never carry one.

    One rule for the provider row and the model row (AI-LLM-003 lets either hold the
    reference), because a literal that reached a column would reach every read model,
    audit row and log line that prints the registration.
    """
    if "://" not in ref:
        raise ValidationError(
            "api_key_secret_ref must be a Secret reference such as 'env://NAME'; a literal "
            "key may not be registered (AI-LLM-005)",
            code="VALIDATION_ERROR",
        )


#: The console's own limits for the two presentational provider fields.  They are not
#: capabilities — ``vendor`` labels where a row started, and ``suggested_models`` only
#: feeds candidate buttons on the model form — but an unbounded column would still
#: reach PostgreSQL and come back as a 500 naming no field.
_VENDOR_MAX_LENGTH = 64
_SUGGESTED_MODEL_MAX_LENGTH = 128
_MAX_SUGGESTED_MODELS = 100


def _vendor_name(value: str | None) -> str:
    """The preset key, with ``custom`` as the reading of "none chosen".

    An *empty* string is not "none chosen": registration and an edit both default an absent
    value, so an empty one can only be a form that stripped a blank box — and silently
    rewriting that to ``custom`` would turn a typo into a successful save.
    """
    name = (value if value is not None else "custom").strip()
    if not name or len(name) > _VENDOR_MAX_LENGTH:
        raise ValidationError(
            f"vendor must be 1..{_VENDOR_MAX_LENGTH} characters",
            code="VALIDATION_ERROR",
            details={"field": "vendor"},
        )
    return name


def _suggested_model_names(value: list[str] | None) -> list[str]:
    """Trim, drop blanks and duplicates, and refuse names past the model column's width.

    Suggestions are never validation: the model registry remains the authority on what
    exists, and a preset's stale name costs one keystroke here.  Deduplicating is the
    one thing this normalisation does more than trim, because two identical buttons in
    a candidate row are indistinguishable to the person tapping them.
    """
    if not value:
        return []
    if len(value) > _MAX_SUGGESTED_MODELS:
        raise ValidationError(
            f"at most {_MAX_SUGGESTED_MODELS} suggested model names may be stored",
            code="VALIDATION_ERROR",
            details={"field": "suggestedModels"},
        )
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        name = str(item).strip()
        if not name:
            continue
        if len(name) > _SUGGESTED_MODEL_MAX_LENGTH:
            raise ValidationError(
                f"a suggested model name may be at most {_SUGGESTED_MODEL_MAX_LENGTH} characters",
                code="VALIDATION_ERROR",
                details={"field": "suggestedModels", "value": name},
            )
        if name not in seen:
            out.append(name)
            seen.add(name)
    return out


def register_provider(
    *,
    name: str,
    provider_class: ProviderClass | str,
    endpoint: str | None = None,
    api_key_secret_ref: str | None = None,
    adapter: str = "openai_compat",
    enabled: bool = True,
    vendor: str = "custom",
    suggested_models: list[str] | None = None,
    operator: str = "system",
    session: Session | None = None,
) -> RegisteredProvider:
    """AI-REG-004.  ``provider_class`` is mandatory, not defaulted.

    A provider with no class cannot be policy-evaluated at all, so a missing or unknown
    value is refused here rather than assumed: assuming ``LOCAL_ONLY`` would be a silent
    promise that nothing leaves the network, which is exactly the assumption S3 must never
    make on someone else's behalf (AI-LLM-002).
    """
    try:
        chosen = as_provider_class(provider_class)
    except ValueError as exc:
        raise ValidationError(
            f"unknown provider class {provider_class!r}",
            code="VALIDATION_ERROR",
            details={"providerClass": provider_class},
        ) from exc
    if api_key_secret_ref:
        _require_secret_ref(api_key_secret_ref)
    chosen_vendor = _vendor_name(vendor)
    chosen_suggestions = _suggested_model_names(suggested_models)

    def _write(sess: Session) -> RegisteredProvider:
        existing = sess.execute(
            select(ProviderRow).where(ProviderRow.name == name)
        ).scalars().first()
        row = existing
        if row is None:
            row = ProviderRow(id=new_id("apvd"), name=name)
            sess.add(row)
        row.provider_class = chosen.value
        row.endpoint = endpoint
        row.api_key_secret_ref = api_key_secret_ref
        row.adapter = adapter
        row.enabled = enabled
        row.vendor = chosen_vendor
        row.suggested_models = chosen_suggestions
        row.registered_by = operator
        audit_log.record(
            "ai.model.registered",
            session=sess,
            object_type="provider",
            object_id=row.id,
            after={
                "name": name,
                "providerClass": chosen.value,
                "endpoint": endpoint,
                # the reference, never a value; there is no value to write here
                "apiKeySecretRef": api_key_secret_ref,
                "adapter": adapter,
                "enabled": enabled,
                "vendor": chosen_vendor,
                "suggestedModels": chosen_suggestions,
            },
            operator=operator,
            operator_is_user=True,
        )
        sess.flush()
        return _as_registered_provider(row)

    return _run(session, _write)


def update_provider(
    *,
    name: str,
    provider_class: ProviderClass | str | None = None,
    endpoint: str | None = None,
    api_key_secret_ref: str | None = None,
    adapter: str | None = None,
    enabled: bool | None = None,
    vendor: str | None = None,
    suggested_models: list[str] | None = None,
    operator: str = "system",
    session: Session | None = None,
) -> RegisteredProvider:
    """Correct one provider in place, touching only what the caller supplied (AI-REG-004).

    :func:`register_provider` writes every column, which is right for registering and wrong
    for editing: retyping a mistyped base URL must not blank the Key reference the provider
    was registered with.  An absent argument here therefore means "leave it as it is".
    """
    changes: dict[str, Any] = {}
    if provider_class is not None:
        try:
            chosen = as_provider_class(provider_class)
        except ValueError as exc:
            raise ValidationError(
                f"unknown provider class {provider_class!r}",
                code="VALIDATION_ERROR",
                details={"providerClass": provider_class},
            ) from exc
        changes["provider_class"] = chosen.value
    if endpoint is not None:
        # a blank address is not "unchanged": the row would keep the name an operator can see and
        # lose the address every call needs (`openai_compat.provider_for` refuses an empty one),
        # so a stray whitespace submission has to fail here rather than disable the provider.
        if not endpoint.strip():
            raise ValidationError(
                "endpoint must not be blank; omit the field to leave the stored address alone",
                code="VALIDATION_ERROR",
                details={"field": "endpoint"},
            )
        changes["endpoint"] = endpoint.strip()
    if api_key_secret_ref is not None:
        _require_secret_ref(api_key_secret_ref)
        changes["api_key_secret_ref"] = api_key_secret_ref.strip()
    if adapter is not None:
        if not adapter.strip():
            raise ValidationError(
                "adapter must not be blank; omit the field to leave the stored one alone",
                code="VALIDATION_ERROR",
                details={"field": "adapter"},
            )
        changes["adapter"] = adapter.strip()
    if enabled is not None:
        changes["enabled"] = bool(enabled)
    if vendor is not None:
        changes["vendor"] = _vendor_name(vendor)
    if suggested_models is not None:
        # an empty list is a change (clear the candidates); only None means "leave it"
        changes["suggested_models"] = _suggested_model_names(suggested_models)
    if not changes:
        raise ValidationError(
            "update_provider needs at least one field to change",
            code="VALIDATION_ERROR",
            details={"provider": name},
        )

    def _write(sess: Session) -> RegisteredProvider:
        row = sess.execute(select(ProviderRow).where(ProviderRow.name == name)).scalars().first()
        if row is None:
            raise NotFoundError(
                f"provider {name!r} is not registered", details={"provider": name}
            )
        before = {_WIRE[key]: getattr(row, key) for key in changes}
        for key, value in changes.items():
            setattr(row, key, value)
        audit_log.record(
            "ai.model.registered",
            session=sess,
            object_type="provider",
            object_id=row.id,
            before=before,
            after={**{_WIRE[k]: v for k, v in changes.items()}, "name": name, "action": "updateProvider"},
            operator=operator,
            operator_is_user=True,
        )
        sess.flush()
        return _as_registered_provider(row)

    return _run(session, _write)


def register_model(
    *,
    provider: str,
    model: str,
    capabilities: Capabilities | dict[str, Any] | None = None,
    unit_price_input_per_1k: float = 0.0,
    unit_price_output_per_1k: float = 0.0,
    applicable_agents: dict[str, int] | None = None,
    enabled: bool = True,
    endpoint: str | None = None,
    api_key_secret_ref: str | None = None,
    operator: str = "system",
    session: Session | None = None,
) -> RegisteredModel:
    """Register one model under an existing provider (AI-REG-004).

    ``endpoint`` and ``api_key_secret_ref`` are AI-LLM-003's per-model configuration; either
    may be left absent, in which case the provider's own value is what a call uses.
    """
    caps = _capabilities(capabilities)
    agents = _agent_priorities(applicable_agents)
    if api_key_secret_ref:
        _require_secret_ref(api_key_secret_ref)

    def _write(sess: Session) -> RegisteredModel:
        provider_row = sess.execute(
            select(ProviderRow).where(ProviderRow.name == provider)
        ).scalars().first()
        if provider_row is None:
            raise NotFoundError(
                f"provider {provider!r} is not registered", details={"provider": provider}
            )
        row = sess.execute(
            select(ModelRow).where(ModelRow.provider_id == provider_row.id, ModelRow.name == model)
        ).scalars().first()
        if row is None:
            row = ModelRow(id=new_id("amdl"), provider_id=provider_row.id, name=model)
            sess.add(row)
        row.capabilities = _capabilities_payload(caps)
        row.enabled = enabled
        row.unit_price_input_per_1k = unit_price_input_per_1k
        row.unit_price_output_per_1k = unit_price_output_per_1k
        row.applicable_agents = agents
        row.endpoint = endpoint
        row.api_key_secret_ref = api_key_secret_ref
        audit_log.record(
            "ai.model.registered",
            session=sess,
            object_type="model",
            object_id=row.id,
            after={
                "provider": provider,
                "model": model,
                "providerClass": provider_row.provider_class,
                "capabilities": _capabilities_payload(caps),
                "unitPriceInputPer1k": unit_price_input_per_1k,
                "unitPriceOutputPer1k": unit_price_output_per_1k,
                "applicableAgents": agents,
                # reference only, on both rows that may carry one (AI-GOV-002)
                "endpoint": endpoint,
                "apiKeySecretRef": api_key_secret_ref,
                "enabled": enabled,
            },
            operator=operator,
            operator_is_user=True,
        )
        sess.flush()
        return _as_registered_model(row, provider_row)

    return _run(session, _write)


def update_model(  # noqa: PLR0913 - the optional set *is* the patchable surface; absent means unchanged
    *,
    ref: str,
    capabilities: Capabilities | dict[str, Any] | None = None,
    unit_price_input_per_1k: float | None = None,
    unit_price_output_per_1k: float | None = None,
    applicable_agents: dict[str, int] | None = None,
    enabled: bool | None = None,
    endpoint: str | None = None,
    api_key_secret_ref: str | None = None,
    operator: str = "system",
    session: Session | None = None,
) -> RegisteredModel:
    """Correct one model in place, touching only what the caller supplied (AI-REG-004, GOV06).

    :func:`register_model` writes every column, which is right for registering and wrong for
    editing: fixing a price must not blank AI-LLM-003's per-model address and Key reference,
    because an omitted argument there is written as NULL and the provider's own values then
    quietly take over again.  An absent argument here therefore means "leave it as it is".

    ``capabilities`` is the one nested column, and it is merged rather than replaced: a caller
    that names only ``maxContextTokens`` gets that one number changed, not a model that has also
    been switched to "no structured output" and "no vision" by the defaults of the object it
    travelled in.  ``applicable_agents`` has no such merge on purpose -- the map *is* the
    statement of who may use the model, and ``{}`` ("nobody") is a value, not an absence.

    There is no way to *remove* an override: an empty string fails the same checks the
    registration applies, so clearing a model-level address means registering the model again on
    a provider whose own address is the one you want.  That is the same limit
    :func:`update_provider` lives under, and it is stated rather than silently half-supported.
    """
    changes: dict[str, Any] = {}
    if capabilities is not None:
        changes["capabilities"] = capabilities
    for column, value in (
        ("unit_price_input_per_1k", unit_price_input_per_1k),
        ("unit_price_output_per_1k", unit_price_output_per_1k),
    ):
        if value is not None:
            changes[column] = float(value)
    if applicable_agents is not None:
        changes["applicable_agents"] = _agent_priorities(applicable_agents)
    if enabled is not None:
        changes["enabled"] = bool(enabled)
    if endpoint is not None:
        if not endpoint.strip():
            raise ValidationError(
                "endpoint must not be blank; omit the field to leave the stored address alone",
                code="VALIDATION_ERROR",
                details={"field": "endpoint"},
            )
        changes["endpoint"] = endpoint.strip()
    if api_key_secret_ref is not None:
        _require_secret_ref(api_key_secret_ref)
        changes["api_key_secret_ref"] = api_key_secret_ref.strip()
    if not changes:
        raise ValidationError(
            "update_model needs at least one field to change",
            code="VALIDATION_ERROR",
            details={"ref": ref},
        )

    def _write(sess: Session) -> RegisteredModel:
        provider_name, _, model_name = ref.partition("/")
        found = sess.execute(
            select(ModelRow, ProviderRow)
            .join(ProviderRow, ModelRow.provider_id == ProviderRow.id)
            .where(ProviderRow.name == provider_name, ModelRow.name == model_name)
        ).first()
        if found is None:
            raise NotFoundError(
                f"model {ref!r} is not registered; a model ref is 'provider/model'"
            )
        row, provider_row = found
        if "capabilities" in changes:
            # merged against what is stored, so an unnamed capability keeps its own value rather
            # than the dataclass default it would have arrived wearing
            merged = dict(row.capabilities or {})
            merged.update(_capability_patch(changes["capabilities"]))
            changes["capabilities"] = _capabilities_payload(_capabilities(merged))
        before = {_MODEL_WIRE[key]: getattr(row, key) for key in changes}
        for key, value in changes.items():
            setattr(row, key, value)
        audit_log.record(
            "ai.model.registered",
            session=sess,
            object_type="model",
            object_id=row.id,
            before=before,
            after={
                **{_MODEL_WIRE[k]: v for k, v in changes.items()},
                "provider": provider_row.name,
                "model": row.name,
                "action": "updateModel",
            },
            operator=operator,
            operator_is_user=True,
        )
        sess.flush()
        return _as_registered_model(row, provider_row)

    return _run(session, _write)


def set_enabled(
    *, kind: str, ref: str, enabled: bool, operator: str = "system", session: Session | None = None
) -> None:
    """Enable or disable a provider (``ref`` = its name) or a model (``ref`` = ``provider/model``).

    Disabling is the only way to take a model out of rotation without deleting it: the
    usage ledger and the evaluation history must survive the decision (AI-REG-005), and an
    operator needs to be able to re-enable exactly what was switched off.
    """
    if kind not in ("provider", "model"):
        raise ValidationError(
            f"cannot enable or disable a {kind!r}; expected 'provider' or 'model'",
            code="VALIDATION_ERROR",
        )

    def _write(sess: Session) -> None:
        if kind == "provider":
            row = sess.execute(
                select(ProviderRow).where(ProviderRow.name == ref)
            ).scalars().first()
            if row is None:
                raise NotFoundError(f"provider {ref!r} is not registered")
            row.enabled = enabled
            object_type, object_id = "provider", row.id
        else:
            provider_name, _, model_name = ref.partition("/")
            found = sess.execute(
                select(ModelRow, ProviderRow)
                .join(ProviderRow, ModelRow.provider_id == ProviderRow.id)
                .where(ProviderRow.name == provider_name, ModelRow.name == model_name)
            ).first()
            if found is None:
                raise NotFoundError(
                    f"model {ref!r} is not registered; a model ref is 'provider/model'"
                )
            found[0].enabled = enabled
            object_type, object_id = "model", found[0].id
        audit_log.record(
            "ai.model.registered",
            session=sess,
            object_type=object_type,
            object_id=object_id,
            after={"enabled": enabled, "action": "setEnabled"},
            operator=operator,
            operator_is_user=True,
        )
        sess.flush()

    _run(session, _write)


def list_providers(*, session: Session | None = None) -> list[RegisteredProvider]:
    def _read(sess: Session) -> list[RegisteredProvider]:
        rows = sess.execute(select(ProviderRow).order_by(ProviderRow.name)).scalars().all()
        return [_as_registered_provider(row) for row in rows]

    return _read_only(session, _read)


def registered_provider(name: str, *, session: Session | None = None) -> RegisteredProvider:
    """Look one provider up by name — the address a reachability probe is aimed at.

    ``list_providers`` serves the catalogue; a call about *this* provider needs the row or an
    explicit "not registered", not an empty list the caller has to interpret.
    """

    def _read(sess: Session) -> RegisteredProvider:
        row = sess.execute(select(ProviderRow).where(ProviderRow.name == name)).scalars().first()
        if row is None:
            raise NotFoundError(f"provider {name!r} is not registered", details={"name": name})
        return _as_registered_provider(row)

    return _read_only(session, _read)


def list_models(
    *, agent_type: AgentType | str | None = None, include_disabled: bool = True,
    session: Session | None = None,
) -> list[RegisteredModel]:
    """The registry, optionally narrowed to one agent's preference list."""
    wanted = AgentType(agent_type).value if agent_type else None

    def _read(sess: Session) -> list[RegisteredModel]:
        rows = sess.execute(
            select(ModelRow, ProviderRow).join(ProviderRow, ModelRow.provider_id == ProviderRow.id)
        ).all()
        out: list[RegisteredModel] = []
        for model_row, provider_row in rows:
            registered = _as_registered_model(model_row, provider_row)
            if not include_disabled and not registered.enabled:
                continue
            if not provider_row.enabled and not include_disabled:
                continue
            if wanted is not None and wanted not in registered.applicable_agents:
                continue
            out.append(registered)
        return sorted(out, key=lambda item: (item.provider, item.model))

    return _read_only(session, _read)


def candidates_for_agent(
    agent_type: AgentType | str, *, needed_tokens: int = 0, session: Session | None = None
) -> list[CandidateModel]:
    """The ordered list AI-POL-007 walks for one agent type.

    A model whose provider is disabled is dropped here rather than reported as a denial:
    an operator turned it off on purpose, and listing it as "unavailable" in every run
    result would argue with that decision.
    """
    wanted = AgentType(agent_type).value

    def _read(sess: Session) -> list[CandidateModel]:
        rows = sess.execute(
            select(ModelRow, ProviderRow).join(ProviderRow, ModelRow.provider_id == ProviderRow.id)
        ).all()
        out: list[CandidateModel] = []
        for model_row, provider_row in rows:
            if not provider_row.enabled:
                continue
            registered = _as_registered_model(model_row, provider_row)
            priority = registered.applicable_agents.get(wanted)
            if priority is None:
                continue
            out.append(registered.candidate(priority=priority, needed_tokens=needed_tokens))
        return out

    return _read_only(session, _read)


def registered_model(ref: str, *, session: Session | None = None) -> RegisteredModel:
    """Look one model up by ``provider/model`` — what a run row records it used."""
    provider_name, _, model_name = ref.partition("/")
    if not model_name:
        raise NotFoundError(
            f"model reference {ref!r} must be 'provider/model'", details={"ref": ref}
        )

    def _read(sess: Session) -> RegisteredModel:
        row = sess.execute(
            select(ModelRow, ProviderRow)
            .join(ProviderRow, ModelRow.provider_id == ProviderRow.id)
            .where(ProviderRow.name == provider_name, ModelRow.name == model_name)
        ).first()
        if row is None:
            raise NotFoundError(f"model {ref!r} is not registered", details={"ref": ref})
        return _as_registered_model(row[0], row[1])

    return _read_only(session, _read)


def record_eval_result(
    *,
    ref: str,
    score: float,
    eval_run_id: str,
    session: Session | None = None,
) -> None:
    """AI-REG-005: the registry keeps the latest score; the history stays in ``ai.eval_run``."""
    provider_name, _, model_name = ref.partition("/")

    def _write(sess: Session) -> None:
        row = sess.execute(
            select(ModelRow, ProviderRow)
            .join(ProviderRow, ModelRow.provider_id == ProviderRow.id)
            .where(ProviderRow.name == provider_name, ModelRow.name == model_name)
        ).first()
        if row is None:
            raise NotFoundError(f"model {ref!r} is not registered", details={"ref": ref})
        model_row = row[0]
        model_row.last_eval_score = score
        model_row.last_eval_run_id = eval_run_id
        sess.flush()

    _run(session, _write)


# --- helpers --------------------------------------------------------------------


def _capabilities(value: Capabilities | dict[str, Any] | None) -> Capabilities:
    if value is None:
        # structured output is not optional for an agent: an answer that cannot be
        # constrained to a schema cannot be checked, and AI-RT-010 requires the check
        return Capabilities(structured_output=True, vision=False, max_context_tokens=8000)
    if isinstance(value, Capabilities):
        return value
    return Capabilities(
        structured_output=bool(value.get("structuredOutput", value.get("structured_output", True))),
        vision=bool(value.get("vision", False)),
        max_context_tokens=int(value.get("maxContextTokens", value.get("max_context_tokens", 8000))),
    )


def _capabilities_payload(caps: Capabilities) -> dict[str, Any]:
    return {
        "structuredOutput": caps.structured_output,
        "vision": caps.vision,
        "maxContextTokens": caps.max_context_tokens,
    }


#: Both spellings of the three capability keys, pointing at the one the column stores.
_CAPS_KEYS = {
    "structuredOutput": "structuredOutput",
    "structured_output": "structuredOutput",
    "vision": "vision",
    "maxContextTokens": "maxContextTokens",
    "max_context_tokens": "maxContextTokens",
}


def _capability_patch(value: Capabilities | dict[str, Any]) -> dict[str, Any]:
    """Only the capability keys the caller named, in the spelling the column stores.

    :func:`_capabilities` cannot stand in for this: it fills what is missing with its own
    defaults, and a patch that says one thing about one key must not silently rewrite the other
    two -- which is exactly how "raise the context window" would become "this model cannot
    answer in a checkable shape".
    """
    if isinstance(value, Capabilities):
        return _capabilities_payload(value)
    out: dict[str, Any] = {}
    for key, item in value.items():
        wire = _CAPS_KEYS.get(str(key))
        if wire is None:
            raise ValidationError(
                f"{key!r} is not a capability; a model carries structuredOutput, vision and maxContextTokens",
                code="VALIDATION_ERROR",
                details={"key": key},
            )
        out[wire] = int(item) if wire == "maxContextTokens" else bool(item)
    return out


def _agent_priorities(value: dict[str, int] | None) -> dict[str, int]:
    if not value:
        return {}
    out: dict[str, int] = {}
    for key, priority in value.items():
        try:
            out[AgentType(str(key)).value] = int(priority)
        except ValueError as exc:
            raise ValidationError(
                f"{key!r} is not an agent type", code="VALIDATION_ERROR", details={"key": key}
            ) from exc
    return out


def _as_registered_provider(row: ProviderRow) -> RegisteredProvider:
    return RegisteredProvider(
        id=row.id,
        name=row.name,
        provider_class=ProviderClass(row.provider_class),
        endpoint=row.endpoint,
        enabled=row.enabled,
        adapter=row.adapter,
        api_key_secret_ref=row.api_key_secret_ref,
        vendor=row.vendor or "custom",
        suggested_models=list(row.suggested_models or []),
    )


def _as_registered_model(row: ModelRow, provider_row: ProviderRow) -> RegisteredModel:
    caps = _capabilities(row.capabilities)
    return RegisteredModel(
        model=row.name,
        provider=provider_row.name,
        provider_id=provider_row.id,
        provider_class=ProviderClass(provider_row.provider_class),
        capabilities=caps,
        enabled=row.enabled,
        unit_price_input_per_1k=row.unit_price_input_per_1k,
        unit_price_output_per_1k=row.unit_price_output_per_1k,
        applicable_agents={str(k): int(v) for k, v in (row.applicable_agents or {}).items()},
        # AI-LLM-003: a model that names its own address or Key wins over the provider's,
        # so two models behind one provider row can be served by two servers
        endpoint=row.endpoint or provider_row.endpoint,
        api_key_secret_ref=row.api_key_secret_ref or provider_row.api_key_secret_ref,
        adapter=provider_row.adapter,
        model_id=row.id,
        last_eval_score=row.last_eval_score,
        last_eval_run_id=row.last_eval_run_id,
    )


T = TypeVar("T")


def _run(session: Session | None, action: Callable[[Session], T]) -> T:
    """Write in the caller's transaction, or open and commit a short one."""
    if session is not None or has_active_uow():
        return action(session if session is not None else current_session())
    with unit_of_work() as sess:
        return action(sess)


def _read_only(session: Session | None, action: Callable[[Session], T]) -> T:
    """Read through the caller's transaction, or a short one of our own.

    Reads never commit: a caller that passes a session may still be inside a transaction
    that ends in a rollback, and committing it here would lose its writes.
    """
    if session is not None:
        return action(session)
    if has_active_uow():
        return action(current_session())
    with unit_of_work() as sess:
        return action(sess)


__all__ = [
    "RegisteredModel",
    "RegisteredProvider",
    "register_provider",
    "register_model",
    "update_model",
    "set_enabled",
    "list_providers",
    "list_models",
    "candidates_for_agent",
    "registered_model",
    "record_eval_result",
]
