"""Seeding helpers for S3's runtime acceptance tests (increment 8, the M1 exit).

Everything here goes through the two public APIs — ``factory_blueprint.api`` and
``factory_intelligence.api`` — and never an internal module, because these tests exist to
prove that S3's runtime drives the *real* Blueprint: AI-AC-04 is only worth something if S2
itself accepts the confirmation record, and AI-RT-013's ladder only ends where S2 says so.

The model is the mock provider (AI-MOCK-001) installed through ``set_llm_provider``, so a run
is reproducible and the suite touches no network, while everything around it — artifacts,
transactions, policy, the registry, S2's validators — is the production path.
"""

from __future__ import annotations

import itertools
from typing import Any

from factory_blueprint import api as blueprint
from factory_foundation.api import (
    ArtifactCreate,
    ArtifactStatus,
    AuditQuery,
    DataClassification,
    query_audit,
    register_artifact,
    unit_of_work,
)
from sqlalchemy import select

from factory_intelligence import api
from factory_intelligence import artifacts as ai_artifacts
from factory_intelligence.dto.request import AgentRunRequest
from factory_intelligence.enums import AgentType, ProviderClass
from factory_intelligence.llm.mock_agent import MockAgentProvider, mock_provider
from factory_intelligence.models.registry import PromptActivationRow

MOCK_PROVIDER = "mock"
MOCK_MODEL = "mock-agent"

_counter = itertools.count(1)


def unique_project_id(prefix: str = "prj") -> str:
    """A project id no other test in this session has seeded."""
    return f"{prefix}-{next(_counter):05d}"


def clear_prompt_activations() -> None:
    """Undo the harness's baseline activation seeding for a test that starts from 'nothing
    activated'.

    ``conftest.harness`` seeds one ``ai.prompt_activation`` row per agent so a normal run is not
    refused (AI-REG-002).  The activation-gate tests and the AI-REG-002 refusal tests need the
    table empty first, so they call this to restore the "nobody finished the deployment" state.
    """
    with unit_of_work() as session:
        for row in session.execute(select(PromptActivationRow)).scalars().all():
            session.delete(row)
        session.flush()


def seed_project(project_id: str, *, mode: str = "B") -> str:
    """Create the Blueprint's ``0.1.0`` version and return its version string."""
    handle = blueprint.prepare_initial_blueprint(
        project_id,
        blueprint.InitialProjectInfo(
            delivery_mode=blueprint.DeliveryMode(mode),
            project_type=blueprint.ProjectType.BROWNFIELD,
            target_dhis2_version_constraint=">=2.41 <2.43",
            default_language="en",
            translation_languages=["fr"],
            code_prefix="MC",
        ),
    )
    with unit_of_work() as session:
        info = blueprint.register_initial_blueprint(project_id, handle, session=session)
    return str(info.version)


def open_draft(project_id: str, stage: str) -> str:
    with unit_of_work() as session:
        return str(blueprint.open_draft(project_id, stage, session=session))


def draft_of(draft_id: str) -> Any:
    return blueprint.get_draft(draft_id)


def install_mock_model(*agents: AgentType | str) -> MockAgentProvider:
    """Register a LOCAL_ONLY mock provider and model, and install the provider.

    Registration goes through ``governance`` — the same operator surface a deployment uses —
    so these tests also prove the registry rows the runtime reads are the rows governance
    wrote.  ``LOCAL_ONLY`` is deliberate: AI-POL-002's default denies every external class,
    so a run that reaches a model is also proof that the policy engine let it.
    """
    provider = mock_provider()
    api.set_llm_provider(provider)
    api.governance(
        "provider.register",
        {"name": MOCK_PROVIDER, "providerClass": ProviderClass.LOCAL_ONLY.value, "adapter": "mock"},
    )
    api.governance(
        "model.register",
        {
            "provider": MOCK_PROVIDER,
            "model": MOCK_MODEL,
            "capabilities": {"structuredOutput": True, "vision": False, "maxContextTokens": 1_000_000},
            "applicableAgents": {str(AgentType(a).value): 1 for a in agents} or {"REQUIREMENTS": 1},
            "unitPriceInputPer1k": 0.001,
            "unitPriceOutputPer1k": 0.002,
        },
    )
    return provider


def script(provider: MockAgentProvider, agent: AgentType | str, *answers: dict[str, Any]) -> str:
    """Attach answers to the agent's script; returns the key used."""
    key = str(AgentType(agent).value)
    provider.add_script(key, *answers)
    return key


def request_for(
    project_id: str,
    agent_type: AgentType | str,
    *,
    stage: str = "REQUIREMENTS",
    draft_id: str | None = None,
    idempotency_key: str | None = None,
    operator: str = "ops",
    input_artifact_ids: list[str] | None = None,
    environment_policies: dict[str, Any] | None = None,
    discovery_result_artifact_id: str | None = None,
    design_reference_environment_key: str | None = None,
    scope: list[str] | None = None,
) -> AgentRunRequest:
    """A request as S1 builds one (``factory_control/services/steps.py:222``)."""
    agent = AgentType(agent_type)
    return AgentRunRequest.model_validate(
        {
            "project_id": project_id,
            "workflow_id": "wf-1",
            "stage_id": "stg-1",
            "stage": stage,
            "agent_type": agent.value,
            "draft_id": draft_id,
            "scope": scope or [],
            "input_artifact_ids": input_artifact_ids or [],
            "design_reference_environment_key": design_reference_environment_key,
            "target_environment_policies": environment_policies or {},
            "capability_profile_artifact_id": None,
            "discovery_result_artifact_id": discovery_result_artifact_id,
            "operator": operator,
            "idempotency_key": idempotency_key or f"run:{project_id}:{agent.value}:{stage}",
        }
    )


def stage_input(
    project_id: str,
    document: dict[str, Any],
    *,
    artifact_type: str = "BUSINESS_INPUT",
    creator: str = "S1",
    classification: DataClassification = DataClassification.INTERNAL,
) -> str:
    """One input artifact of a chosen classification, in the store and registered.

    An input belongs to whoever uploaded it, not to S3 (FD-ART-002): S1 owns
    ``BUSINESS_INPUT`` and S4 owns ``DISCOVERY_RESULT``, so a test that registered them as
    S3's own products would be seeding a state production cannot produce.  Content is staged
    through S3's gateway because that is the form the runtime reads.
    """
    handle = ai_artifacts.stage_json(project_id, artifact_type, document)
    with unit_of_work() as session:
        info = register_artifact(
            handle,
            ArtifactCreate(
                project_id=project_id,
                type=artifact_type,
                media_type="application/json",
                data_classification=classification,
                creator_subsystem=creator,
                source_artifact_ids=[],
                status=ArtifactStatus.DRAFT,
            ),
            session=session,
        )
    return str(info.id)


def audit_types(project_id: str, operator: str | None = None) -> list[str]:
    page = query_audit(AuditQuery(project_id=project_id, operator_name=operator, limit=200))
    return [event.event_type for event in page.items]


# --- scripted model answers ------------------------------------------------------


def add_operation(path: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"op": "add", "path": path, "value": value}


def requirement_item(
    artifact_id: str,
    *,
    key: str = "req-record-weight",
    statement: str = "The clinic records a child's weight at every visit.",
    status: str = "CONFIRMED",
    confidence: float = 1.0,
) -> dict[str, Any]:
    """One entry shaped to S2's ``requirements.items`` schema (AI-INP-005)."""
    return {
        "businessKey": key,
        "statement": statement,
        "status": status,
        "confidence": confidence,
        "priority": "MUST",
        "source": {"artifactId": artifact_id},
    }


def answer(
    *,
    summary: str = "consolidated the inputs into two requirements",
    confidence: float = 0.9,
    operations: list[dict[str, Any]] | None = None,
    questions: list[dict[str, Any]] | None = None,
    decisions: list[dict[str, Any]] | None = None,
    base_version: str = "0.1.0",
) -> dict[str, Any]:
    """One envelope-shaped answer (AI-RT-010)."""
    document: dict[str, Any] = {
        "summary": summary,
        "confidence": confidence,
        "decisions": decisions or [],
        "questions": questions or [],
    }
    if operations is not None:
        document["patch"] = {"baseVersion": base_version, "operations": operations}
    return document


def question(
    local_id: str = "Q1",
    text: str = "Which programme owns the weight record?",
    severity: str = "HIGH",
    affects: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": local_id,
        "text": text,
        "severity": severity,
        "affects": affects or ["req-record-weight"],
    }


def suggested_question(
    local_id: str = "Q1",
    text: str = "Which programme owns the weight record?",
    severity: str = "HIGH",
    affects: list[str] | None = None,
) -> dict[str, Any]:
    """A blocking question in the shape the repair turn is asked to produce."""
    return {
        **question(local_id, text, severity, affects),
        "answerMode": "SINGLE",
        "suggestions": [
            {
                "id": "S1",
                "text": "The child programme owns it; reuse its existing weight data element.",
                "rationale": "Reuses an approved object and avoids a duplicate data element.",
                "recommended": True,
                "changesUid": False,
                "regenerates": [],
            },
            {
                "id": "S2",
                "text": "Create a dedicated weight data element for this follow-up programme.",
                "rationale": "Keeps the follow-up programme independent but creates a new object.",
                "recommended": False,
                "changesUid": False,
                "regenerates": [],
            },
        ],
    }
