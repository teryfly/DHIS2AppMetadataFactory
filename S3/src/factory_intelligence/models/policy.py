"""``ai.policy_decision``, ``ai.org_policy``, ``ai.policy_relaxation`` (AI-POL).

One ``policy_decision`` row is written for **every** model call, allowed or denied
(AI-POL-008): the audit event says a call happened, this row says on what basis and
with what in the context.  AI-NFR-03's "100% of calls have usage and audit records"
is only checkable if the allowed case is recorded too, which is the easy one to forget.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Index, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from factory_intelligence.models.base import AiTable


class PolicyDecisionRow(AiTable):
    __tablename__ = "policy_decision"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    project_id: Mapped[str] = mapped_column(String(64), nullable=False)
    #: the resolved strictest-of policy actually applied, with its sources named
    effective_policy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    provider_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: [{"ref": artifactId, "classification": "..."}] — what was about to leave (AI-RT-006)
    items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    #: ALLOWED | DENIED
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    denied_items: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    #: AI-POL-007: one entry per rejected model, which is what AC-AG-01 asks to show
    model_denials: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    #: normalisation notes from an unrecognized environment policy (never silently applied)
    warnings: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    __table_args__ = (
        Index("ix_ai_policy_decision_run", "run_id"),
        Index("ix_ai_policy_decision_project_created", "project_id", "created_at"),
        Index("ix_ai_policy_decision_result", "result"),
    )


class OrgPolicyRow(AiTable):
    """The organisation-level half of the effective policy (AI-POL-002).

    Seeded deny-by-default: the system's starting answer for an external provider is
    no, and only an explicit, audited relaxation widens it (AI-BR-03).
    """

    __tablename__ = "org_policy"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # fixed "org": one row
    #: {"PUBLIC": ["LOCAL_ONLY", ...], ...} — one entry per classification, always all five
    allowed_classes_by_level: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False, default="system")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class PolicyRelaxationRow(AiTable):
    """AI-POL-002: widening access is itself an audited event with a named operator."""

    __tablename__ = "policy_relaxation"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    #: "organization" or an environment key
    scope: Mapped[str] = mapped_column(String(128), nullable=False)
    #: {"level": "CONFIDENTIAL", "addClasses": ["PRIVATE_NETWORK"]}
    change: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    operator: Mapped[str] = mapped_column(String(128), nullable=False)
    #: M4 turns this into a platform-administrator-only action; MVP records the name
    role_checked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_ai_relaxation_scope_created", "scope", "created_at"),)


__all__ = ["PolicyDecisionRow", "OrgPolicyRow", "PolicyRelaxationRow"]
