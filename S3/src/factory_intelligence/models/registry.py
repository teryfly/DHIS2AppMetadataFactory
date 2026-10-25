"""``ai.provider``, ``ai.model``, ``ai.prompt_version``, ``ai.prompt_activation``,
``ai.regression_ref`` — the two registries of SRS 3.6/3.7.

Why ``api_key_secret_ref`` and never a key column (AI-LLM-005, 00 doc 6.9): the
reference is a pointer such as ``env://MODEL_API_KEY`` that only the adapter resolves,
at request time, into a :class:`factory_foundation.api.Secret` whose ``__str__`` is
already masked.  A key stored here, logged here, or copied onto a DTO would be a
secret in a database that other subsystems can read.

Why activation is a separate table from the versions themselves (AI-REG-002): "a
version exists" and "this version is in use" are different facts, and only the second
one is allowed to fail a gate.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_intelligence.models.base import AiTable


class ProviderRow(AiTable):
    __tablename__ = "provider"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    #: LOCAL_ONLY | PRIVATE_NETWORK | APPROVED_EXTERNAL | UNRESTRICTED — the adapter
    #: must declare it (AI-LLM-002); the policy engine cannot work without it
    provider_class: Mapped[str] = mapped_column(String(32), nullable=False)
    endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_key_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: which adapter constructs it; AI-NFR-05: a new vendor means a new value here
    #: and one new file, and nothing else changes
    adapter: Mapped[str] = mapped_column(String(32), nullable=False, default="openai_compat")
    #: the vendor preset this row was started from; a label for the console, not a
    #: capability switch — the address above remains the one thing a call dials
    vendor: Mapped[str] = mapped_column(String(64), nullable=False, default="custom")
    #: model names worth offering when a model is registered under this provider.
    #: Suggestions only: the authoritative model registry is ``ai.model``, and a
    #: private gateway may serve names no preset knows about.
    suggested_models: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    registered_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")

    __table_args__ = (UniqueConstraint("name", name="uq_ai_provider_name"),)


class ModelRow(AiTable):
    """AI-REG-004: everything the provider selector and the cost model need."""

    __tablename__ = "model"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ai.provider.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: {"structuredOutput": true, "vision": false, "maxContextTokens": 128000}
    capabilities: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    #: per one thousand tokens (AI-COST-001)
    unit_price_input_per_1k: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unit_price_output_per_1k: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    #: {"MAPPING": 1, "REQUIREMENTS": 2} — agent type -> preference order (AI-POL-007)
    applicable_agents: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    #: AI-LLM-003 lets *each model registration* configure its interface address and Key:
    #: one provider can front two servers (a vLLM host and an Ollama host, or two regions),
    #: and a run must not be pinned to one endpoint because two models share a name row.
    #: Absent means "use the provider's", which is what every registration before this
    #: column existed meant.
    endpoint: Mapped[str | None] = mapped_column(String(512), nullable=True)
    api_key_secret_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: the latest evaluation recorded against this model (AI-REG-005); the history
    #: stays in ``ai.eval_run``
    last_eval_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    last_eval_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint("provider_id", "name", name="uq_ai_model_provider_name"),
        Index("ix_ai_model_enabled", "enabled"),
    )


class PromptVersionRow(AiTable):
    """AI-REG-001: a versioned prompt and everything it declares.

    ``prohibitions`` is data, not prose in the body: AI-RT-012 enforces it as a
    constraint check, so a prompt that says "never emit a UID" and a runtime that
    rejects one are the same declared fact rather than two things that may disagree.
    """

    __tablename__ = "prompt_version"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: short name, e.g. "mapping.decide"
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: integer version, so "mapping.decide" v3 reads as ``mapping.decide.v3``
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    agent_type: Mapped[str] = mapped_column(String(32), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    system_rules: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    output_schema_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    blueprint_schema_range: Mapped[str] = mapped_column(String(64), nullable=False, default=">=1")
    dhis2_version_range: Mapped[str] = mapped_column(String(64), nullable=False, default="*")
    prohibitions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    applicable_stages: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: hash of the packaged source file, so a registry row can be traced to a release
    source_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    registered_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")

    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_ai_prompt_version_name_version"),
        Index("ix_ai_prompt_version_agent", "agent_type"),
    )


class PromptActivationRow(AiTable):
    """AI-REG-002: the one version an agent type is actually running.

    One row per agent type.  Writing it is the only way a prompt change takes effect,
    and it is refused unless both gates of AI-REG-003 are present — which is why the
    gate lives here rather than being checked at read time by each caller.
    """

    __tablename__ = "prompt_activation"

    agent_type: Mapped[str] = mapped_column(String(32), primary_key=True)
    prompt_version_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ai.prompt_version.id"), nullable=False
    )
    #: the ordered candidate list AI-POL-007 walks, resolved to model ids
    model_list: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    activated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    eval_run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    regression_passed_ref: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (Index("ix_ai_prompt_activation_prompt", "prompt_version_id"),)


class RegressionRefRow(AiTable):
    """The "回归通过" mark the test group registers after a Golden Project run.

    SRS AI-REG-003 requires it and S3 cannot produce it: the regression is executed by
    another team against another system, so it arrives as a claim with a source, and
    activation refuses an unverified one.
    """

    __tablename__ = "regression_ref"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    prompt_version_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("ai.prompt_version.id"), nullable=False
    )
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(128), nullable=False, default="golden-project")
    recorded_by: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index("ix_ai_regression_prompt_passed", "prompt_version_id", "passed"),
    )


__all__ = [
    "ProviderRow",
    "ModelRow",
    "PromptVersionRow",
    "PromptActivationRow",
    "RegressionRefRow",
]
